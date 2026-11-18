import re
import socketserver
import threading
from contextlib import contextmanager
from email.message import EmailMessage


def sample_message(message_id='<delivery-a@studio.local>', content='第一版字幕', name='正片.srt'):
    message = EmailMessage()
    message['From'] = 'delivery@studio.local'
    message['To'] = 'intake@channel.local'
    message['Date'] = 'Sun, 15 Nov 2026 10:00:00 +0800'
    message['Subject'] = '分集字幕交付'
    message['Message-ID'] = message_id
    message.set_content('请查收字幕。')
    message.add_attachment(content.encode(), maintype='application', subtype='octet-stream', filename=name)
    return message.as_bytes()


class MailState:
    def __init__(self):
        self.uidvalidity = 700
        self.messages = {1: sample_message()}
        self.deleted = set()
        self.interrupt_uid = None
        self.delete_after_search = set()
        self.commands = []
        self.lock = threading.RLock()


@contextmanager
def serve(state=None):
    state = state or MailState()

    class Handler(socketserver.StreamRequestHandler):
        def line(self, value):
            self.wfile.write(value.encode() + b'\r\n')
            self.wfile.flush()

        def handle(self):
            self.line('* OK local IMAP4rev1 delivery service')
            authenticated = False
            while True:
                raw = self.rfile.readline(65536)
                if not raw:
                    return
                command = raw.decode().strip()
                state.commands.append(command)
                pieces = command.split(' ', 2)
                if len(pieces) < 2:
                    return
                tag, operation = pieces[:2]
                argument = pieces[2] if len(pieces) > 2 else ''
                operation = operation.upper()
                if operation == 'CAPABILITY':
                    self.line('* CAPABILITY IMAP4rev1 UIDPLUS')
                elif operation == 'LOGIN':
                    if argument.replace('"', '') != 'collector local-password':
                        self.line(tag + ' NO invalid credentials')
                        continue
                    authenticated = True
                elif operation == 'LOGOUT':
                    self.line('* BYE closing connection')
                    self.line(tag + ' OK LOGOUT completed')
                    return
                elif not authenticated:
                    self.line(tag + ' NO authenticate first')
                    continue
                elif operation in ('SELECT', 'EXAMINE'):
                    self.line(f'* {len(state.messages)} EXISTS')
                    self.line('* FLAGS (\\Seen \\Deleted)')
                    self.line(f'* OK [UIDVALIDITY {state.uidvalidity}] stable mailbox identity')
                    self.line(f'* OK [UIDNEXT {max(state.messages, default=0) + 1}] predicted next UID')
                    self.line(tag + f' OK [{"READ-ONLY" if operation == "EXAMINE" else "READ-WRITE"}] selected')
                    continue
                elif operation == 'UID':
                    action, _, rest = argument.partition(' ')
                    if action.upper() == 'SEARCH':
                        criterion = re.search(r'UID\s+(\d+):\*', rest, re.I)
                        minimum = int(criterion.group(1)) if criterion else 1
                        identifiers = [str(uid) for uid in sorted(state.messages) if uid >= minimum and uid not in state.deleted]
                        self.line('* SEARCH ' + ' '.join(identifiers))
                        gone = [int(uid) for uid in identifiers if int(uid) in state.delete_after_search]
                        state.deleted.update(gone)
                    elif action.upper() == 'FETCH':
                        uid = int(rest.split()[0])
                        if uid not in state.messages or uid in state.deleted:
                            self.line(tag + ' NO message unavailable')
                            continue
                        data = state.messages[uid]
                        self.line(f'* 1 FETCH (UID {uid} BODY[] {{{len(data)}}}')
                        if state.interrupt_uid == uid:
                            self.wfile.write(data[:len(data) // 2])
                            self.wfile.flush()
                            return
                        self.wfile.write(data)
                        self.wfile.write(b')\r\n')
                        self.wfile.flush()
                    else:
                        self.line(tag + ' NO unsupported UID operation')
                        continue
                elif operation not in ('NOOP', 'CLOSE', 'UNSELECT'):
                    self.line(tag + ' BAD unsupported command')
                    continue
                self.line(tag + ' OK ' + operation + ' completed')

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ('127.0.0.1', server.server_address[1]), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
