import os
from pathlib import Path

DATA_DIR = Path(os.getenv('DW_DATA_DIR', str(Path.home() / '.darkwatch'))).expanduser()
DB_PATH = Path(os.getenv('DW_DB', str(DATA_DIR / 'darkwatch.db'))).expanduser()
SSH_KEY = Path(os.getenv('DW_SSH_KEY', str(DATA_DIR / '.ssh' / 'id_ed25519'))).expanduser()
KNOWN_HOSTS = Path(os.getenv('DW_KNOWN_HOSTS', str(DATA_DIR / '.ssh' / 'known_hosts'))).expanduser()
ADMIN_USER = os.getenv('DW_USER', 'admin')
PASSWORD_HASH = os.getenv('DW_PASSWORD_HASH', '')
SESSION_SECURE = os.getenv('DW_SESSION_SECURE', '1') == '1'
PUBLIC_ORIGIN = os.getenv('DW_PUBLIC_ORIGIN', '').rstrip('/')
POLL_SECONDS = max(2, min(30, int(os.getenv('DW_POLL_SECONDS', '3'))))
SESSION_SECONDS = 12 * 3600
