import base64
import hashlib
import hmac
import secrets
import time
from . import db
from .settings import SESSION_SECONDS


def new_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return 'scrypt$' + base64.urlsafe_b64encode(salt).decode() + '$' + base64.urlsafe_b64encode(digest).decode()


def check_hash(password, expected):
    try:
        alg, salt64, digest64 = expected.split('$')
        if alg != 'scrypt': return False
        salt = base64.urlsafe_b64decode(salt64)
        digest = base64.urlsafe_b64decode(digest64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session():
    token, csrf = secrets.token_urlsafe(36), secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute('DELETE FROM sessions WHERE expires < ?', (int(time.time()),))
        conn.execute('INSERT INTO sessions VALUES (?,?,?)', (hash_token(token), csrf, int(time.time()) + SESSION_SECONDS))
    return token, csrf


def get_session(token):
    if not token or len(token) > 256: return None
    with db.connect() as conn:
        row = conn.execute('SELECT csrf,expires FROM sessions WHERE token_hash=?', (hash_token(token),)).fetchone()
    return dict(row) if row and row['expires'] > time.time() else None


def delete_session(token):
    if token:
        with db.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (hash_token(token),))
