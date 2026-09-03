#!/usr/bin/env bash
set -Eeuo pipefail

APP=/etc/custom-panel
REPO="${CUSTOM_PANEL_REPO_URL:-https://github.com/rima0222/ss.git}"
CLEAN="${CUSTOM_PANEL_CLEAN_INSTALL:-1}"

[[ "$EUID" -eq 0 ]] || { echo "Run as root."; exit 1; }

backup_old(){
  [[ -d "$APP" ]] || return 0
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  rescue="/root/custom-panel-rescue-$stamp"
  mkdir -p "$rescue"
  for item in data backups .env admin-credentials.txt; do
    [[ -e "$APP/$item" ]] && cp -a "$APP/$item" "$rescue/"
  done
  tar -C /root -czf "$rescue.tar.gz" "$(basename "$rescue")" 2>/dev/null || true
}

# Best-effort removal of other well-known SSH/VPN/proxy panels so this panel
# is the only thing managing accounts and ports on the box. This only touches
# services/directories with recognizable names; it will not touch unrelated
# software. If you use a panel not listed here, remove it manually first.
purge_foreign_panels(){
  echo "Checking for previously installed panels to remove..."

  local services=(
    x-ui s-ui 3x-ui
    marzban marzban-node marzneshin
    hiddify-panel hiddify-manager
    wg-easy
    sanaei-panel ssh-panel ssh-manager
  )
  for svc in "${services[@]}"; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}\.service"; then
      echo "  removing service: $svc"
      systemctl disable --now "$svc" >/dev/null 2>&1 || true
      rm -f "/etc/systemd/system/${svc}.service"
    fi
  done

  # Docker-based panels (Marzban, Marzneshin, wg-easy are commonly deployed
  # via docker-compose).
  if command -v docker >/dev/null 2>&1; then
    for name in marzban marzban-node marzneshin wg-easy; do
      if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qi "$name"; then
        echo "  removing docker container(s): $name"
        docker rm -f "$(docker ps -aq --filter "name=$name")" >/dev/null 2>&1 || true
      fi
    done
  fi

  local dirs=(
    /usr/local/x-ui /etc/x-ui /usr/bin/x-ui
    /opt/marzban /var/lib/marzban /opt/marzneshin
    /opt/hiddify-manager
    /etc/v2ray-agent
  )
  for dir in "${dirs[@]}"; do
    [[ -e "$dir" ]] && { echo "  removing: $dir"; rm -rf "$dir"; }
  done

  systemctl daemon-reload 2>/dev/null || true
  echo "Foreign-panel check done."
}
purge_foreign_panels

if [[ "$CLEAN" == "1" ]]; then
  systemctl disable --now custom-panel custom-panel-proxy custom-panel-accounting custom-panel-helper custom-panel-sshd custom-panel-watchdog.timer custom-panel-watchdog.service 2>/dev/null || true
  backup_old
  rm -f /etc/systemd/system/custom-panel.service
  rm -f /etc/systemd/system/custom-panel-proxy.service
  rm -f /etc/systemd/system/custom-panel-accounting.service
  rm -f /etc/systemd/system/custom-panel-helper.service
  rm -f /etc/systemd/system/custom-panel-sshd.service
  rm -f /etc/systemd/system/custom-panel-watchdog.service
  rm -f /etc/systemd/system/custom-panel-watchdog.timer
  rm -rf "$APP"
  systemctl daemon-reload
fi

if ! pgrep -x useradd >/dev/null 2>&1 &&
   ! pgrep -x usermod >/dev/null 2>&1 &&
   ! pgrep -x userdel >/dev/null 2>&1 &&
   ! pgrep -x chpasswd >/dev/null 2>&1; then
  rm -f /etc/passwd.lock /etc/shadow.lock /etc/group.lock /etc/gshadow.lock
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv git curl ca-certificates openssh-server sqlite3 ufw util-linux dnsutils certbot

