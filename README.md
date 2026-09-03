# Custom Panel — OpenSSH + SSH-over-WebSocket Gateway

A self-hosted panel for managing tunnel-only SSH accounts, with an async
gateway that accounts for every byte and connection so the dashboard's
online state and traffic usage are always accurate.

## Architecture

```text
OpenSSH TCP ports 20000-24999 ─┐
                               ├─> Async Gateway (app/proxy_runtime.py) ─> internal OpenSSH 127.0.0.1:2222
SSH-over-WebSocket 25000-29999 ┘        (WebSocket leg is TLS-wrapped
                                          with a real Let's Encrypt cert)

Server administration stays on normal SSH port 22.
Managed users only get a forced tunnel shell (no interactive login) via the
internal sshd, reached exclusively through their assigned TCP or WS endpoint.
```

- Every managed byte crosses the gateway; online state is the gateway's live
  connection count, snapshotted every 0.5s and durably flushed to SQLite
  every 2s (WAL mode) so it survives restarts without losing data or hammering
  the disk.
- The web dashboard (`templates/index.html` + `static/app.js`) polls
  `/api/stats` every 5s and overlays not-yet-flushed bytes on top of stored
  totals, so `used / quota` and per-user online dots are accurate to the
  second, not just to the last DB flush.
- OpenSSH TCP and SSH-over-WebSocket use separate listener ports and cannot
  collide.

## Anti-filtering design

- The WebSocket leg is terminated with a **real, CA-signed** TLS certificate
  (Let's Encrypt), so on the wire it is a normal HTTPS handshake — the same
  thing DPI middleboxes see for any ordinary website. This is what actually
  matters for evading SNI/certificate-fingerprint based blocking; a
  self-signed cert would not help and is refused by the installer.
- The panel's own web UI is also served over HTTPS with the same certificate.
- Because the certificate is tied to a domain, `install.sh` requires you to
  point a domain/subdomain's DNS **A record** at the server before it will
  install anything, and re-verifies that on every run.

## Features

- OpenSSH TCP and SSH-over-WebSocket (TLS) per user
- Add, edit, pause, resume, delete, reset traffic
- Per-user password, quota, remaining days, enabled methods
- Separate TCP/WS online indicators, accurate combined RX/TX
- Automatic pause on quota/time expiry (checked every 5s)
- Backup and restore (JSON)
- Change panel admin username/password from the panel
- **Change the panel's own listening port from inside the panel**
  (Admin tab → "تغییر پورت پنل"); the firewall rule for the new port is
  opened automatically and the web service restarts on it.
- Encrypted user passwords at rest, hashed admin password
- One asyncio gateway process, one Gunicorn worker — light enough for small
  VPS instances even with many concurrent users

## Install

Requirements: a fresh Ubuntu/Debian VPS, and a domain/subdomain whose DNS
**A record already points at the server's IP**.

```bash
export CUSTOM_PANEL_DOMAIN=panel.example.com   # must already resolve to this server
curl -fsSL https://raw.githubusercontent.com/rima0222/ss/main/install.sh -o /tmp/install.sh
bash -n /tmp/install.sh
sudo -E bash /tmp/install.sh
```

Optional environment variables:

- `CUSTOM_PANEL_PORT` — panel web port (default `443`; can also be changed
  later from inside the panel).
- `CUSTOM_PANEL_EMAIL` — contact email used for the Let's Encrypt account
  (default `admin@<your domain>`).

Every run of the installer also does a best-effort removal of other common
SSH/VPN panels it detects on the box (x-ui/3x-ui/s-ui, Marzban, Marzneshin,
Hiddify, wg-easy, and similarly named services/directories) so this panel is
the only thing managing users and ports on the server. If you're running a
panel not in that list, remove it manually before installing.

## Credentials

```bash
sudo bash /etc/custom-panel/show-credentials.sh
```

## Diagnostics

```bash
sudo bash /etc/custom-panel/diagnose.sh
```

## Important client rule

Use the TCP port or WebSocket URL downloaded from the user's "Config" button
(`wss://` when TLS is active). Connecting to server port 22 bypasses the
user's assigned gateway endpoint and is reserved for server administration.
