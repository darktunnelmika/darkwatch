import subprocess

import pytest
from fastapi.testclient import TestClient

from app import auth, db, settings
from app.cli import valid_password
from app.collector import SCRIPT, parse_output, rate_metrics, host_network_interfaces
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


def test_tls_directory_permissions_regression():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1]
    installer = (base / 'install.sh').read_text()
    access = (base / 'configure-access.sh').read_text()
    required = 'install -d -m 0750 -o root -g darkwatch /etc/darkwatch'
    assert required in installer
    assert required in access
    assert 'runuser -u darkwatch -- test -r "$key"' in access
    assert 'runuser -u darkwatch -- test -r "$crt"' in access
    assert 'Type YES to enable direct HTTPS' not in access


def test_total_server_traffic_all_physical_interfaces_without_double_counting():
    """Count every host NIC, but not tun/bridge/veth that mirror packet paths."""
    snap = {
        'cpu': {'cpu': (100, 200)}, 'mem': {'MemTotal': 1024, 'MemAvailable': 512},
        'disk': {'total': 1024, 'used': 512}, 'uptime': 120,
        'load': [0.1, 0.1, 0.1], 'primary': 'ens3',
        'net': {
            'ens3': (100000, 200000), 'ens4': (10000, 20000),
            'tun0': (40000, 50000), 'docker0': (40000, 50000),
            'lo': (500000, 500000),
        }, 'physical_interfaces': ['ens3', 'ens4'],
    }
    nxt = dict(snap)
    nxt['cpu'] = {'cpu': (150, 300)}
    nxt['net'] = {
        'ens3': (1100000, 2200000), 'ens4': (510000, 1020000),
        'tun0': (2040000, 2050000), 'docker0': (2040000, 2050000),
        'lo': (5500000, 5500000),
    }
    result = rate_metrics(nxt, (snap, 100), 102)
    assert result['network_interfaces'] == ['ens3', 'ens4']
    assert result['rx_bps'] == 6_000_000
    assert result['tx_bps'] == 12_000_000
    assert result['rx_total'] == 1_610_000
    assert result['tx_total'] == 3_220_000
    assert result['interfaces']['tun0']['rx_bps'] == 8_000_000
    assert result['network_scope'] == 'all-physical'


def test_host_nic_fallback_without_sysfs():
    snap={'primary':'eth0','net':{'lo':(100,100),'eth0':(200,400),
                                'tun0':(300,600),'docker0':(400,800)}}
    assert host_network_interfaces(snap) == ['eth0']


def test_dashboard_uses_server_total_not_selected_virtual_interface():
    from pathlib import Path
    dashboard=(Path(__file__).resolve().parents[1] / 'app/static/app.js').read_text()
    assert "function hostNetwork(s)" in dashboard
    assert "selectedIfaces" not in dashboard
    assert "effectiveNet(s)" not in dashboard


def test_password_ssh_register_verify_and_update_existing_server(client, monkeypatch, tmp_path):
    """One add request authenticates SSH and saves encrypted credentials."""
    from app import ssh_credentials
    monkeypatch.setattr(settings, 'DATA_DIR', tmp_path / 'credentials-data')
    attempts = []
    def fake_collect(server, password, script, confirmed=None):
        attempts.append((password, confirmed, server['host']))
        raw = subprocess.run(['sh', '-s'], input=SCRIPT, capture_output=True,
                             check=True, text=True).stdout
        return raw.encode()
    monkeypatch.setattr(ssh_credentials, 'collect_password_metrics', fake_collect)
    csrf = login(client)
    payload = {'name':'Iran server','host':'192.0.2.99','port':2022,'ssh_user':'root',
               'group_name':'ایران','ssh_password':'RemotePassword_123!'}
    headers = {'X-CSRF-Token':csrf}
    created = client.post('/api/servers', json=payload, headers=headers)
    assert created.status_code == 201
    assert len(attempts) == 1
    sid = created.json()['id']
    state = client.get('/api/state').json()
    entry = next(s for s in state['servers'] if s['id'] == sid)
    assert entry['online'] is True
    assert entry['memory_total'] > 0
    registered = client.get('/api/servers').json()
    assert len(registered)==1
    assert 'ssh_password' not in registered[0]
    assert 'encrypted_password' not in registered[0]
    enc = db.get_encrypted_password(sid)
    assert enc and 'RemotePassword_123!' not in enc
    assert ssh_credentials.decrypt_password(enc) == 'RemotePassword_123!'
    change = client.put(f'/api/servers/{sid}/ssh-password', headers=headers,
        json={'ssh_password':'AnotherPassword_456!'})
    assert change.status_code == 200
    assert len(attempts) == 2
    assert ssh_credentials.decrypt_password(db.get_encrypted_password(sid)) == 'AnotherPassword_456!'
    assert client.delete(f'/api/servers/{sid}', headers=headers).status_code == 200
    assert db.get_encrypted_password(sid) is None