getent group panelusers >/dev/null || groupadd --system panelusers
getent group custompanel >/dev/null || groupadd --system custompanel
id -u custompanel >/dev/null 2>&1 || useradd --system --no-create-home --gid custompanel --shell /usr/sbin/nologin custompanel
usermod -g custompanel custompanel

cat > /usr/local/bin/panel-hold <<'EOF'
#!/usr/bin/env bash
trap 'exit 0' TERM INT HUP
while true; do sleep 3600; done
EOF
chmod 755 /usr/local/bin/panel-hold
grep -qxF '/usr/local/bin/panel-hold' /etc/shells || echo '/usr/local/bin/panel-hold' >> /etc/shells

# Keep the normal SSH daemon on port 22 for server administration.
rm -f /etc/ssh/sshd_config.d/99-custom-panel.conf
sshd -t
systemctl enable --now ssh
systemctl restart ssh

# Managed users use a separate OpenSSH instance bound only to localhost.
cat > /etc/ssh/sshd_config_custom_panel <<'EOF'
Port 2222
ListenAddress 127.0.0.1
Protocol 2
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key
UsePAM yes
PasswordAuthentication yes
KbdInteractiveAuthentication no
PermitRootLogin no
AllowGroups panelusers
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding yes
GatewayPorts no
PermitTunnel no
PrintMotd no
PidFile /run/sshd-custom-panel.pid
Subsystem sftp internal-sftp

Match Group panelusers
    ForceCommand /usr/local/bin/panel-hold
    PermitTTY no
EOF

/usr/sbin/sshd -t -f /etc/ssh/sshd_config_custom_panel

cat > /etc/systemd/system/custom-panel-sshd.service <<'EOF'
[Unit]
Description=Custom Panel internal OpenSSH
After=network.target
Before=custom-panel-proxy.service

[Service]
Type=notify
ExecStart=/usr/sbin/sshd -D -f /etc/ssh/sshd_config_custom_panel
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

git clone --depth=1 "$REPO" "$APP"
test -f "$APP/app/__init__.py"
test -f "$APP/requirements.txt"
test -f "$APP/templates/index.html"

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --upgrade pip
"$APP/venv/bin/pip" install -r "$APP/requirements.txt"

mkdir -p "$APP/data" "$APP/backups" "$APP/runtime" /run/custom-panel
chown -R root:custompanel "$APP"
find "$APP" -type d -exec chmod 750 {} +
find "$APP" -type f -exec chmod 640 {} +
find "$APP/venv/bin" -type f -exec chmod 750 {} +
chmod 750 "$APP/install.sh" "$APP/show-credentials.sh" "$APP/reset-admin-password.sh" "$APP/diagnose.sh"
chown -R custompanel:custompanel "$APP/data" "$APP/backups" "$APP/runtime"
chmod 770 "$APP/data" "$APP/backups" "$APP/runtime"
chown root:custompanel /run/custom-panel
chmod 770 /run/custom-panel

SERVER_HOST="${CUSTOM_PANEL_SERVER_HOST:-$(curl -4fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')}"
[[ -n "$SERVER_HOST" ]] || { echo "Could not detect server IP."; exit 1; }

# --- Mandatory domain + real (CA-signed) TLS certificate ------------------
# This check runs on every install/re-install, on purpose. A real
# Let's-Encrypt certificate lets the SSH-over-WebSocket endpoint present a
# completely normal-looking HTTPS TLS handshake, which is what actually
# matters against SNI/certificate-based DPI filtering. A self-signed cert
# does not help with this, so it is refused.
DOMAIN="${CUSTOM_PANEL_DOMAIN:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "ERROR: CUSTOM_PANEL_DOMAIN is not set."
  echo "Point an A record for a domain/subdomain at this server's IP ($SERVER_HOST),"
  echo "then re-run with e.g.:"
  echo "  export CUSTOM_PANEL_DOMAIN=panel.example.com"
  exit 1
fi

