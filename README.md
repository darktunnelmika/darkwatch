# DARKWATCH — Private Fleet Monitor

**A private, cyber-styled, read-only Linux fleet monitor with real SSH terminals.**

- Multiple Iran/VPS servers on one dashboard
- CPU (including cores), RAM, disk, load and uptime, every 3 seconds
- Live total server NIC traffic RX/TX in bits/s and cumulative bytes (physical network adapters combined)
- All-server bandwidth overview and short-term historical graphs
- Secure, interactive in-browser OpenSSH terminal for remote nodes
- Responsive, RTL, all assets served locally (no remote CDNs), monitor wallboard mode
- Agentless: the central hub connects to existing Linux servers over SSH; no changes to tunnels, network interfaces or firewall
- Single personal admin, secure cookie, CSRF validation, and SSH host-key checks

**Scope v0.1.1:** live data + in-memory graphs; graph history resets on reboot. No long-term historical DB, no Telegram notifications, no file editor, no server reboot/automated commands. This is intentionally a personal observation tool, not a commercial NOC product.

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

**Shortcut (review the script before running):** `curl -fsSL https://raw.githubusercontent.com/darktunnelmika/darkwatch/main/bootstrap.sh | bash`

## Install on the Iran hub

```bash
sudo apt update && sudo apt install -y python3 python3-venv openssh-client
unzip darkwatch-v0.1.0.zip
cd darkwatch
sudo bash install.sh
sudo systemctl status darkwatch --no-pager
```

The installer prompts for a password with either a 4-digit numeric PIN (SSH tunnel only) or a 12+ character password and admin username on first install. It registers `darkwatch.service`, creates a dedicated unprivileged `darkwatch` system user, generates an Ed25519 SSH key, and binds the web server **only to 127.0.0.1:8765**.

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
- Network rates are calculated from Linux NIC RX/TX byte counters across **all hardware-backed server adapters**. Loopback, virtual tunnels, bridges and containers are not added again (otherwise traffic could be counted twice). If hardware-backed interfaces are not identifiable, the default-route adapter is used as a fallback. This is overall network-interface traffic, not per-service/client usage. Cumulative NIC counters reset when the OS resets them. Detailed per-interface statistics remain available through `/api/state`.
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

### Local PIN support (v0.1.1)

The installer accepts **exactly four ASCII digits** as a personal PIN (or a regular password of 12 or more characters). A four-digit PIN is weak: **do not expose this web application publicly** if using a PIN. Keep binding to `127.0.0.1` and access it only through your SSH tunnel. If setup was interrupted by a password rejection, re-run `bootstrap.sh` to resume installation.


## v0.1.2 — configurable port / printed address
Installation now asks for TCP port (1024-65535) and access mode, then prints the panel URL when the service is active. Direct HTTPS works at `https://SERVER-IP:PORT` with a self-signed TLS certificate and requires a password of at least 12 characters. Four-digit PIN is available only in private SSH tunnel mode. No firewall, SSH or tunnel configurations are modified automatically.


## PIN/password setup fix (v0.1.3)
On first install, choose a four-digit numeric PIN or 12+ character password. Public HTTPS no longer forces a second 12-character password. To use a short PIN with direct HTTPS, you must type YES to acknowledge the risk; HTTPS does NOT protect against PIN guessing, and an IP allowlist or a private tunnel is strongly recommended. On reinstall, answer 'y' to 'Change existing PIN/password?' to reset the PIN. Server entries, database and SSH keys are retained.


## Update an existing panel (no reinstallation)

Run on the already installed DARKWATCH host as root:

```bash
curl -fsSL https://raw.githubusercontent.com/darktunnelmika/darkwatch/main/update.sh | bash
```

The updater only fetches code, installs Python dependencies inside the existing
`/opt/darkwatch/.venv` and restarts `darkwatch.service`. It does not run the
installer, reset the login, change the selected port, overwrite
`/etc/darkwatch/env`, remove saved servers, or replace the SSH key.
Active web SSH sessions will disconnect during the service restart.

### Add a server using its SSH password

Open **Servers > Add Server** and enter the server name, IP, SSH port,
SSH username and **SSH password**. The panel checks login and reads real
CPU/RAM/network counters before storing the server. For a previously unknown
SSH host key, verify the shown SHA256 fingerprint independently in the
datacenter console and explicitly confirm it. A changed known host key is rejected.

The SSH password is encrypted at rest with a randomly generated Fernet key
inside the private `/var/lib/darkwatch/.ssh/credential.key` (permissions 0600).
The server password is never returned through the browser API. Do not lose
the credential key if restoring the database from backup; without it, saved
passwords cannot be decrypted. The `🔑` control lets you set/change an SSH
password for an already registered server. SSH key authentication continues
working for servers configured using the previous method.

For internet-facing installation, use a **strong admin password and an IP
allowlist**. A 4-digit panel PIN is not sufficient protection for a tool
that stores SSH credentials and exposes browser terminals.


### Server list reliability

- Add a server with IP/domain, SSH port, SSH user and SSH password. First connection
  may request host-key fingerprint confirmation; verify it against your provider console.
- Re-adding the same IP, SSH port and SSH user after a failed setup securely checks
  the password and updates the **existing** entry rather than creating a duplicate.
- The delete button removes the monitoring record and its encrypted SSH credential
  from DARKWATCH **only**; it never deletes anything from the remote VPS.
- Updates use `update.sh` exclusively and verify both HTML and JavaScript are
  served before reporting success. The configured port and database are retained.


### Root SSH password prompt

In **Add Server**, the field is explicitly labelled **رمز عبور root سرور**
when the SSH username is `root` (the default). This is the root SSH password
on the **target VPS**, NOT the DARKWATCH admin PIN/password. The field is required
in the web form, and the SSH connection is verified before saving the server.
For another SSH username, the label changes automatically. Previously configured
key-only servers are not modified.


## Mobile viewport and one-step SSH onboarding (v0.1.5)

The panel keeps a stable small viewport on mobile browsers, ensures form inputs
are at least 16px to prevent iPhone Safari's automatic focus zoom, and keeps
card widths and modals within the mobile screen without disabling pinch zoom.

After entering the SSH user/password, the panel tests authentication and reads
real CPU/RAM/network metrics in **one step**. On the first successful SSH
connection it automatically pins the server's SSH host key (trust on first use,
TOFU); future host key changes are **rejected**. This avoids repeated prompts,
but TOFU cannot protect a first connection from a man-in-the-middle attack.
Verify the pinned SSH fingerprint later using your hosting provider's console.
For strict pre-verification, operators can set `DW_SSH_REQUIRE_VERIFIED_HOSTS=1`
in `/etc/darkwatch/env` and manage known_hosts themselves.

Password-enrolled servers continue using their encrypted password on every
monitoring refresh, rather than falling back to SSH-key-only monitoring.
Root password authentication must be enabled on the target VPS: the monitor does
not change SSHD configuration or security settings on any monitored server.

After successful SSH registration, DARKWATCH immediately marks the new server
online using its first verified CPU/RAM/network sample. Live CPU and bitrate
values become available after the next sampling interval. Connection failures
show specific SSH/authentication/port/timeout errors instead of silently
creating offline records.
