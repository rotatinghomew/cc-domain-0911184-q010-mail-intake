from email import policy
from email.parser import BytesParser
from pathlib import Path


def parse_message(data):
    message = BytesParser(policy=policy.default).parsebytes(data)
    if not message.get('From') or not message.get('Date'):
        raise ValueError('mail sender and date are required')
    return message


def message_meta(message):
    return {
        'message_id': str(message.get('Message-ID') or ''),
        'subject': str(message.get('Subject') or ''),
        'sender': str(message.get('From') or ''),
        'sent': str(message.get('Date') or ''),
    }


def load_message(path):
    data = Path(path).read_bytes()
    return parse_message(data), data