def test_password_login_does_not_disclose_ssh_secrets(client):
    from app import ssh_credentials
    csrf=login(client)
    headers={'X-CSRF-Token':csrf}
    sid=db.add_server({'name':'Example','host':'203.0.113.2','port':22,
        'ssh_user':'root','group_name':'ایران'})
    db.set_password(sid, 'ENCRYPTED_SAMPLE_DO_NOT_RETURN')
    for endpoint in ['/api/servers','/api/state']:
        response=client.get(endpoint)
        assert response.status_code == 200
        assert 'ENCRYPTED_SAMPLE_DO_NOT_RETURN' not in response.text


def test_ui_password_field_and_upgrade_script_safety():
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    page=(base/'app/static/index.html').read_text()
    ui=(base/'app/static/app.js').read_text()
    upgrader=(base/'update.sh').read_text()
    assert 'name="ssh_password"' in page
    assert 'type="password"' in page
    assert 'data-password' in ui
    assert 'host_key' not in ui
    assert 'await api(target,{method,body:JSON.stringify(d),timeoutMs:45000})' in ui
    assert '/etc/darkwatch/env' in upgrader
    assert 'bash "$source_repo/install.sh"' not in upgrader
    assert 'configure-access.sh' not in upgrader.replace(
        '# DARKWATCH in-place update. Never run install.sh or configure-access.sh.','')


def test_bootstrap_never_starts_with_black_blank_screen(client):
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    page=(base/'app/static/index.html').read_text()
    script=(base/'app/static/app.js').read_text()
    assert '<div id="login" class="login-screen">' in page
    assert '<div id="application" class="app-shell hidden">' in page
    assert 'const timeout=setTimeout(()=>controller.abort()' in script
    assert "showLogin();" in script
    assert client.get('/').status_code == 200



def test_reregister_existing_server_repairs_ssh_password_without_duplicate(client, monkeypatch, tmp_path):
    from app import ssh_credentials
    monkeypatch.setattr(settings, 'DATA_DIR', tmp_path / 'credential-storage')
    def fake_probe(server, password, script, confirmed=None):
        assert password == 'another-good-SSH-password'
        return subprocess.run(
            ['sh','-s'], input=SCRIPT, capture_output=True, text=True, check=True
        ).stdout.encode()
    monkeypatch.setattr(ssh_credentials, 'collect_password_metrics', fake_probe)
    original = db.add_server({'name':'Existing Iran','host':'192.0.2.55','port':22,
                              'ssh_user':'root','group_name':'ایران'})
    assert db.get_encrypted_password(original) is None
    csrf=login(client)
    res=client.post('/api/servers', headers={'X-CSRF-Token':csrf},
        json={'name':'Duplicate Iran','host':'192.0.2.55','port':22,'ssh_user':'root',
              'group_name':'ایران','ssh_password':'another-good-SSH-password'})
    assert res.status_code == 201
    assert res.json()['updated'] is True
    assert res.json()['id'] == original
    saved=client.get('/api/servers').json()
    assert len(saved)==1
    assert saved[0]['name']=='Existing Iran'
    assert ssh_credentials.decrypt_password(db.get_encrypted_password(original)) == 'another-good-SSH-password'
    assert client.delete(f'/api/servers/{original}',headers={'X-CSRF-Token':csrf}).status_code==200
    assert db.get_encrypted_password(original) is None


