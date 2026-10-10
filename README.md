# DARKWATCH — Private Fleet Monitor

**A private, cyber-styled, read-only Linux fleet monitor with real SSH terminals.**

- Multiple Iran/VPS servers on one dashboard
- CPU (including cores), RAM, disk, load and uptime, every 3 seconds
- Live per-interface network traffic RX/TX in bits/s and cumulative bytes
- All-server bandwidth overview and short-term historical graphs
- Secure, interactive in-browser OpenSSH terminal for remote nodes
- Responsive, RTL, all assets served locally (no remote CDNs), monitor wallboard mode
- Agentless: the central hub connects to existing Linux servers over SSH; no changes to tunnels, network interfaces or firewall
- Single personal admin, secure cookie, CSRF validation, and SSH host-key checks

**Scope v0.1.0:** live data + in-memory graphs; graph history resets on reboot. No long-term historical DB, no Telegram notifications, no file editor, no server reboot/automated commands. This is intentionally a personal observation tool, not a commercial NOC product.

## Requirements

- Ubuntu/Debian VPS hub with Linux, Python 3.10+, python3-venv, pip, OpenSSH client and systemd
- Outbound TCP access from the hub to SSH ports of monitored servers
- Linux VPS targets with `/proc`, `df`, `/bin/sh`, `head`, `grep`, `cat` (typical Ubuntu/Debian)
- A public HTTPS reverse proxy, or **an SSH tunnel**, for browser access. **Do not expose raw HTTP port 8765 on the internet.**

## Install from GitHub

On Ubuntu/Debian, run as root:

```bash
apt update && apt install -y ca-certificates git python3 python3-venv openssh-client
git clone --depth 1 https://github.com/darktunnelmika/darkwatch.git /root/darkwatch-source
bash /root/darkwatch-source/install.sh
```

The installer will ask for an admin username and password. Your configuration/data stay in `/etc/darkwatch` and `/var/lib/darkwatch` on subsequent installations, respectively.

> The repository URL above becomes functional only once the repository is created and these files are uploaded.

## Install on the Iran hub

```bash
sudo apt update && sudo apt install -y python3 python3-venv openssh-client
unzip darkwatch-v0.1.0.zip
cd darkwatch
sudo bash install.sh
sudo systemctl status darkwatch --no-pager
```

The installer prompts for a password with at least 12 characters and admin username on first install. It registers `darkwatch.service`, creates a dedicated unprivileged `darkwatch` system user, generates an Ed25519 SSH key, and binds the web server **only to 127.0.0.1:8765**.

**Local access without a domain:** from your Windows PowerShell terminal:

```powershell
ssh -N -L 8765:127.0.0.1:8765 root@HUB_IP
```

Open http://127.0.0.1:8765 in your browser. **For SSH-tunnel HTTP**, set `DW_SESSION_SECURE=0` in `/etc/darkwatch/env` and restart (`sudo systemctl restart darkwatch`). This is acceptable only over an encrypted SSH tunnel ending on your own machine. For public access, restore `DW_SESSION_SECURE=1` and configure HTTPS.

**HTTPS with a domain:** terminate TLS in a reverse proxy on the same machine (Caddy or Nginx), forwarding to `http://127.0.0.1:8765`. Set `DW_PUBLIC_ORIGIN=https://your-domain.example` in `/etc/darkwatch/env`, leave secure session cookies enabled, and restart the service. Set WebSocket upgrade headers with Nginx. Avoid opening port 8765 in the firewall.

## Add each monitored server securely

1. Read the hub's **public** key:

   ```bash
   sudo cat /var/lib/darkwatch/.ssh/id_ed25519.pub
   ```

2. On each target server, add that public key to the chosen user's `~/.ssh/authorized_keys`, with correct file permissions. Avoid passwords and favor a dedicated restricted monitoring user if possible. For **browser SSH** you need a shell-enabled user, and commands execute with that user's permissions.
3. Verify the target **SSH host fingerprint independently** using the hosting provider's console (do not blindly trust the results of ssh-keyscan). After matching the fingerprint, add its host key to the hub's known_hosts. One way to **view** a candidate host key (not inherently trustworthy) is:

   ```bash
   ssh-keyscan -p 22 -H TARGET_IP
   ```

   Save the **verified** host key to `/var/lib/darkwatch/.ssh/known_hosts` and ensure `darkwatch` can read it. For a nonstandard port, known_hosts host patterns must use `[IP]:PORT`. Example after independent verification:

   ```bash
   sudo -u darkwatch sh -c 'ssh-keyscan -p 22 -H TARGET_IP >> /var/lib/darkwatch/.ssh/known_hosts'
   sudo chmod 600 /var/lib/darkwatch/.ssh/known_hosts
   ```

   That command is just an example of recording a key: run only after you have **verified** the SSH host fingerprint. It is not a verification method.
4. In the **Servers → Add server** form, enter the target IP, port and SSH username. Data will appear once authentication and host-key verification succeed.

### Minimal target permissions

The collector only runs standard read-only commands (`/proc/stat`, `/proc/meminfo`, `/proc/net/dev`, `df`, etc.). A standard user can gather these metrics; root is **not required**. Browser SSH has whatever permissions the selected user has. Consider two separate keys/users if you need strong monitoring-only restrictions.

## Configure and troubleshoot

```bash
sudo systemctl status darkwatch --no-pager
sudo journalctl -u darkwatch -n 70 --no-pager
sudo systemctl restart darkwatch
```

Configuration: `/etc/darkwatch/env`. Database: `/var/lib/darkwatch/darkwatch.db`. SSH key: `/var/lib/darkwatch/.ssh/id_ed25519`. Change the login password by generating a hash with `cd /opt/darkwatch && sudo /opt/darkwatch/.venv/bin/python -m app.cli hash-password`, then update `DW_PASSWORD_HASH` in `/etc/darkwatch/env` and restart. Restart terminates active sessions; saved cookies remain valid up to 12 hours (database sessions).

### Security notes

- Do **not** forward the hub's SSH private key or expose its contents.
- Web SSH has shell-level capabilities. Limit panel access with SSH tunnel / VPN or HTTPS + firewall and a strong login password. Additional 2FA is recommended before exposing the panel publicly (not implemented in v0.1.0).
- Known_hosts verification is strict. Adding an IP to the web UI cannot silently accept a new host key.
- No Agent, sudo, privileged systemd service, blind firewall changes, or tunnel configuration changes are performed on remote targets.
- Network rates are measured by the Linux kernel's NIC counters on the **default network interface** of each server. They include all traffic through that interface, not solely the tunnel or client traffic. Extra interfaces and cumulative byte counters are available in `/api/state`; each server card has an interface selector when the server has more than one interface.
- The aggregate network number simply sums each server's default NIC rates. If traffic crosses several monitored servers, it will be counted multiple times. This figure is a fleet throughput view, not customer billable usage.

## Development (Linux/macOS with OpenSSH)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m app.cli hash-password
# store output in DW_PASSWORD_HASH
export DW_DATA_DIR=/tmp/darkwatch-dev DW_PASSWORD_HASH='scrypt$...'
export DW_SESSION_SECURE=0
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## License

DarkWatch application code: for personal use / adaptation. Bundled `xterm.js` and `xterm-fit` client-side terminal dependencies are third-party MIT-licensed code and remain under their original terms. Read `THIRD_PARTY.md`.