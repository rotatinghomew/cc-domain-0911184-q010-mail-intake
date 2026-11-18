import base64
import tempfile
import threading
import time
import unittest
from email.message import EmailMessage
from pathlib import Path

from mailbox.files import load_message, message_meta
from mailbox.imap import Collector, ImapConfig
from mailbox.mime import collect_subtitles, subtitles
from mailbox.receipts import Receipts
from mailbox.service import sample_message, serve


def make_collector(host, port, book):
    return Collector(ImapConfig(host, port, 'collector', 'local-password'), book)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def nested_message():
    inner = EmailMessage()
    inner['From'] = 'delivery@studio.local'
    inner['Date'] = 'Sun, 15 Nov 2026 10:00:00 +0800'
    inner.set_content('内层说明')
    inner.add_attachment('内层字幕'.encode(), maintype='application',
                         subtype='octet-stream', filename='内层.srt')

    message = EmailMessage()
    message['From'] = 'delivery@studio.local'
    message['To'] = 'intake@channel.local'
    message['Date'] = 'Sun, 15 Nov 2026 10:00:00 +0800'
    message['Subject'] = '嵌套交付'
    message['Message-ID'] = '<nested@studio.local>'
    message.set_content('外层说明')
    message.add_attachment(b'PNG', maintype='image', subtype='png',
                           filename='image.png', disposition='inline')
    message.add_attachment('甲版'.encode(), maintype='application',
                           subtype='octet-stream', filename='同名片.srt')
    message.add_attachment('乙版'.encode(), maintype='application',
                           subtype='octet-stream', filename='同名片.srt')
    message.add_attachment(inner)

    # 模拟老客户端：RFC 2047 编码词形式的附件名
    encoded_word = '=?UTF-8?B?' + base64.b64encode('正片.srt'.encode()).decode() + '?='
    legacy = EmailMessage()
    legacy.set_content('正片内容'.encode(), maintype='application', subtype='octet-stream')
    legacy['Content-Disposition'] = f'attachment; filename="{encoded_word}"'
    message.attach(legacy)

    # RFC 2231 参数编码的附件名
    vtt = EmailMessage()
    vtt.set_content('WEBVTT\n\n00:00.000 --> 00:01.000\n曲目', subtype='vtt')
    vtt.add_header('Content-Disposition', 'attachment', filename=('utf-8', '', '曲目.vtt'))
    message.attach(vtt)
    return message.as_bytes()


def partial_message(second_content):
    message = EmailMessage()
    message['From'] = 'delivery@studio.local'
    message['Date'] = 'Sun, 15 Nov 2026 10:00:00 +0800'
    message['Subject'] = '分集交付'
    message['Message-ID'] = '<partial@studio.local>'
    message.set_content('见附件')
    message.add_attachment('第一集内容'.encode(), maintype='application',
                           subtype='octet-stream', filename='第一集.srt')
    message.add_attachment(second_content, maintype='application',
                           subtype='octet-stream', filename='第二集.srt')
    return message.as_bytes()


