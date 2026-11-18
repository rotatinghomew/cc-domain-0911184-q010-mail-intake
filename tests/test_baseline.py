import imaplib
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from mailbox.files import load_message
from mailbox.mime import subtitles
from mailbox.receipts import Receipts
from mailbox.service import sample_message, serve


class MailTests(unittest.TestCase):
    def test_file_import_and_durable_content_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mail.eml'
            path.write_bytes(sample_message())
            message, raw = load_message(path)
            self.assertEqual(str(message['Subject']), '分集字幕交付')
            root = Path(directory) / 'received'
            book = Receipts(root)
            book.receive(subtitles(message))
            book.receive(subtitles(message))
            self.assertEqual(len(Receipts(root).rows()), 1)

    def test_imap_peek_and_uidvalidity(self):
        with serve() as ((host, port), state):
            client = imaplib.IMAP4(host, port, timeout=3)
            try:
                self.assertEqual(client.login('collector', 'local-password')[0], 'OK')
                self.assertEqual(client.select('INBOX', readonly=True)[0], 'OK')
                self.assertEqual(client.response('UIDVALIDITY')[1], [b'700'])
                status, ids = client.uid('SEARCH', None, 'UID 1:*')
                self.assertEqual(ids, [b'1'])
                status, response = client.uid('FETCH', '1', '(BODY.PEEK[])')
                body = next(item[1] for item in response if isinstance(item, tuple))
                self.assertEqual(body, state.messages[1])
            finally:
                client.logout()
            self.assertTrue(any('EXAMINE' in command for command in state.commands))
            self.assertFalse(any('STORE' in command for command in state.commands))

    def test_mailbox_rebuild_reuses_uid_with_new_generation(self):
        with serve() as ((host, port), state):
            state.uidvalidity = 701
            state.messages = {1: sample_message(content='补正字幕')}
            client = imaplib.IMAP4(host, port, timeout=3)
            client.login('collector', 'local-password')
            client.select('INBOX', readonly=True)
            self.assertEqual(client.response('UIDVALIDITY')[1], [b'701'])
            self.assertEqual(client.uid('SEARCH', None, 'UID 1:*')[1], [b'1'])
            client.logout()

    def test_interrupted_literal_raises_instead_of_becoming_mail(self):
        with serve() as ((host, port), state):
            state.interrupt_uid = 1
            client = imaplib.IMAP4(host, port, timeout=3)
            client.login('collector', 'local-password')
            client.select('INBOX', readonly=True)
            with self.assertRaises(imaplib.IMAP4.abort):
                client.uid('FETCH', '1', '(BODY.PEEK[])')
            client.shutdown()

    def test_inline_images_are_not_subtitles(self):
        message = BytesParser(policy=policy.default).parsebytes(sample_message())
        message.add_attachment(b'image', maintype='image', subtype='png', filename='image.png', disposition='inline')
        self.assertEqual([item['name'] for item in subtitles(message)], ['正片.srt'])

    def test_message_id_is_not_an_attachment_digest(self):
        first = BytesParser(policy=policy.default).parsebytes(sample_message(content='甲'))
        second = BytesParser(policy=policy.default).parsebytes(sample_message(content='乙'))
        self.assertEqual(first['Message-ID'], second['Message-ID'])
        self.assertNotEqual(subtitles(first)[0]['sha256'], subtitles(second)[0]['sha256'])
