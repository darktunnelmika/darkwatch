#!/usr/bin/env bash
set -Eeuo pipefail
trap 'printf "[DARKWATCH] Install failed at line %s (exit %s). Service was not started.\n" "$LINENO" "$?" >&2' ERR
if [[ $EUID -ne 0 ]]; then echo 'Run as root: sudo bash install.sh'; exit 1; fi
if ! command -v python3 >/dev/null || ! command -v ssh >/dev/null; then
  echo 'Install first: apt update && apt install -y python3 python3-venv openssh-client'; exit 1
fi
python3 - <<'PY_CHECK'
import sys
if sys.version_info < (3, 10):
    raise SystemExit('DarkWatch requires Python 3.10 or newer.')
PY_CHECK
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SRC" != '/opt/darkwatch' ]]; then
  mkdir -p /opt/darkwatch
  cp -a "$SRC"/app "$SRC"/requirements.txt "$SRC"/deploy /opt/darkwatch/
fi
id -u darkwatch >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/darkwatch --shell /usr/sbin/nologin darkwatch
install -d -m 0700 -o darkwatch -g darkwatch /var/lib/darkwatch /var/lib/darkwatch/.ssh
install -d -m 0750 -o root -g darkwatch /etc/darkwatch
python3 -m venv /opt/darkwatch/.venv
/opt/darkwatch/.venv/bin/python -m pip install --disable-pip-version-check -r /opt/darkwatch/requirements.txt
if [[ ! -f /var/lib/darkwatch/.ssh/id_ed25519 ]]; then
  runuser -u darkwatch -- ssh-keygen -q -t ed25519 -f /var/lib/darkwatch/.ssh/id_ed25519 -N ''
fi
first_install=0
if [[ ! -f /etc/darkwatch/env ]]; then
  first_install=1
  echo 'Choose a 4-digit numeric PIN or a stronger password (12+ characters):'
  until result="$(cd /opt/darkwatch && /opt/darkwatch/.venv/bin/python -m app.cli hash-password --with-type)"; do
    echo 'Password was not accepted. Try again (Ctrl-C to cancel).' >&2
  done
  kind="${result%%|*}"
  hash="${result#*|}"
  [[ "$kind" == pin || "$kind" == strong ]] || { echo 'Invalid PIN type'; exit 1; }
  [[ "$hash" == scrypt\$* ]] || { echo 'Invalid password hash'; exit 1; }
  while true; do
    read -rp 'Admin username [admin]: ' username
    username="${username:-admin}"
    [[ "$username" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] && break
    echo 'Use letters/numbers/underscore/hyphen only.'
  done
  cat > /etc/darkwatch/env <<ENV
DW_USER=$username
DW_PASSWORD_HASH=$hash
DW_PASSWORD_TYPE=$kind
DW_ALLOW_SHORT_PIN_PUBLIC=0
DW_DATA_DIR=/var/lib/darkwatch
DW_DB=/var/lib/darkwatch/darkwatch.db
DW_SSH_KEY=/var/lib/darkwatch/.ssh/id_ed25519
DW_KNOWN_HOSTS=/var/lib/darkwatch/.ssh/known_hosts
DW_SESSION_SECURE=1
DW_POLL_SECONDS=3
# DW_PUBLIC_ORIGIN=https://your-domain.example
ENV
  chmod 0600 /etc/darkwatch/env
fi
chown -R root:root /opt/darkwatch/app /opt/darkwatch/requirements.txt
chown darkwatch:darkwatch /var/lib/darkwatch
chmod 700 /var/lib/darkwatch/.ssh
cp /opt/darkwatch/deploy/darkwatch.service /etc/systemd/system/darkwatch.service
systemctl daemon-reload
systemctl enable --now darkwatch
DW_FIRST_INSTALL="$first_install" bash "$SRC/configure-access.sh"
printf '\nDarkWatch installed: systemctl status darkwatch\n'
printf 'Dashboard configured with selected port and access mode.\n'
printf 'SSH public key to allow on each monitored server:\n'
cat /var/lib/darkwatch/.ssh/id_ed25519.pub
printf '\nRead README.md for SSH host verification and HTTPS/Tunnel instructions.\n'
