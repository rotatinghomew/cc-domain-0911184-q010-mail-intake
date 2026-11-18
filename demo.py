import json
import tempfile
from pathlib import Path

from mailbox.files import load_message, message_meta
from mailbox.imap import Collector, ImapConfig
from mailbox.mime import subtitles
from mailbox.receipts import Receipts
from mailbox.service import sample_message, serve


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        book = Receipts(root / 'received')

        # 原有邮件文件导入路径保持可用，并记录文件来源证据
        path = root / 'delivery.eml'
        path.write_bytes(sample_message(message_id='<file-a@studio.local>', content='文件字幕'))
        message, _ = load_message(path)
        accepted = book.receive(subtitles(message), ref=str(path), meta=message_meta(message))

        # 从项目 IMAP 邮箱只读采集：第二封沿用旧 Message-ID 但内容不同，属于补正
        with serve() as ((host, port), state):
            state.messages = {
                1: sample_message(message_id='<delivery-a@studio.local>', content='第一版字幕'),
                2: sample_message(message_id='<delivery-a@studio.local>', content='补正字幕'),
            }
            collector = Collector(ImapConfig(host, port, 'collector', 'local-password'), book)
            notices = collector.poll()
            rescan = collector.poll()  # 重新扫描补齐漏取，但不重复通知

        print(json.dumps({
            'file_import_accepted': accepted,
            'imap_notices': notices,
            'rescan_notices': rescan,
            'content': book.rows(),
            'receptions': book.receptions(),
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