echo "Verifying DNS for $DOMAIN ..."
RESOLVED_IP="$(dig +short A "$DOMAIN" @1.1.1.1 2>/dev/null | tail -n1)"
if [[ -z "$RESOLVED_IP" ]]; then
  echo "ERROR: $DOMAIN does not resolve to any IP yet."
  echo "Create the DNS A record first, wait for propagation, then re-run this installer."
  exit 1
fi
if [[ "$RESOLVED_IP" != "$SERVER_HOST" ]]; then
  echo "ERROR: $DOMAIN resolves to $RESOLVED_IP, not this server ($SERVER_HOST)."
  echo "Fix the DNS A record before installing."
  exit 1
fi

echo "DNS OK. Requesting a Let's Encrypt certificate for $DOMAIN ..."
systemctl stop custom-panel >/dev/null 2>&1 || true
fuser -k 80/tcp >/dev/null 2>&1 || true
CERT_EMAIL="${CUSTOM_PANEL_EMAIL:-admin@$DOMAIN}"
certbot certonly --standalone --non-interactive --agree-tos \
  -m "$CERT_EMAIL" -d "$DOMAIN" --keep-until-expiring

CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
[[ -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" ]] || {
  echo "ERROR: certificate was not issued."
  echo "Make sure port 80 is open to the internet on this server and DNS is correct, then re-run."
  exit 1
}

# The panel/proxy run as an unprivileged "custompanel" user, which cannot
# read /etc/letsencrypt (root-only, 0700). Keep a private copy it can read,
# refreshed automatically whenever certbot renews.
TLS_DIR="$APP/tls"
mkdir -p "$TLS_DIR"
sync_cert(){
  install -m 640 -o root -g custompanel "$CERT_DIR/fullchain.pem" "$TLS_DIR/fullchain.pem"
  install -m 640 -o root -g custompanel "$CERT_DIR/privkey.pem" "$TLS_DIR/privkey.pem"
}
sync_cert

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/custom-panel-reload.sh <<HOOK
#!/usr/bin/env bash
install -m 640 -o root -g custompanel "$CERT_DIR/fullchain.pem" "$TLS_DIR/fullchain.pem"
install -m 640 -o root -g custompanel "$CERT_DIR/privkey.pem" "$TLS_DIR/privkey.pem"
systemctl restart custom-panel-proxy custom-panel 2>/dev/null || true
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/custom-panel-reload.sh

PANEL_PORT="${CUSTOM_PANEL_PORT:-443}"

# --- Self-heal reinstall: reuse the previous install's secrets so restored
# data (encrypted passwords, admin login) keeps working seamlessly. ---------
RESTORE_FROM=""
if [[ -n "${rescue:-}" && -f "$rescue/.env" ]]; then
  RESTORE_FROM="$rescue"
  echo "Existing installation detected — reusing its keys/admin login and restoring its data."
  # shellcheck disable=SC1090
  set -a; source "$rescue/.env"; set +a
  SECRET="$CUSTOM_PANEL_SECRET_KEY"
  DATA_KEY="$CUSTOM_PANEL_DATA_KEY"
  OLD_ADMIN_USERNAME="$CUSTOM_PANEL_ADMIN_USERNAME"
  OLD_ADMIN_HASH="$CUSTOM_PANEL_ADMIN_PASSWORD_HASH"
fi

ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)"
if [[ -z "${SECRET:-}" ]]; then
  SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
fi
if [[ -z "${DATA_KEY:-}" ]]; then
  DATA_KEY="$("$APP/venv/bin/python" - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
)"
fi
if [[ -n "${OLD_ADMIN_USERNAME:-}" && -n "${OLD_ADMIN_HASH:-}" ]]; then
  ADMIN_HASH="$OLD_ADMIN_HASH"
  ADMIN_USERNAME_VALUE="$OLD_ADMIN_USERNAME"
else
  ADMIN_HASH="$("$APP/venv/bin/python" - <<PY
from werkzeug.security import generate_password_hash
print(generate_password_hash("""$ADMIN_PASSWORD"""))
PY
)"
  ADMIN_USERNAME_VALUE="admin"
fi

