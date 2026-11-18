import json
import tempfile
from pathlib import Path
from mailbox.files import load_message
from mailbox.mime import subtitles
from mailbox.receipts import Receipts
from mailbox.service import sample_message


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / 'delivery.eml'
        path.write_bytes(sample_message())
        message, raw = load_message(path)
        receipts = Receipts(root / 'received')
        accepted = receipts.receive(subtitles(message))
        print(json.dumps({'subject': str(message['Subject']), 'accepted': accepted, 'content': receipts.rows()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