class CollectorTests(unittest.TestCase):
    def test_poll_receives_once_and_stays_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                state.messages = {
                    1: sample_message(content='第一版'),
                    2: sample_message(message_id='<delivery-b@studio.local>',
                                      content='第二版', name='第二集.srt'),
                }
                collector = make_collector(host, port, book)
                notices = collector.poll()
                self.assertEqual([(n['generation'], n['uid']) for n in notices],
                                 [(700, 1), (700, 2)])
                self.assertEqual({n['kind'] for n in notices}, {'new'})
                self.assertEqual(book.message_row(700, 1)['status'], 'received')
                self.assertEqual(book.message_row(700, 2)['status'], 'received')
                self.assertEqual(collector.poll(), [])
                self.assertTrue(any('EXAMINE' in command for command in state.commands))
                self.assertTrue(any('BODY.PEEK[]' in command for command in state.commands))
                self.assertFalse(any('STORE' in command for command in state.commands))
                self.assertFalse(any(' SELECT ' in command for command in state.commands))

    def test_generation_change_rescans_reused_uids(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                collector = make_collector(host, port, book)
                first = collector.poll()
                self.assertEqual(len(first), 1)
                old_digest = first[0]['digest']
                # 邮箱重建：新代际，UID 从 1 重新开始，内容是补正版
                state.uidvalidity = 701
                state.messages = {1: sample_message(content='补正字幕')}
                second = collector.poll()
                self.assertEqual(len(second), 1)
                notice = second[0]
                self.assertEqual((notice['generation'], notice['uid']), (701, 1))
                self.assertEqual(notice['kind'], 'version')
                self.assertEqual(notice['supersedes'], old_digest)
                # 旧版已交付内容不被覆盖
                self.assertTrue((book.root / old_digest).exists())
                self.assertEqual(len(book.rows()), 2)
                self.assertEqual({row['generation'] for row in book.receptions()}, {700, 701})
                self.assertEqual(book.message_row(700, 1)['status'], 'received')
                self.assertEqual(book.message_row(701, 1)['status'], 'received')

    def test_interrupted_fetch_stays_pending_until_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                state.messages = {
                    1: sample_message(content='甲'),
                    2: sample_message(message_id='<delivery-b@studio.local>', content='乙'),
                }
                state.interrupt_uid = 2
                collector = make_collector(host, port, book)
                notices = collector.poll()
                self.assertEqual([n['uid'] for n in notices], [1])
                row = book.message_row(700, 2)
                self.assertEqual(row['status'], 'pending')
                self.assertFalse(any(r['uid'] == 2 for r in book.receptions()))
                state.interrupt_uid = None
                notices = collector.poll()
                self.assertEqual([n['uid'] for n in notices], [2])
                self.assertEqual(book.message_row(700, 2)['status'], 'received')
                self.assertEqual(collector.poll(), [])

    def test_moved_message_is_unavailable_not_received(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                state.messages = {
                    1: sample_message(content='甲'),
                    2: sample_message(message_id='<delivery-b@studio.local>', content='乙'),
                }
                state.delete_after_search = {2}
                collector = make_collector(host, port, book)
                notices = collector.poll()
                self.assertEqual([n['uid'] for n in notices], [1])
                row = book.message_row(700, 2)
                self.assertEqual(row['status'], 'unavailable')
                self.assertFalse(any(r['uid'] == 2 for r in book.receptions()))
                self.assertEqual(collector.poll(), [])
                self.assertEqual(book.message_row(700, 2)['status'], 'unavailable')

    def test_stuck_pending_is_not_carried_into_new_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                state.messages = {
                    1: sample_message(content='甲'),
                    2: sample_message(message_id='<delivery-b@studio.local>', content='乙'),
                }
                state.interrupt_uid = 2
                collector = make_collector(host, port, book)
                collector.poll()
                self.assertEqual(book.message_row(700, 2)['status'], 'pending')
                # 邮箱重建：旧代际未取完的邮件不能记成已收，新代际重新发现
                state.interrupt_uid = None
                state.uidvalidity = 701
                state.messages = {1: sample_message(content='新邮件')}
                notices = collector.poll()
                self.assertEqual([(n['generation'], n['uid']) for n in notices], [(701, 1)])
                self.assertEqual(book.message_row(700, 2)['status'], 'unavailable')
                self.assertEqual(book.message_row(701, 1)['status'], 'received')

    def test_correction_and_redelivery_are_distinguished_by_content(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                collector = make_collector(host, port, book)
                first = collector.poll()
                original = first[0]['digest']
                self.assertEqual(first[0]['kind'], 'new')
                # 补正：沿用旧 Message-ID，附件内容不同
                state.messages[2] = sample_message(message_id='<delivery-a@studio.local>',
                                                   content='补正字幕')
                correction = collector.poll()
                self.assertEqual(correction[0]['kind'], 'version')
                self.assertEqual(correction[0]['supersedes'], original)
                self.assertEqual(correction[0]['message_id'], '<delivery-a@studio.local>')
                # 真正的重发：换了 Message-ID，附件内容相同
                state.messages[3] = sample_message(message_id='<delivery-c@studio.local>',
                                                   content='第一版字幕')
                redelivery = collector.poll()
                self.assertEqual(redelivery[0]['kind'], 'duplicate')
                self.assertEqual(redelivery[0]['digest'], original)
                # 两个版本各自归档，旧版文件仍在
                self.assertEqual(len(book.rows()), 2)
                self.assertTrue((book.root / original).exists())
                self.assertEqual(len(book.receptions()), 3)

    def test_nested_mime_encoded_names_and_inline_images(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                state.messages = {1: nested_message()}
                collector = make_collector(host, port, book)
                notices = collector.poll()
                names = [n['name'] for n in notices]
                self.assertEqual(sorted(names), sorted(
                    ['同名片.srt', '同名片.srt', '内层.srt', '正片.srt', '曲目.vtt']))
                self.assertNotIn('image.png', names)
                kinds = sorted(n['kind'] for n in notices)
                self.assertEqual(kinds, ['new', 'new', 'new', 'new', 'version'])
                # 同名附件各自留存，按 MIME 序号区分
                same_name = [n for n in notices if n['name'] == '同名片.srt']
                self.assertEqual(len(same_name), 2)
                self.assertNotEqual(same_name[0]['part'], same_name[1]['part'])
                self.assertNotEqual(same_name[0]['digest'], same_name[1]['digest'])
                self.assertEqual(len(book.receptions()), 5)
                self.assertEqual(book.message_row(700, 1)['status'], 'received')

    def test_partial_failure_keeps_verified_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                # 第二集附件字节无法用声明字符集解码，第一集正常
                state.messages = {1: partial_message(b'\xff\xfe\xfd')}
                collector = make_collector(host, port, book)
                notices = collector.poll()
                self.assertEqual([n['name'] for n in notices], ['第一集.srt'])
                row = book.message_row(700, 1)
                self.assertEqual(row['status'], 'pending')
                self.assertIn('第二集.srt', row['detail'])
                self.assertEqual(len(book.receptions()), 1)
                # 修复后重扫：只补发第二集的通知，第一集不重复通知、来历保留
                state.messages[1] = partial_message('第二集内容'.encode())
                notices = collector.poll()
                self.assertEqual([n['name'] for n in notices], ['第二集.srt'])
                self.assertEqual(book.message_row(700, 1)['status'], 'received')
                receptions = book.receptions()
                self.assertEqual(len(receptions), 2)
                self.assertEqual({r['uid'] for r in receptions}, {1})
                self.assertEqual(len(book.rows()), 2)

    def test_run_collects_continuously_until_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Receipts(Path(directory) / 'received')
            with serve() as ((host, port), state):
                collector = make_collector(host, port, book)
                stop = threading.Event()
                notices = []
                thread = threading.Thread(
                    target=collector.run,
                    kwargs={'interval': 0.05, 'stop': stop, 'on_notices': notices.extend},
                    daemon=True)
                thread.start()
                try:
                    self.assertTrue(wait_for(
                        lambda: (book.message_row(700, 1) or {}).get('status') == 'received'))
                    state.messages[2] = sample_message(message_id='<late@studio.local>',
                                                       content='迟到字幕')
                    self.assertTrue(wait_for(
                        lambda: (book.message_row(700, 2) or {}).get('status') == 'received'))
                finally:
                    stop.set()
                    thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual({n['digest'] for n in notices},
                                 {r['digest'] for r in book.receptions()})

    def test_file_import_keeps_working_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = Receipts(root / 'received')
            first = root / 'v1.eml'
            first.write_bytes(sample_message(content='旧版'))
            message, _ = load_message(first)
            accepted = book.receive(subtitles(message), ref=str(first), meta=message_meta(message))
            self.assertEqual(len(accepted), 1)
            second = root / 'v2.eml'
            second.write_bytes(sample_message(content='补正版'))
            message, _ = load_message(second)
            book.receive(subtitles(message), ref=str(second), meta=message_meta(message))
            records = book.receptions(source='file')
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]['kind'], 'new')
            self.assertEqual(records[1]['kind'], 'version')
            self.assertEqual(records[1]['supersedes'], records[0]['digest'])
            # 重复导入同一文件不产生新证据行
            book.receive(subtitles(message), ref=str(second), meta=message_meta(message))
            self.assertEqual(len(book.receptions(source='file')), 2)
            self.assertEqual(len(book.rows()), 2)


if __name__ == '__main__':
    unittest.main()
