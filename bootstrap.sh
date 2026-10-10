#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo 'Please run as root: sudo bash bootstrap.sh' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates git python3 python3-venv openssh-client openssl iproute2
repo=https://github.com/darktunnelmika/darkwatch.git
target=/root/darkwatch-source
if [[ ! -e "$target" ]]; then
  git clone --depth 1 "$repo" "$target"
elif [[ -d "$target/.git" ]]; then
  git -C "$target" pull --ff-only
else
  echo "$target exists but is not a Git repository; aborting without overwriting." >&2
  exit 1
fi
# Read admin credentials from the controlling terminal, even with curl | bash.
if [[ -r /dev/tty ]]; then
  bash "$target/install.sh" </dev/tty
else
  echo "Interactive terminal required to set the admin password." >&2
  exit 1
fi
