import sqlite3
import time
from contextlib import contextmanager
from .settings import DB_PATH

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()

def init_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 22,
            ssh_user TEXT NOT NULL DEFAULT 'root', group_name TEXT NOT NULL DEFAULT 'ایران',
            created_at INTEGER NOT NULL,
            UNIQUE(host, port, ssh_user)
        );
        CREATE TABLE IF NOT EXISTS server_credentials (
            server_id INTEGER PRIMARY KEY,
            encrypted_password TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL
        );
        ''')

def servers():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM servers ORDER BY id')]

def get_server(server_id):
    with connect() as db:
        row = db.execute('SELECT * FROM servers WHERE id = ?', (server_id,)).fetchone()
        return dict(row) if row else None

def add_server(d):
    with connect() as db:
        c = db.execute('''INSERT INTO servers (name,host,port,ssh_user,group_name,created_at)
            VALUES (?,?,?,?,?,?)''', (d['name'], d['host'], d['port'], d['ssh_user'], d['group_name'], int(time.time())))
        return c.lastrowid

def get_encrypted_password(server_id):
    with connect() as conn:
        row = conn.execute('SELECT encrypted_password FROM server_credentials WHERE server_id=?',
                           (server_id,)).fetchone()
        return row['encrypted_password'] if row else None


def add_password_server(d, encrypted_password):
    with connect() as conn:
        cur = conn.execute('''INSERT INTO servers (name,host,port,ssh_user,group_name,created_at)
            VALUES (?,?,?,?,?,?)''', (d['name'], d['host'], d['port'], d['ssh_user'],
                                     d['group_name'], int(time.time())))
        sid = cur.lastrowid
        conn.execute('INSERT INTO server_credentials (server_id,encrypted_password) VALUES (?,?)',
                     (sid, encrypted_password))
        return sid


def set_password(server_id, encrypted_password):
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM servers WHERE id=?', (server_id,)).fetchone():
            return False
        conn.execute('INSERT OR REPLACE INTO server_credentials (server_id,encrypted_password) VALUES (?,?)',
                     (server_id, encrypted_password))
        return True


def remove_server(server_id):
    with connect() as db:
        db.execute('DELETE FROM server_credentials WHERE server_id=?', (server_id,))
        c = db.execute('DELETE FROM servers WHERE id=?', (server_id,))
        return c.rowcount