cat > "$APP/.env" <<EOF
CUSTOM_PANEL_SECRET_KEY=$SECRET
CUSTOM_PANEL_ADMIN_USERNAME=$ADMIN_USERNAME_VALUE
CUSTOM_PANEL_ADMIN_PASSWORD_HASH=$ADMIN_HASH
CUSTOM_PANEL_DATA_KEY=$DATA_KEY
CUSTOM_PANEL_DB=$APP/data/panel.db
CUSTOM_PANEL_SERVER_HOST=$SERVER_HOST
CUSTOM_PANEL_INTERNAL_SSH_PORT=2222
CUSTOM_PANEL_TCP_PORT_START=20000
CUSTOM_PANEL_TCP_PORT_END=24999
CUSTOM_PANEL_WS_PORT_START=25000
CUSTOM_PANEL_WS_PORT_END=29999
CUSTOM_PANEL_HELPER_SOCKET=/run/custom-panel/helper.sock
CUSTOM_PANEL_LIVE_PATH=/run/custom-panel/live.json
CUSTOM_PANEL_DOMAIN=$DOMAIN
CUSTOM_PANEL_TLS_CERT=$TLS_DIR/fullchain.pem
CUSTOM_PANEL_TLS_KEY=$TLS_DIR/privkey.pem
PANEL_PORT=$PANEL_PORT
EOF
if [[ -n "$RESTORE_FROM" ]]; then
  cat > "$APP/admin-credentials.txt" <<EOF
This is a reinstall: your previous admin username/password still work.
(A new random password was NOT generated, so nothing changed here.)
Username: $ADMIN_USERNAME_VALUE
EOF
else
  cat > "$APP/admin-credentials.txt" <<EOF
Username: $ADMIN_USERNAME_VALUE
Password: $ADMIN_PASSWORD
EOF
fi
chown root:custompanel "$APP/.env"
chmod 640 "$APP/.env"
chmod 600 "$APP/admin-credentials.txt"

if [[ -n "$RESTORE_FROM" ]]; then
  echo "Restoring users/usage/backups from the previous install ($RESTORE_FROM) ..."
  [[ -d "$RESTORE_FROM/data" ]] && cp -a "$RESTORE_FROM/data/." "$APP/data/"
  [[ -d "$RESTORE_FROM/backups" ]] && cp -a "$RESTORE_FROM/backups/." "$APP/backups/"
  chown -R custompanel:custompanel "$APP/data" "$APP/backups"
  find "$APP/data" -type d -exec chmod 770 {} +
  find "$APP/data" -type f -exec chmod 660 {} +
  echo "Restore done: existing users keep their remaining days/quota/usage exactly as before."
fi

cat > /etc/tmpfiles.d/custom-panel.conf <<'EOF'
d /run/custom-panel 0770 root custompanel -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/custom-panel.conf

cat > /etc/systemd/system/custom-panel-helper.service <<EOF
[Unit]
Description=Custom Panel account helper
After=local-fs.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=PYTHONPATH=$APP
ExecStart=$APP/venv/bin/python -m app.account_helper
Restart=on-failure
RestartSec=2
PrivateTmp=true
LimitNOFILE=1024

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/custom-panel-proxy.service <<EOF
[Unit]
Description=OpenSSH and SSH WebSocket proxy
After=network-online.target ssh.service
Wants=network-online.target

[Service]
Type=simple
User=custompanel
Group=custompanel
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=PYTHONPATH=$APP
ExecStart=$APP/venv/bin/python -m app.proxy_runtime
Restart=on-failure
RestartSec=2
PrivateTmp=true
NoNewPrivileges=true
UMask=0007
LimitNOFILE=65535
Nice=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/custom-panel-accounting.service <<EOF
[Unit]
Description=Custom Panel accounting
After=custom-panel-helper.service custom-panel-proxy.service
Requires=custom-panel-helper.service

