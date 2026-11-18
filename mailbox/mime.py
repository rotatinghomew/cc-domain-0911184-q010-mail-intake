import hashlib


def subtitles(message):
    found = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() != 'attachment':
            continue
        name = part.get_filename() or ''
        if not name.lower().endswith(('.srt', '.vtt')):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            raise ValueError('attachment cannot be decoded')
        payload.decode(part.get_content_charset() or 'utf-8')
        found.append({'name': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'data': payload})
    return found
