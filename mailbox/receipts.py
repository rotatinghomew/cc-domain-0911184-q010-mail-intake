import hashlib
import sqlite3
from pathlib import Path


class Receipts:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'receipts.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS content(digest TEXT PRIMARY KEY, name TEXT, size INTEGER)')

    def receive(self, attachments):
        accepted = []
        with self.db:
            for attachment in attachments:
                digest = hashlib.sha256(attachment['data']).hexdigest()
                if digest != attachment['sha256']:
                    raise ValueError('attachment digest mismatch')
                path = self.root / digest
                if not path.exists():
                    path.write_bytes(attachment['data'])
                self.db.execute('INSERT OR IGNORE INTO content VALUES(?,?,?)',
                                (digest, attachment['name'], len(attachment['data'])))
                accepted.append(digest)
        return accepted

    def rows(self):
        return self.db.execute('SELECT digest,name,size FROM content ORDER BY digest').fetchall()
