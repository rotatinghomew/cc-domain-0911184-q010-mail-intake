import hashlib
from email.header import decode_header, make_header

SUBTITLE_SUFFIXES = ('.srt', '.vtt')


def _filename(part):
    name = part.get_filename()
    if name is None:
        return None
    if '=?' in name:
        try:
            name = str(make_header(decode_header(name)))
        except Exception:
            pass
    return name


def _subtitle_parts(message):
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        if disposition == 'inline':
            continue
        if disposition not in (None, 'attachment'):
            continue
        name = _filename(part)
        if not name or not name.lower().endswith(SUBTITLE_SUFFIXES):
            continue
        yield index, part, name


def _extract(index, part, name):
    payload = part.get_payload(decode=True)
    if payload is None:
        raise ValueError('attachment cannot be decoded')
    payload.decode(part.get_content_charset() or 'utf-8')
    return {
        'part': index,
        'name': name,
        'ctype': part.get_content_type(),
        'sha256': hashlib.sha256(payload).hexdigest(),
        'data': payload,
    }


def subtitles(message):
    return [_extract(index, part, name) for index, part, name in _subtitle_parts(message)]


def collect_subtitles(message):
    found = []
    errors = []
    for index, part, name in _subtitle_parts(message):
        try:
            found.append(_extract(index, part, name))
        except Exception as exc:
            errors.append({'part': index, 'name': name, 'error': f'{type(exc).__name__}: {exc}'})
    return found, errors
