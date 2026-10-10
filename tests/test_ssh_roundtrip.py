"""Loopback SSH integration test: no external VPS is used."""
import asyncio
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import paramiko

from app import db, settings, ssh_credentials
from app.collector import Monitor, SCRIPT, parse_output


class PasswordServer(paramiko.ServerInterface):
    def __init__(self):
        self.exec_ready = threading.Event()

    def get_allowed_auths(self, username):
        return 'password'

    def check_auth_password(self, username, password):
        if username == 'root' and password == 'local-test-only-password':
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        if command != b'sh -s':
            return False
        self.exec_ready.set()
        return True


def test_password_registration_then_monitored_refresh_with_local_ssh(tmp_path, monkeypatch):
    """End-to-end password SSH, automatic TOFU pin, and 2nd monitor poll."""
    monkeypatch.setattr(settings, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(settings, 'KNOWN_HOSTS', tmp_path / 'data' / '.ssh' / 'known_hosts')
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'data' / 'fleet.db')
    monkeypatch.delenv('DW_SSH_REQUIRE_VERIFIED_HOSTS', raising=False)
    db.init_db()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(2)
    listener.settimeout(10)
    port = listener.getsockname()[1]
    key = paramiko.RSAKey.generate(2048)
    errors = []

    def serve():
        try:
            for _ in range(2):
                sock, _ = listener.accept()
                transport = paramiko.Transport(sock)
                try:
                    transport.add_server_key(key)
                    handler = PasswordServer()
                    transport.start_server(server=handler)
                    channel = transport.accept(8)
                    assert channel is not None
                    assert handler.exec_ready.wait(6)
                    channel.settimeout(6)
                    data = bytearray()
                    while True:
                        piece = channel.recv(4096)
                        if not piece:
                            break
                        data.extend(piece)
                    assert b'__CPU__' in data
                    sampled = subprocess.run(['sh', '-s'], input=bytes(data),
                                             capture_output=True, check=True, timeout=5)
                    channel.sendall(sampled.stdout)
                    channel.send_exit_status(0)
                    channel.shutdown_write()
                    channel.exit_status_ready()
                finally:
                    transport.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    server = {'name': 'Test VPS', 'host': '127.0.0.1', 'port': port,
              'ssh_user': 'root', 'group_name': 'localtest'}
    try:
        first = ssh_credentials.collect_password_metrics(
            server, 'local-test-only-password', SCRIPT)
        assert parse_output(first.decode())['mem']['MemTotal'] > 0
        known = settings.KNOWN_HOSTS.read_text()
        assert '[127.0.0.1]:' + str(port) in known
        assert 'ssh-rsa ' in known
        encrypted = ssh_credentials.encrypt_password('local-test-only-password')
        server_id = db.add_password_server(server, encrypted)
        record = db.get_server(server_id)
        monitor = Monitor()
        asyncio.run(monitor.poll(record))
        assert monitor.snapshots[server_id]['online'], monitor.snapshots[server_id]['error']
        assert monitor.snapshots[server_id]['disk_total'] > 0
        assert monitor.snapshots[server_id]['memory_total'] > 0
        assert monitor.snapshots[server_id]['network_interfaces']
    finally:
        worker.join(timeout=12)
        listener.close()
    assert not errors, errors
    assert not worker.is_alive()
