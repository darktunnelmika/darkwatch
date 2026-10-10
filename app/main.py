"""DarkWatch read-only metrics & authenticated browser SSH."""
import asyncio
import collections
import fcntl
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import struct
import termios
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.websockets import WebSocketState

from . import auth, db, settings
from .collector import Monitor

monitor = Monitor()
login_attempts = collections.defaultdict(collections.deque)
active_terminals = set()

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    if not settings.PASSWORD_HASH:
        print('WARNING: DW_PASSWORD_HASH not set. Logins are disabled.', flush=True)
    monitor.task = asyncio.create_task(monitor.run())
    yield
    monitor.task.cancel()
    try: await monitor.task
    except asyncio.CancelledError: pass

app = FastAPI(title='DarkWatch', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
static_dir = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = ("default-src 'self'; img-src 'self' data:; "
        "font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self' ws: wss:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    return response

@app.get('/')
def index():
    return FileResponse(static_dir / 'index.html')

class LoginRequest(BaseModel):
    username: str
    password: str


def client_ip(request: Request):
    # Do not trust Forwarded/X-Forwarded-For supplied by the client.
    return request.client.host if request.client else 'unknown'

@app.post('/api/login')
def login(data: LoginRequest, request: Request):
    ip = client_ip(request)
    q = login_attempts[ip]
    now = time.monotonic()
    while q and q[0] < now-900: q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, 'ورود موقتاً محدود شد. ۱۵ دقیقه بعد امتحان کن.')
    q.append(now)
    good_user = secrets.compare_digest(data.username.encode(), settings.ADMIN_USER.encode())
    good_pass = auth.check_hash(data.password, settings.PASSWORD_HASH)
    if not good_user or not good_pass:
        raise HTTPException(401, 'نام کاربری یا رمز عبور اشتباه است')
    q.clear()
    token, csrf = auth.create_session()
    res = JSONResponse({'ok': True, 'csrf': csrf, 'username': settings.ADMIN_USER})
    res.set_cookie('dw_session', token, max_age=settings.SESSION_SECONDS, httponly=True,
                   samesite='strict', secure=settings.SESSION_SECURE, path='/')
    return res


def current_session(request: Request):
    sess = auth.get_session(request.cookies.get('dw_session'))
    if not sess: raise HTTPException(401, 'نیاز به ورود داری')
    return sess


def verify_csrf(request: Request, sess=Depends(current_session)):
    provided = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(provided.encode(), sess['csrf'].encode()):
        raise HTTPException(403, 'CSRF token invalid')
    origin = request.headers.get('origin')
    if origin and not origin_ok(origin, request.headers.get('host', '')):
        raise HTTPException(403, 'Origin mismatch')
    return sess


def origin_ok(origin: str, host: str):
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname:
            return False
        if settings.PUBLIC_ORIGIN:
            return origin.rstrip('/') == settings.PUBLIC_ORIGIN
        return parsed.netloc.lower() == host.lower()
    except Exception:
        return False

@app.get('/api/me')
def me(sess=Depends(current_session)):
    return {'username': settings.ADMIN_USER, 'csrf': sess['csrf'], 'version':'0.1.0'}

@app.post('/api/logout')
def logout(request: Request, _=Depends(verify_csrf)):
    auth.delete_session(request.cookies.get('dw_session'))
    res = JSONResponse({'ok':True})
    res.delete_cookie('dw_session', path='/')
    return res

@app.get('/api/state')
def state(_=Depends(current_session)):
    return monitor.state()

@app.get('/api/servers')
def servers(_=Depends(current_session)):
    return db.servers()

class ServerInput(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default='root', min_length=1, max_length=32)
    group_name: str = Field(default='ایران', min_length=1, max_length=48)

    @field_validator('name','group_name')
    @classmethod
    def safe_text(cls,v):
        v = v.strip()
        if not v or any(ord(c)<32 for c in v): raise ValueError('نام نامعتبر')
        return v

    @field_validator('ssh_user')
    @classmethod
    def safe_user(cls,v):
        if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', v):
            raise ValueError('نام کاربری SSH نامعتبر')
        return v

    @field_validator('host')
    @classmethod
    def safe_host(cls,v):
        v = v.strip().lower()
        try:
            ipaddress.ip_address(v)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*',v):
                raise ValueError('دامنه/IP نامعتبر')
        return v

@app.post('/api/servers', status_code=201)
def create_server(data: ServerInput, _=Depends(verify_csrf)):
    try:
        sid = db.add_server(data.model_dump())
        return {'id':sid, 'ok':True}
    except Exception as e:
        if 'UNIQUE' in str(e): raise HTTPException(409,'این سرور از قبل اضافه شده')
        raise

@app.delete('/api/servers/{server_id}')
def delete_server(server_id:int, _=Depends(verify_csrf)):
    if server_id < 1 or not db.remove_server(server_id): raise HTTPException(404,'سرور پیدا نشد')
    monitor.clear(server_id)
    return {'ok':True}


def ws_auth(ws:WebSocket):
    return auth.get_session(ws.cookies.get('dw_session')) is not None and origin_ok(
        ws.headers.get('origin',''), ws.headers.get('host',''))

def terminal_resize(fd, cols, rows):
    winsize = struct.pack('HHHH', rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

@app.websocket('/ws/ssh/{server_id}')
async def terminal(ws:WebSocket, server_id:int):
    if not ws_auth(ws):
        await ws.close(code=4403); return
    server = db.get_server(server_id) if server_id>0 else None
    if not server:
        await ws.close(code=4404); return
    if len(active_terminals) >= 4:
        await ws.close(code=4429); return
    await ws.accept()
    token = object()
    active_terminals.add(token)
    master = slave = None
    proc = None
    outgoing = None
    try:
        if not shutil.which('ssh'):
            await ws.send_json({'type':'error','message':'OpenSSH client نصب نشده است'}); return
        master, slave = os.openpty()
        terminal_resize(slave, 100, 28)
        proc = await asyncio.create_subprocess_exec('ssh', '-tt', '-i', str(settings.SSH_KEY),
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={settings.KNOWN_HOSTS}', '-o', 'ConnectTimeout=5',
            '-p', str(server['port']), '-l', server['ssh_user'], '--', server['host'],
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave); slave = None
        async def pump():
            while True:
                try:
                    output = await asyncio.to_thread(os.read, master, 16384)
                except (OSError, ValueError):
                    break
                if not output: break
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json({'type':'output','data':output.decode('utf-8','replace')})
        outgoing = asyncio.create_task(pump())
        while True:
            payload = await ws.receive_text()
            if len(payload) > 70000: await ws.close(code=1009); break
            try: message = json.loads(payload)
            except json.JSONDecodeError: continue
            if message.get('type')=='input':
                s = message.get('data','')
                if isinstance(s,str) and len(s)<65536:
                    await asyncio.to_thread(os.write,master,s.encode('utf-8'))
            elif message.get('type')=='resize':
                cols = message.get('cols',100); rows = message.get('rows',28)
                if type(cols)==int and type(rows)==int and 20<=cols<=400 and 8<=rows<=180:
                    terminal_resize(master, cols, rows)
    except (WebSocketDisconnect, OSError, ConnectionError):
        pass
    except Exception as exc:
        try: await ws.send_json({'type':'error','message':'خطا در اتصال SSH'})
        except Exception: pass
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            try: await asyncio.wait_for(proc.wait(),timeout=2)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
        if master is not None: os.close(master)
        if slave is not None: os.close(slave)
        if outgoing:
            outgoing.cancel()
            try: await outgoing
            except (asyncio.CancelledError, OSError): pass
        active_terminals.discard(token)
        if ws.client_state == WebSocketState.CONNECTED:
            try: await ws.close()
            except Exception: pass
