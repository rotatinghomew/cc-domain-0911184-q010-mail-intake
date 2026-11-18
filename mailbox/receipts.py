import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS content(digest TEXT PRIMARY KEY, name TEXT, size INTEGER)',
    '''CREATE TABLE IF NOT EXISTS sync_state(
        id INTEGER PRIMARY KEY CHECK(id=1),
        generation INTEGER NOT NULL,
        low_uid INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS messages(
        generation INTEGER NOT NULL,
        uid INTEGER NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        message_id TEXT,
        subject TEXT,
        sender TEXT,
        sent TEXT,
        detail TEXT,
        updated_at TEXT,
        PRIMARY KEY(generation, uid))''',
    '''CREATE TABLE IF NOT EXISTS receptions(
        source TEXT NOT NULL,
        msg_key TEXT NOT NULL,
        part INTEGER NOT NULL,
        generation INTEGER,
        uid INTEGER,
        name TEXT,
        digest TEXT NOT NULL,
        size INTEGER,
        ctype TEXT,
        message_id TEXT,
        subject TEXT,
        sender TEXT,
        sent TEXT,
        kind TEXT NOT NULL,
        supersedes TEXT,
        received_at TEXT NOT NULL,
        PRIMARY KEY(source, msg_key, part))''',
)

MESSAGE_COLUMNS = 'generation,uid,status,attempts,message_id,subject,sender,sent,detail,updated_at'
RECEPTION_COLUMNS = ('source,msg_key,part,generation,uid,name,digest,size,ctype,'
                     'message_id,subject,sender,sent,kind,supersedes,received_at')


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


