#!/usr/bin/env bash
# DARKWATCH in-place update. Never run install.sh or configure-access.sh.
set -Eeuo pipefail
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo 'Run as root: sudo bash update.sh' >&2
  exit 1
fi
if [[ ! -f /etc/darkwatch/env || ! -d /opt/darkwatch/app ||
      ! -x /opt/darkwatch/.venv/bin/python || ! -f /etc/systemd/system/darkwatch.service ]]; then
  echo 'Existing DARKWATCH installation not found. Update cancelled (no new installation).' >&2
  exit 1
fi
command -v git >/dev/null || { echo 'git is required to update existing DARKWATCH'; exit 1; }
source_repo=/root/darkwatch-source
workdir=''
if [[ -d "$source_repo/.git" ]]; then
  echo '[DARKWATCH] Refreshing existing GitHub checkout...'
  git -C "$source_repo" pull --ff-only
else
  workdir="$(mktemp -d /tmp/darkwatch-update.XXXXXX)"
  git clone --depth 1 https://github.com/darktunnelmika/darkwatch.git "$workdir/repo"
  source_repo="$workdir/repo"
fi
backup="$(mktemp -d /opt/darkwatch-update-backup.XXXXXX)"
cp -a /opt/darkwatch/app "$backup/app"
cp -a /opt/darkwatch/requirements.txt "$backup/requirements.txt"
rollback() {
  echo '[DARKWATCH] Update failed; restoring previous application code.' >&2
  rm -rf /opt/darkwatch/app
  cp -a "$backup/app" /opt/darkwatch/app
  cp -a "$backup/requirements.txt" /opt/darkwatch/requirements.txt
  /opt/darkwatch/.venv/bin/python -m pip install --disable-pip-version-check -r /opt/darkwatch/requirements.txt >/dev/null 2>&1 || true
  systemctl restart darkwatch || true
}
cleanup() {
  if [[ -n "$workdir" ]]; then rm -rf "$workdir"; fi
}
trap cleanup EXIT
trap 'rollback' ERR
echo '[DARKWATCH] Updating application code (not database, port or login)...'
cp -a "$source_repo/app/." /opt/darkwatch/app/
cp -a "$source_repo/requirements.txt" /opt/darkwatch/requirements.txt
chown -R root:root /opt/darkwatch/app /opt/darkwatch/requirements.txt
/opt/darkwatch/.venv/bin/python -m pip install --disable-pip-version-check -r /opt/darkwatch/requirements.txt
/opt/darkwatch/.venv/bin/python -m compileall -q /opt/darkwatch/app
systemctl restart darkwatch
sleep 2
systemctl is-active --quiet darkwatch
# Check that pages are actually served; "active" alone is not enough.
port="$(sed -n 's/^DW_PORT=//p' /etc/darkwatch/env | tail -1)"
bind="$(sed -n 's/^DW_BIND=//p' /etc/darkwatch/env | tail -1)"
protocol=http
if [[ "$bind" == '0.0.0.0' ]]; then protocol=https; fi
if [[ ! "$port" =~ ^[0-9]+$ ]]; then port=8765; fi
ready=0
for attempt in 1 2 3; do
  web_code="$(curl -ksS --connect-timeout 2 --max-time 8 -o /dev/null -w '%{http_code}' "$protocol://127.0.0.1:$port/" || true)"
  assets_code="$(curl -ksS --connect-timeout 2 --max-time 8 -o /dev/null -w '%{http_code}' "$protocol://127.0.0.1:$port/static/app.js" || true)"
  if [[ "$web_code" == 200 && "$assets_code" == 200 ]]; then ready=1; break; fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo "[DARKWATCH] Web health-check failed (HTML=$web_code, JS=$assets_code). Rolling back." >&2
  false
fi
trap - ERR
rm -rf "$backup"
echo ''
echo 'DARKWATCH UPDATE: SUCCESS'
echo 'Port, panel login, SSH key and saved servers remain unchanged.'
port="$(sed -n 's/^DW_PORT=//p' /etc/darkwatch/env | tail -1)"
ip="$(sed -n 's/^DW_DISPLAY_IP=//p' /etc/darkwatch/env | tail -1)"
bind="$(sed -n 's/^DW_BIND=//p' /etc/darkwatch/env | tail -1)"
if [[ "$bind" == '0.0.0.0' && -n "$ip" && -n "$port" ]]; then
  echo "PANEL ADDRESS: https://$ip:$port"
else
  echo "PANEL ADDRESS (private): http://127.0.0.1:${port:-8765}"
fi