def test_update_is_in_place_and_checks_web_assets():
    from pathlib import Path
    update=(Path(__file__).resolve().parents[1]/'update.sh').read_text()
    assert '/etc/darkwatch/env' in update
    assert '/opt/darkwatch/app' in update
    assert '/static/app.js' in update
    assert '/opt/darkwatch/requirements.txt' in update
    assert 'configure-access.sh' not in update.replace(
        '# DARKWATCH in-place update. Never run install.sh or configure-access.sh.','')


def test_root_password_is_explicit_in_add_server_form(client):
    page=client.get('/').text
    assert 'رمز عبور root سرور' in page
    assert 'name="ssh_password" type="password"' in page
    assert 'maxlength="1024" required' in page
    assert 'name="ssh_user" value="root"' in page
    assert 'پسورد root سرور مقصد' in page
    js=client.get('/static/app.js').text
    assert 'updateSshPasswordLabel' in js
    assert 'if(!d.ssh_password)' in js


def test_add_server_buttons_use_multi_selector(client):
    js=client.get('/static/app.js').text
    assert "$$('.add-server-btn').forEach(" in js
    assert "$('.add-server-btn').forEach(" not in js.replace("$$('.add-server-btn').forEach(", "")


def test_first_connection_pins_new_key_without_prompt(monkeypatch, tmp_path):
    import os
    import paramiko
    from app import ssh_credentials as ssh
    monkeypatch.setenv('DW_SSH_REQUIRE_VERIFIED_HOSTS','0')
    monkeypatch.setattr(settings, 'KNOWN_HOSTS', tmp_path/'known_hosts')
    key=paramiko.RSAKey.generate(1024)
    policy=ssh.ConfirmHostPolicy()
    policy.missing_host_key(None, 'host.example', key)
    assert policy.pending == ('host.example',key)
    assert not settings.KNOWN_HOSTS.exists()
    ssh.save_verified_host_key(policy)
    assert settings.KNOWN_HOSTS.is_file()
    hosts=paramiko.HostKeys(str(settings.KNOWN_HOSTS))
    assert hosts.lookup('host.example')[key.get_name()] == key
    # Security: strict opt-in still requires verified fingerprints.
    monkeypatch.setenv('DW_SSH_REQUIRE_VERIFIED_HOSTS','1')
    with pytest.raises(ssh.UntrustedHostKey):
        ssh.ConfirmHostPolicy().missing_host_key(None,'untrusted.example',key)


def test_monitor_uses_encrypted_ssh_password_after_register(monkeypatch,tmp_path):
    import asyncio
    from app import ssh_credentials
    from app.collector import Monitor
    monkeypatch.setattr(db, 'DB_PATH', tmp_path/'monitor.db')
    monkeypatch.setattr(settings, 'DATA_DIR', tmp_path/'monitor')
    db.init_db()
    server={'name':'Iran VPS','host':'192.0.2.50','port':2222,
            'ssh_user':'root','group_name':'ایران'}
    encrypted=ssh_credentials.encrypt_password('secret-root-pass')
    sid=db.add_password_server(server,encrypted)
    record=db.get_server(sid)
    received=[]
    def fake_read(entry,passphrase,script,confirmed=None):
        received.append((entry['host'],passphrase))
        return subprocess.run(['sh','-s'],input=script,
                              check=True,capture_output=True,text=True).stdout.encode()
    monkeypatch.setattr(ssh_credentials,'collect_password_metrics',fake_read)
    monitor=Monitor()
    asyncio.run(monitor.poll(record))
    assert received == [('192.0.2.50','secret-root-pass')]
    assert monitor.snapshots[sid]['online'] is True
    assert monitor.snapshots[sid]['memory_total'] > 0
    assert monitor.snapshots[sid]['interfaces']
    assert 'ssh_password' not in monitor.state()['servers'][1]