[Service]
Type=simple
User=custompanel
Group=custompanel
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=PYTHONPATH=$APP
ExecStart=$APP/venv/bin/python -m app.accounting_worker
Restart=on-failure
RestartSec=3
PrivateTmp=true
NoNewPrivileges=true
UMask=0007
Nice=10

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/custom-panel.service <<EOF
[Unit]
Description=Custom Panel web
After=network-online.target custom-panel-helper.service custom-panel-proxy.service
Requires=custom-panel-helper.service

[Service]
Type=simple
User=custompanel
Group=custompanel
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=PYTHONPATH=$APP
ExecStart=$APP/venv/bin/gunicorn --workers 1 --threads 4 --timeout 30 --keep-alive 3 --max-requests 3000 --max-requests-jitter 300 --certfile=$TLS_DIR/fullchain.pem --keyfile=$TLS_DIR/privkey.pem --bind 0.0.0.0:\${PANEL_PORT} "app:create_app()"
Restart=on-failure
RestartSec=3
PrivateTmp=true
NoNewPrivileges=true
UMask=0007
LimitNOFILE=8192
Nice=5

[Install]
WantedBy=multi-user.target
EOF

runuser -u custompanel -- test -x "$APP"
runuser -u custompanel -- test -r "$APP/app/proxy_runtime.py"
runuser -u custompanel -- test -r "$APP/app/__init__.py"
runuser -u custompanel -- test -w "$APP/data"

# Create and verify the SQLite database using the same user that runs all
# database-writing services. This prevents readonly database errors.
runuser -u custompanel -- env \
  PYTHONPATH="$APP" \
  CUSTOM_PANEL_SECRET_KEY="$SECRET" \
  CUSTOM_PANEL_ADMIN_USERNAME="admin" \
  CUSTOM_PANEL_ADMIN_PASSWORD_HASH="$ADMIN_HASH" \
  CUSTOM_PANEL_DATA_KEY="$DATA_KEY" \
  CUSTOM_PANEL_DB="$APP/data/panel.db" \
  CUSTOM_PANEL_SERVER_HOST="$SERVER_HOST" \
  CUSTOM_PANEL_INTERNAL_SSH_PORT="2222" \
  CUSTOM_PANEL_TCP_PORT_START="20000" \
  CUSTOM_PANEL_TCP_PORT_END="24999" \
  CUSTOM_PANEL_WS_PORT_START="25000" \
  CUSTOM_PANEL_WS_PORT_END="29999" \
  CUSTOM_PANEL_HELPER_SOCKET="/run/custom-panel/helper.sock" \
  CUSTOM_PANEL_LIVE_PATH="/run/custom-panel/live.json" \
  "$APP/venv/bin/python" - <<'PY'
