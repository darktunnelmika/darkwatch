import subprocess

import pytest
from fastapi.testclient import TestClient

from app import auth, db, settings
from app.cli import valid_password
from app.collector import SCRIPT, parse_output, rate_metrics
from app.main import app

PASSWORD = 'TestPassword-Very-Strong-123'

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'app.db')
    monkeypatch.setattr(settings, 'PASSWORD_HASH', auth.new_hash(PASSWORD))
    monkeypatch.setattr(settings, 'SESSION_SECURE', False)
    with TestClient(app) as c:
        yield c


def login(client):
    response = client.post('/api/login', json={'username': 'admin', 'password': PASSWORD})
    assert response.status_code == 200
    return response.json()['csrf']


def test_auth_and_csrf(client):
    assert client.get('/api/state').status_code == 401
    assert client.post('/api/login', json={'username':'admin','password':'wrong'}).status_code == 401
    csrf = login(client)
    assert client.get('/api/state').status_code == 200
    entry = {'name':'Tehran node','host':'192.0.2.100','ssh_user':'root','port':2222,'group_name':'ایران'}
    assert client.post('/api/servers', json=entry).status_code == 403
    r = client.post('/api/servers', json=entry, headers={'X-CSRF-Token':csrf})
    assert r.status_code == 201
    assert client.post('/api/servers', json=entry, headers={'X-CSRF-Token':csrf}).status_code == 409
    assert len(client.get('/api/servers').json()) == 1
    assert client.delete('/api/servers/0', headers={'X-CSRF-Token':csrf}).status_code == 404
    assert client.delete('/api/servers/1', headers={'X-CSRF-Token':csrf}).status_code == 200
    assert client.get('/api/servers').json() == []
    assert client.post('/api/logout', headers={'X-CSRF-Token':csrf}, json={}).status_code == 200
    assert client.get('/api/state').status_code == 401


def test_host_user_validation(client):
    csrf = login(client)
    headers={'X-CSRF-Token':csrf}
    base={'name':'safe','host':'example.net','port':22,'ssh_user':'root','group_name':'ایران'}
    for host in ['-ProxyCommand=evil','a b','example.net;rm -rf /','https://example.com']:
        assert client.post('/api/servers', headers=headers, json={**base,'host':host}).status_code == 422
    assert client.post('/api/servers', headers=headers, json={**base,'ssh_user':'root;evil'}).status_code == 422
    assert client.post('/api/servers', headers=headers, json={**base,'host':'2001:db8::7'}).status_code == 201


def test_origin_and_assets(client):
    csrf=login(client)
    entry={'name':'Test','host':'192.0.2.10','port':22,'ssh_user':'root','group_name':'ایران'}
    r=client.post('/api/servers',headers={'X-CSRF-Token':csrf,'Origin':'https://malicious.example'},json=entry)
    assert r.status_code == 403
    assert 'Content-Security-Policy' in client.get('/').headers
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/static/vendor/xterm.js').status_code == 200


def test_live_proc_sampler():
    raw = subprocess.run(['sh','-s'], input=SCRIPT, capture_output=True, text=True, check=True).stdout
    parsed = parse_output(raw)
    assert parsed['cpu']['cpu'][1] > 0
    assert parsed['mem']['MemTotal'] > 0
    assert parsed['disk']['total'] > 0
    assert parsed['net']
    first = rate_metrics(parsed, None, 100)
    assert first['cpu'] is None
    assert first['memory_total'] > 0
    now = {**parsed, 'cpu':dict(parsed['cpu']), 'net': dict(parsed['net'])}
    before = parsed['cpu']['cpu']
    now['cpu']['cpu'] = (before[0]+25, before[1]+100)
    nic = parsed['primary']
    oldrx,oldtx = parsed['net'][nic]
    now['net'][nic] = (oldrx+1_000_000, oldtx+2_000_000)
    next_metrics = rate_metrics(now,(parsed,100),102)
    assert next_metrics['cpu'] == 25.0
    assert next_metrics['rx_bps'] == 4_000_000
    assert next_metrics['tx_bps'] == 8_000_000

def test_password_policy_local_pin():
    for good in ('0000', '1234', '9876', 'VeryLongPassword123'):
        assert valid_password(good)
    for bad in ('123', '12345', 'abcd', '١٢٣٤', '1234a', 'short'):
        assert not valid_password(bad)


def test_four_digit_pin_login(client, monkeypatch):
    monkeypatch.setattr(settings, 'PASSWORD_HASH', auth.new_hash('1234'))
    bad = client.post('/api/login', json={'username':'admin','password':'0000'})
    assert bad.status_code == 401
    good = client.post('/api/login', json={'username':'admin','password':'1234'})
    assert good.status_code == 200


def test_exposed_mode_password_policy():
    assert valid_password('1234')
    assert not valid_password('1234', strong_only=True)
    assert valid_password('MyStrongPassword-123', strong_only=True)

def test_custom_port_and_https_gate(monkeypatch, tmp_path):
    import app.server as server
    called=[]
    monkeypatch.setattr(server.uvicorn,'run',lambda *args,**kw: called.append(kw))
    monkeypatch.setenv('DW_PORT','15432')
    monkeypatch.setenv('DW_BIND','127.0.0.1')
    server.main()
    assert called[-1]['port']==15432
    monkeypatch.setenv('DW_BIND','0.0.0.0')
    monkeypatch.setenv('DW_SESSION_SECURE','1')
    monkeypatch.setenv('DW_PASSWORD_TYPE','pin-or-strong')
    with pytest.raises(SystemExit,match='explicit confirmation'):
        server.main()
    key=tmp_path/'key'; crt=tmp_path/'crt'
    key.write_text('test'); crt.write_text('test')
    monkeypatch.setenv('DW_PASSWORD_TYPE','strong')
    monkeypatch.setenv('DW_TLS_KEY',str(key))
    monkeypatch.setenv('DW_TLS_CERT',str(crt))
    server.main()
    assert called[-1]['ssl_keyfile']==str(key)
    monkeypatch.setenv('DW_PASSWORD_TYPE','pin')
    monkeypatch.setenv('DW_ALLOW_SHORT_PIN_PUBLIC','1')
    server.main()
    assert called[-1]['port']==15432


def test_public_pin_wizard_and_password_reset():
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    installer=(base/'install.sh').read_text()
    access=(base/'configure-access.sh').read_text()
    assert 'hash-password --with-type' in installer
    assert 'Change existing PIN/password?' in access
    assert 'DW_ALLOW_SHORT_PIN_PUBLIC' in access
    assert 'DW_FIRST_INSTALL' in installer