def test_mobile_stability_css_and_no_host_confirmation():
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    css=(base/'app/static/style.css').read_text()
    html=(base/'app/static/index.html').read_text()
    js=(base/'app/static/app.js').read_text()
    assert '100svh' in css
    assert 'font-size:16px!important' in css
    assert 'viewport-fit=cover' in html
    assert 'window.confirm(' not in js.split("  $('#server-form').addEventListener('submit',async e=>{",1)[1].split("  $('#search-server')",1)[0]
    assert 'const result=await api(target,' in js


def test_probe_errors_are_actionable_for_root_password(monkeypatch):
    import paramiko
    from app.main import verify_password_connection
    from app import ssh_credentials
    def reject(*args,**kwargs):
        raise paramiko.AuthenticationException('Authentication failed')
    monkeypatch.setattr(ssh_credentials,'collect_password_metrics',reject)
    with pytest.raises(Exception) as error:
        verify_password_connection({'name':'Test VPS','host':'192.0.2.10','port':22,'ssh_user':'root'},'badpwd')
    assert getattr(error.value,'status_code',None)==422
    assert 'root' in error.value.detail


def test_mobile_nav_is_a_bottom_dock_and_pages_fit_viewport(client):
    page=client.get('/').text
    css=client.get('/static/style.css').text
    assert page.count('class="nav-mobile-copy"') == 4
    assert 'class="logout-mobile-copy"' in page
    assert 'style.css?v=0.1.7' in page
    assert 'app.js?v=0.1.7' in page
    assert '@media (max-width: 767px)' in css
    assert 'position: fixed; inset: auto 0 0 0' in css
    assert 'grid-template-columns: repeat(4,minmax(0,1fr))' in css
    assert '.data-table tr { grid-template-columns: repeat(3,minmax(0,1fr))' in css
    assert '.data-table tbody { display: grid' in css
    assert 'overflow-x: clip' in css
    assert 'min-height: 100svh' in css
    assert 'env(safe-area-inset-bottom,0px)' in css
    assert 'font-size:16px!important' in css
    # Accessible pinch zoom must remain enabled; no maximum-scale or user-scalable=no.
    assert 'user-scalable=no' not in page


def test_resource_capacity_fields_are_available_in_monitoring(client):
    from app.collector import rate_metrics, parse_output, SCRIPT
    import subprocess
    txt = subprocess.run(['sh','-s'],input=SCRIPT,text=True,
                         capture_output=True,check=True).stdout
    snapshot = parse_output(txt)
    metrics = rate_metrics(snapshot,None,100)
    assert metrics['cores'] >= 1
    assert metrics['memory_total'] >= metrics['memory_used'] >= 0
    assert metrics['disk_total'] >= metrics['disk_used'] >= 0
    assert metrics['swap_total'] >= metrics['swap_used'] >= 0


def test_capacity_details_appear_in_server_cards_without_mobile_overflow(client):
    html=client.get('/').text
    js=client.get('/static/app.js').text
    css=client.get('/static/style.css').text
    assert 'app.js?v=0.1.7' in html
    assert 'style.css?v=0.1.7' in html
    assert 'function vcpuFmt(cores)' in js
    assert 'function capacityFmt(used,total)' in js
    assert "vcpuFmt(s.cores)" in js
    assert 'capacityFmt(s.memory_used,s.memory_total)' in js
    assert 'capacityFmt(s.disk_used,s.disk_total)' in js
    assert 'capacityFmt(s.swap_used,s.swap_total)' in js
    assert '.server-stats .stat-detail' in css
    assert 'text-overflow:ellipsis' in css
