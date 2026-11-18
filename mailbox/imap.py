import imaplib
import ssl
import threading
from dataclasses import dataclass

from mailbox.files import message_meta, parse_message
from mailbox.mime import collect_subtitles


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    user: str
    password: str
    mailbox: str = 'INBOX'
    use_ssl: bool = False
    timeout: float = 30.0


class Collector:
    """只读 IMAP 采集器。

    每轮 poll 重新 EXAMINE 并核对 UIDVALIDITY（邮箱代际）：代际不变时从
    低水位继续发现，代际变化时从头重扫。取正文一律 BODY.PEEK[]，不置
    已读、不删除；发现但未完整取回的邮件保持 pending，留待下轮补齐。
    """

    def __init__(self, config, receipts):
        self.config = config
        self.receipts = receipts

    def _connect(self):
        if self.config.use_ssl:
            client = imaplib.IMAP4_SSL(self.config.host, self.config.port,
                                       ssl_context=ssl.create_default_context(),
                                       timeout=self.config.timeout)
        else:
            client = imaplib.IMAP4(self.config.host, self.config.port,
                                   timeout=self.config.timeout)
        client.login(self.config.user, self.config.password)
        return client

    def poll(self):
        notices = []
        client = self._connect()
        try:
            if client.select(self.config.mailbox, readonly=True)[0] != 'OK':
                raise RuntimeError(f'cannot examine {self.config.mailbox}')
            response = client.response('UIDVALIDITY')[1]
            if not response or response[0] is None:
                raise RuntimeError('server did not report UIDVALIDITY')
            generation = self.receipts.begin_generation(int(response[0]))
            typ, data = client.uid('SEARCH', None, f'UID {self.receipts.low_water()}:*')
            if typ != 'OK':
                raise RuntimeError(f'UID SEARCH failed: {typ}')
            uids = []
            for chunk in data or []:
                if chunk:
                    uids.extend(int(item) for item in chunk.split())
            self.receipts.record_discovered(generation, uids)
            for uid in self.receipts.pending_uids(generation):
                self.receipts.note_attempt(generation, uid)
                try:
                    typ, data = client.uid('FETCH', str(uid), '(BODY.PEEK[])')
                except (imaplib.IMAP4.abort, OSError):
                    break
                except imaplib.IMAP4.error as exc:
                    self.receipts.record_error(generation, uid, f'fetch error: {exc}')
                    continue
                if typ != 'OK':
                    self.receipts.mark_unavailable(generation, uid, f'server refused fetch: {typ}')
                    continue
                raw = next((item[1] for item in data if isinstance(item, tuple)), None) if data else None
                if raw is None:
                    self.receipts.record_error(generation, uid, 'server returned no message data')
                    continue
                try:
                    message = parse_message(raw)
                    parts, errors = collect_subtitles(message)
                except Exception as exc:
                    self.receipts.record_error(generation, uid, f'{type(exc).__name__}: {exc}')
                    continue
                notices.extend(
                    self.receipts.record_message(generation, uid, message_meta(message),
                                                 parts, errors))
            return notices
        finally:
            try:
                client.logout()
            except Exception:
                try:
                    client.shutdown()
                except Exception:
                    pass

    def run(self, interval=60.0, stop=None, on_notices=None, on_error=None):
        stop = stop if stop is not None else threading.Event()
        while not stop.is_set():
            try:
                notices = self.poll()
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)
            else:
                if notices and on_notices is not None:
                    on_notices(notices)
            stop.wait(interval)
