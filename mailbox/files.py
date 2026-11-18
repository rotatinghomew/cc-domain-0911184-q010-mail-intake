from email import policy
from email.parser import BytesParser
from pathlib import Path


def load_message(path):
    data = Path(path).read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(data)
    if not message.get('From') or not message.get('Date'):
        raise ValueError('mail sender and date are required')
    return message, data