class Receipts:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'receipts.sqlite3', check_same_thread=False)
        with self.lock, self.db:
            for statement in SCHEMA:
                self.db.execute(statement)

    def close(self):
        with self.lock:
            self.db.close()

    # ---- 内容归档与版本分类 ----

    def _classify(self, name, digest):
        if self.db.execute('SELECT 1 FROM content WHERE digest=?', (digest,)).fetchone():
            return 'duplicate', None
        row = self.db.execute(
            'SELECT digest FROM receptions WHERE name=? AND digest<>?'
            ' ORDER BY received_at DESC, rowid DESC LIMIT 1',
            (name, digest)).fetchone()
        if row:
            return 'version', row[0]
        return 'new', None

    def _archive_and_record(self, source, key, part, name, digest, data, ctype, meta,
                            generation, uid, now):
        # 先按既有台账分类，再把内容入库，否则新内容会被误判为重复交付
        kind, supersedes = self._classify(name, digest)
        path = self.root / digest
        if not path.exists():
            path.write_bytes(data)
        self.db.execute('INSERT OR IGNORE INTO content VALUES(?,?,?)', (digest, name, len(data)))
        cursor = self.db.execute(
            f'INSERT OR IGNORE INTO receptions({RECEPTION_COLUMNS})'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (source, key, part, generation, uid, name, digest, len(data), ctype,
             meta.get('message_id'), meta.get('subject'), meta.get('sender'), meta.get('sent'),
             kind, supersedes, now))
        if not cursor.rowcount:
            return None
        return {'source': source, 'generation': generation, 'uid': uid, 'part': part,
                'name': name, 'digest': digest, 'size': len(data), 'kind': kind,
                'supersedes': supersedes, 'message_id': meta.get('message_id'),
                'subject': meta.get('subject'), 'sender': meta.get('sender'),
                'sent': meta.get('sent'), 'received_at': now}

    # ---- 邮件文件导入（原有入口，保持可用） ----

    def receive(self, attachments, source='file', ref=None, meta=None):
        meta = meta or {}
        accepted = []
        now = _utcnow()
        with self.lock, self.db:
            for position, attachment in enumerate(attachments):
                digest = hashlib.sha256(attachment['data']).hexdigest()
                if digest != attachment['sha256']:
                    raise ValueError('attachment digest mismatch')
                part = attachment.get('part', position)
                key = ref if ref is not None else f'{source}:{digest}:{part}'
                self._archive_and_record(source, key, part, attachment['name'], digest,
                                         attachment['data'], attachment.get('ctype'),
                                         meta, None, None, now)
                accepted.append(digest)
        return accepted

    # ---- IMAP 代际与发现状态 ----

    def begin_generation(self, uidvalidity):
        now = _utcnow()
        with self.lock, self.db:
            row = self.db.execute('SELECT generation FROM sync_state WHERE id=1').fetchone()
            if row is None:
                self.db.execute('INSERT INTO sync_state(id,generation,low_uid) VALUES(1,?,1)',
                                (uidvalidity,))
            elif row[0] != uidvalidity:
                self.db.execute(
                    "UPDATE messages SET status='unavailable', detail=?, updated_at=?"
                    " WHERE status='pending' AND generation<>?",
                    (f'mailbox generation changed to {uidvalidity} before full reception',
                     now, uidvalidity))
                self.db.execute('UPDATE sync_state SET generation=?, low_uid=1 WHERE id=1',
                                (uidvalidity,))
        return uidvalidity

    def low_water(self):
        with self.lock:
            row = self.db.execute('SELECT low_uid FROM sync_state WHERE id=1').fetchone()
            return row[0] if row else 1

    def record_discovered(self, generation, uids):
        now = _utcnow()
        with self.lock, self.db:
            for uid in uids:
                self.db.execute(
                    "INSERT OR IGNORE INTO messages(generation,uid,status,attempts,updated_at)"
                    " VALUES(?,?,'pending',0,?)",
                    (generation, uid, now))
            if uids:
                self.db.execute('UPDATE sync_state SET low_uid=MAX(low_uid,?) WHERE id=1',
                                (max(uids) + 1,))

    def pending_uids(self, generation):
        with self.lock:
            return [row[0] for row in self.db.execute(
                "SELECT uid FROM messages WHERE generation=? AND status='pending' ORDER BY uid",
                (generation,))]

    def note_attempt(self, generation, uid):
        now = _utcnow()
        with self.lock, self.db:
            self.db.execute(
                'UPDATE messages SET attempts=attempts+1, updated_at=?'
                ' WHERE generation=? AND uid=?',
                (now, generation, uid))

    def mark_unavailable(self, generation, uid, detail):
        now = _utcnow()
        with self.lock, self.db:
            self.db.execute(
                "UPDATE messages SET status='unavailable', detail=?, updated_at=?"
                " WHERE generation=? AND uid=? AND status='pending'",
                (detail, now, generation, uid))

    def record_error(self, generation, uid, detail):
        now = _utcnow()
        with self.lock, self.db:
            self.db.execute(
                "UPDATE messages SET detail=?, updated_at=?"
                " WHERE generation=? AND uid=? AND status='pending'",
                (detail, now, generation, uid))

    # ---- IMAP 邮件接收与证据 ----

    def record_message(self, generation, uid, meta, parts, errors):
        notices = []
        failures = list(errors)
        now = _utcnow()
        key = f'{generation}:{uid}'
        with self.lock, self.db:
            self.db.execute(
                'INSERT INTO messages(generation,uid,status,attempts,message_id,subject,sender,sent,updated_at)'
                ' VALUES(?,?,?,?,?,?,?,?,?)'
                ' ON CONFLICT(generation,uid) DO UPDATE SET message_id=excluded.message_id,'
                ' subject=excluded.subject, sender=excluded.sender, sent=excluded.sent,'
                ' updated_at=excluded.updated_at',
                (generation, uid, 'pending', 0, meta.get('message_id'), meta.get('subject'),
                 meta.get('sender'), meta.get('sent'), now))
            for part in parts:
                digest = hashlib.sha256(part['data']).hexdigest()
                if digest != part['sha256']:
                    failures.append({'part': part['part'], 'name': part['name'],
                                     'error': 'attachment digest mismatch'})
                    continue
                notice = self._archive_and_record('imap', key, part['part'], part['name'],
                                                  digest, part['data'], part.get('ctype'),
                                                  meta, generation, uid, now)
                if notice:
                    notices.append(notice)
            if failures:
                self.db.execute(
                    "UPDATE messages SET status='pending', detail=?, updated_at=?"
                    " WHERE generation=? AND uid=? AND status='pending'",
                    (json.dumps(failures, ensure_ascii=False), now, generation, uid))
            else:
                self.db.execute(
                    "UPDATE messages SET status='received', detail=NULL, updated_at=?"
                    " WHERE generation=? AND uid=?",
                    (now, generation, uid))
        return notices

    # ---- 查询 ----

    def rows(self):
        with self.lock:
            return self.db.execute('SELECT digest,name,size FROM content ORDER BY digest').fetchall()

    def message_row(self, generation, uid):
        with self.lock:
            row = self.db.execute(
                f'SELECT {MESSAGE_COLUMNS} FROM messages WHERE generation=? AND uid=?',
                (generation, uid)).fetchone()
        if row is None:
            return None
        return dict(zip(MESSAGE_COLUMNS.split(','), row))

    def receptions(self, source=None):
        query = f'SELECT {RECEPTION_COLUMNS} FROM receptions'
        args = ()
        if source is not None:
            query += ' WHERE source=?'
            args = (source,)
        query += ' ORDER BY rowid'
        with self.lock:
            rows = self.db.execute(query, args).fetchall()
        return [dict(zip(RECEPTION_COLUMNS.split(','), row)) for row in rows]