from app.db import init_db, connect
from app.config import Config
init_db(Config.DB_PATH)
with connect() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS install_write_test(id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO install_write_test(id,value) VALUES(1,'ok')")
    conn.commit()
    assert conn.execute("SELECT value FROM install_write_test WHERE id=1").fetchone()[0] == "ok"
    conn.execute("DROP TABLE install_write_test")
    conn.commit()
print("SQLite write test: OK")
PY

chown -R custompanel:custompanel "$APP/data"
find "$APP/data" -type d -exec chmod 770 {} +
find "$APP/data" -type f -exec chmod 660 {} +

ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow "$PANEL_PORT/tcp" >/dev/null 2>&1 || true
ufw allow 20000:29999/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

# --- Watchdog: catches the rare case a service wedges without crashing
# (systemd's Restart=on-failure only helps on an actual crash/exit). Checked
# every 30s; nothing here should ever require a manual reboot again.
cat > "$APP/watchdog.sh" <<'WD'
#!/usr/bin/env bash
set -u
LIVE=/run/custom-panel/live.json
now=$(date +%s)

# internal sshd must be listening
ss -lnt | grep -q "127.0.0.1:2222" || systemctl restart custom-panel-sshd

# proxy must be writing a fresh live snapshot (stale = hung event loop)
if [[ -f "$LIVE" ]]; then
  updated=$(python3 -c "import json;print(json.load(open('$LIVE')).get('updated_at',0))" 2>/dev/null || echo 0)
  if (( now - updated > 30 )); then
    systemctl restart custom-panel-proxy
  fi
else
  systemctl restart custom-panel-proxy
fi

# web panel must answer
PORT="$(grep -oP '(?<=^PANEL_PORT=).*' /etc/custom-panel/.env 2>/dev/null || echo 443)"
curl -ksf --max-time 5 "https://127.0.0.1:$PORT/login" >/dev/null || systemctl restart custom-panel
WD
chmod +x "$APP/watchdog.sh"

cat > /etc/systemd/system/custom-panel-watchdog.service <<EOF
[Unit]
Description=Custom Panel watchdog (one-shot health check)

[Service]
Type=oneshot
ExecStart=$APP/watchdog.sh
EOF

cat > /etc/systemd/system/custom-panel-watchdog.timer <<'EOF'
[Unit]
Description=Run Custom Panel watchdog every 30s

[Timer]
OnBootSec=60
OnUnitActiveSec=30
AccuracySec=5

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl reset-failed custom-panel-helper custom-panel-proxy custom-panel-accounting custom-panel 2>/dev/null || true
systemctl enable custom-panel-sshd custom-panel-helper custom-panel-proxy custom-panel-accounting custom-panel custom-panel-watchdog.timer >/dev/null

systemctl start custom-panel-sshd
sleep 1
systemctl start custom-panel-helper
sleep 1
systemctl start custom-panel-proxy
sleep 2
ss -lnt | grep -q "127.0.0.1:2222" || {
  journalctl -u custom-panel-sshd -n 120 --no-pager || true
  echo "Internal OpenSSH is not listening on localhost:2222."
  exit 1
}
test -f /run/custom-panel/live.json || {
  journalctl -u custom-panel-proxy -n 120 --no-pager || true
  echo "Live accounting snapshot was not created."
  exit 1
}
systemctl start custom-panel-accounting
systemctl start custom-panel
systemctl start custom-panel-watchdog.timer
sleep 3

for service in ssh custom-panel-sshd custom-panel-helper custom-panel-proxy custom-panel-accounting custom-panel; do
  if ! systemctl is-active --quiet "$service"; then
    journalctl -u "$service" -n 120 --no-pager || true
    echo "Service failed: $service"
    exit 1
  fi
done

curl -kfsS --max-time 10 "https://127.0.0.1:$PANEL_PORT/login" >/dev/null
runuser -u custompanel -- env PYTHONPATH="$APP" \
  CUSTOM_PANEL_SECRET_KEY="$SECRET" \
  CUSTOM_PANEL_ADMIN_USERNAME="admin" \
  CUSTOM_PANEL_ADMIN_PASSWORD_HASH="$ADMIN_HASH" \
  CUSTOM_PANEL_DATA_KEY="$DATA_KEY" \
  CUSTOM_PANEL_DB="$APP/data/panel.db" \
  CUSTOM_PANEL_SERVER_HOST="$SERVER_HOST" \
  CUSTOM_PANEL_INTERNAL_SSH_PORT="2222" \
  CUSTOM_PANEL_TCP_PORT_START="20000" \
  CUSTOM_PANEL_TCP_PORT_END="24999" \
  CUSTOM_PANEL_WS_PORT_START="25000" \
  CUSTOM_PANEL_WS_PORT_END="29999" \
  CUSTOM_PANEL_HELPER_SOCKET="/run/custom-panel/helper.sock" \
  CUSTOM_PANEL_LIVE_PATH="/run/custom-panel/live.json" \
  "$APP/venv/bin/python" -c "from app.db import init_db,connect; from app.config import Config; init_db(Config.DB_PATH); c=connect().__enter__(); c.execute('PRAGMA wal_checkpoint(PASSIVE)'); c.close()"
ss -lnt | grep -qE "[:.]$PANEL_PORT[[:space:]]"

echo "Installed: https://$DOMAIN:$PANEL_PORT"
echo "Credentials: $APP/admin-credentials.txt"
echo "Show credentials: sudo bash $APP/show-credentials.sh"
