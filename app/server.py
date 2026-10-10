"""Starts DarkWatch on the installer-selected port and safe access mode."""
import os
import uvicorn

def main():
    host=os.environ.get('DW_BIND','127.0.0.1')
    port=int(os.environ.get('DW_PORT','8765'))
    if host not in ('127.0.0.1','0.0.0.0') or not (1024 <= port <= 65535):
        raise SystemExit('Invalid bind/port')
    opts={'host':host,'port':port,'workers':1,'access_log':False}
    if host=='0.0.0.0':
        if os.environ.get('DW_SESSION_SECURE')!='1':
            raise SystemExit('Refusing public HTTP without secure session cookie')
        if os.environ.get('DW_PASSWORD_TYPE')!='strong' and os.environ.get('DW_ALLOW_SHORT_PIN_PUBLIC')!='1':
            raise SystemExit('Public PIN access requires explicit confirmation (DW_ALLOW_SHORT_PIN_PUBLIC=1)')
        cert=os.environ.get('DW_TLS_CERT','/etc/darkwatch/tls/server.crt')
        key=os.environ.get('DW_TLS_KEY','/etc/darkwatch/tls/server.key')
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            raise SystemExit('Public HTTPS certificate missing')
        opts.update(ssl_certfile=cert,ssl_keyfile=key)
    uvicorn.run('app.main:app',**opts)

if __name__=='__main__':
    main()
