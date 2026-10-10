#!/usr/bin/env bash
set -Eeuo pipefail
cfg=/etc/darkwatch/env
getcfg() { sed -n "s/^$1=//p" "$cfg" | tail -n 1; }
setcfg() {
  if grep -q "^$1=" "$cfg"; then
    sed -i "s|^$1=.*|$1=$2|" "$cfg"
  else
    printf '%s=%s\n' "$1" "$2" >> "$cfg"
  fi
}
echo ""
echo "======= DARKWATCH ACCESS SETUP ======="
if [[ "${DW_FIRST_INSTALL:-0}" != 1 ]]; then
  read -rp 'Change existing PIN/password? [y/N]: ' resetpw
  if [[ "${resetpw,,}" == y || "${resetpw,,}" == yes ]]; then
    echo 'Set a 4-digit numeric PIN or a 12+ character password:'
    until result="$(cd /opt/darkwatch && /opt/darkwatch/.venv/bin/python -m app.cli hash-password --with-type)"; do
      echo 'PIN/password mismatch or invalid length. Try again (Ctrl-C to cancel).' >&2
    done
    kind="${result%%|*}"; hash="${result#*|}"
    [[ "$kind" == pin || "$kind" == strong ]] || exit 1
    [[ "$hash" == scrypt\$* ]] || exit 1
    setcfg DW_PASSWORD_HASH "$hash"
    setcfg DW_PASSWORD_TYPE "$kind"
  fi
fi
oldport="$(getcfg DW_PORT)"
if [[ ! "$oldport" =~ ^[0-9]{4,5}$ ]]; then oldport=8765; fi
while true; do
  read -rp "Choose panel TCP port [$oldport]: " port
  if [[ -z "$port" ]]; then port="$oldport"; fi
  if [[ "$port" =~ ^[0-9]{4,5}$ ]] && ((10#$port >= 1024 && 10#$port <= 65535)); then port="$((10#$port))"; break; fi
  echo "Use a port from 1024 through 65535."
done
echo "1) Direct HTTPS panel: https://SERVER-IP:PORT (PIN optional after security warning)"
echo "2) Private SSH tunnel panel (4-digit PIN allowed)"
while true; do
  read -rp "Access mode [1]: " mode
  if [[ -z "$mode" ]]; then mode=1; fi
  if [[ "$mode" == 1 || "$mode" == 2 ]]; then break; fi
done
if [[ "$mode" == 1 ]]; then
  ip="$(hostname -I | awk '{print $1}')"
  while true; do
    read -rp "Server public IPv4 [$ip]: " val
    if [[ -n "$val" ]]; then ip="$val"; fi
    if python3 - "$ip" <<'PYIP'
import ipaddress,sys
try: ipaddress.IPv4Address(sys.argv[1])
except ValueError: sys.exit(1)
PYIP
    then break; fi
    echo "Please enter a valid IPv4 address."
  done
  bind=0.0.0.0
  if [[ "$(getcfg DW_PASSWORD_TYPE)" != strong ]]; then
    echo 'SECURITY WARNING: A 4-digit PIN is weak for public access to a web SSH terminal.'
    echo 'HTTPS does not stop PIN guessing. Restrict source IPs in your datacenter firewall.'
    read -rp 'Type YES to enable direct HTTPS with this PIN: ' acknowledgement
    if [[ "$acknowledgement" != YES ]]; then
      echo 'Direct PIN access cancelled. Choose private mode or a stronger password.' >&2
      exit 1
    fi
    setcfg DW_ALLOW_SHORT_PIN_PUBLIC 1
  else
    setcfg DW_ALLOW_SHORT_PIN_PUBLIC 0
  fi
  install -d -m 0750 -o root -g darkwatch /etc/darkwatch/tls
  key=/etc/darkwatch/tls/server.key
  crt=/etc/darkwatch/tls/server.crt
  if [[ ! -s "$key" || ! -s "$crt" || "$ip" != "$(getcfg DW_DISPLAY_IP)" ]]; then
    openssl req -newkey rsa:3072 -nodes -x509 -days 365 -sha256 -keyout "$key" -out "$crt" -subj "/CN=DarkWatch" -addext "subjectAltName=IP:$ip" >/dev/null 2>&1
  fi
  chown root:darkwatch "$key" "$crt"
  chmod 0640 "$key" "$crt"
  setcfg DW_SESSION_SECURE 1
else
  ip=127.0.0.1
  bind=127.0.0.1
  if [[ -z "$(getcfg DW_PASSWORD_TYPE)" ]]; then setcfg DW_PASSWORD_TYPE pin-or-strong; fi
  setcfg DW_SESSION_SECURE 0
  setcfg DW_ALLOW_SHORT_PIN_PUBLIC 0
fi
setcfg DW_PORT "$port"
setcfg DW_BIND "$bind"
setcfg DW_DISPLAY_IP "$ip"
setcfg DW_TLS_CERT /etc/darkwatch/tls/server.crt
setcfg DW_TLS_KEY /etc/darkwatch/tls/server.key
chmod 0600 "$cfg"
systemctl restart darkwatch
sleep 1
if ! systemctl is-active --quiet darkwatch; then
  echo "Dashboard failed to start:"
  journalctl -u darkwatch -n 35 --no-pager
  exit 1
fi
echo "======================================"
if [[ "$mode" == 1 ]]; then
  echo "PANEL ADDRESS: https://$ip:$port"
  echo "First browser visit may show a self-signed HTTPS certificate warning."
  echo "Make sure the datacenter permits TCP port $port; firewall rules are not modified."
else
  echo "PANEL ADDRESS (SSH tunnel): http://127.0.0.1:$port"
  echo "On PC: ssh -N -L $port:127.0.0.1:$port root@SERVER_IP"
fi
echo "SERVICE STATUS: active"
echo "======================================"
