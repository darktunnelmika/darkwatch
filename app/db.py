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

def remove_server(server_id):
    with connect() as db:
        c = db.execute('DELETE FROM servers WHERE id=?', (server_id,))
        return c.rowcount
