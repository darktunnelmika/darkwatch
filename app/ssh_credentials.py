"""SSH password authentication, encrypted credentials and verified server identity."""
import base64
import hashlib
import os

import paramiko
from cryptography.fernet import Fernet

from . import settings


class UntrustedHostKey(Exception):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        super().__init__('SSH server fingerprint must be verified')


def fingerprint_of(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


class ConfirmHostPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, confirmed=None):
        self.confirmed = confirmed
        self.pending = None

    def missing_host_key(self, client, hostname, key):
        fingerprint = fingerprint_of(key)
        # First use: remember the presented key in memory, authenticate and
        # successfully read metrics, THEN pin it in known_hosts. Subsequent
        # connections reject a changed key via Paramiko's host-key check.
        # No extra popup/second submission for the user's own servers.
        strict = os.environ.get('DW_SSH_REQUIRE_VERIFIED_HOSTS') == '1'
        if strict and self.confirmed != fingerprint:
            raise UntrustedHostKey(fingerprint)
        if self.confirmed and self.confirmed != fingerprint:
            raise UntrustedHostKey(fingerprint)
        self.pending = (hostname, key)


def secret_key():
    path = settings.DATA_DIR / '.ssh' / 'credential.key'
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def encrypt_password(password):
    return secret_key().encrypt(password.encode('utf-8')).decode('ascii')


def decrypt_password(encrypted):
    return secret_key().decrypt(encrypted.encode('ascii')).decode('utf-8')


def connect_password(server, password, confirmed=None):
    client = paramiko.SSHClient()
    known = settings.KNOWN_HOSTS
    if known.is_file():
        client.load_host_keys(str(known))
    policy = ConfirmHostPolicy(confirmed)
    client.set_missing_host_key_policy(policy)
    try:
        client.connect(
            hostname=server['host'], port=server['port'], username=server['ssh_user'],
            password=password, look_for_keys=False, allow_agent=False, timeout=7,
            auth_timeout=7, banner_timeout=7)
    except Exception:
        client.close()
        raise
    return client, policy


def save_verified_host_key(policy):
    if not policy.pending:
        return
    hostname, key = policy.pending
    target = settings.KNOWN_HOSTS
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = f"{hostname} {key.get_name()} {key.get_base64()}\n".encode('ascii')
    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def collect_password_metrics(server, password, script, confirmed=None):
    client, policy = connect_password(server, password, confirmed)
    try:
        stdin, stdout, stderr = client.exec_command('sh -s', timeout=7)
        stdin.write(script)
        stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read(2 * 1024 * 1024 + 1)
        if len(output) > 2 * 1024 * 1024:
            raise ValueError('Remote metrics output too large')
        if stdout.channel.recv_exit_status() != 0:
            raise ValueError('Remote metrics command failed')
        if b'__END__' not in output:
            raise ValueError('Incomplete metrics response')
        # Only pin an unknown key after successful login AND valid command output.\n        save_verified_host_key(policy)
        return output
    finally:
        client.close()


def open_password_terminal(server, password):
    client, _ = connect_password(server, password)
    try:
        channel = client.invoke_shell(term='xterm', width=100, height=28)
        channel.settimeout(0.4)
        return client, channel
    except Exception:
        client.close()
        raise
