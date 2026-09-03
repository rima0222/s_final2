#!/usr/bin/env bash
# Client-side kill switch for a Custom Panel SSH account (Linux only).
#
# A kill switch is inherently a CLIENT-side control — the server has no way
# to stop a client's own internet access when the tunnel drops. This script
# runs on the customer's machine/router: it keeps the SSH tunnel alive with
# autossh, and firewalls the box so that if the tunnel goes down, normal
# internet traffic is blocked instead of silently leaking outside the tunnel
# — the same idea as a VPN app's kill switch.
#
# Usage:
#   sudo SSH_HOST=panel.example.com SSH_PORT=20001 SSH_USER=myuser \
#        bash client-kill-switch-linux.sh
#
# What it sets up:
#   - autossh dynamic SOCKS5 proxy on 127.0.0.1:1080, auto-reconnecting
#   - redsocks, transparently routing all outbound TCP through that SOCKS
#     proxy (so you don't need to configure SOCKS in every app)
#   - iptables rules that only allow: loopback, DNS, and traffic to
#     $SSH_HOST:$SSH_PORT itself — everything else is dropped, so if the
#     tunnel dies, you go offline instead of leaking your real IP.
#
# To remove everything: bash client-kill-switch-linux.sh --uninstall

set -Eeuo pipefail
[[ "${EUID}" -eq 0 ]] || { echo "Run as root."; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now panel-tunnel.service redsocks 2>/dev/null || true
  rm -f /etc/systemd/system/panel-tunnel.service
  iptables -t nat -F PANEL_KILLSWITCH 2>/dev/null || true
  iptables -t nat -D OUTPUT -j PANEL_KILLSWITCH 2>/dev/null || true
  iptables -t nat -X PANEL_KILLSWITCH 2>/dev/null || true
  iptables -F PANEL_KILLSWITCH_OUT 2>/dev/null || true
  iptables -D OUTPUT -j PANEL_KILLSWITCH_OUT 2>/dev/null || true
  iptables -X PANEL_KILLSWITCH_OUT 2>/dev/null || true
  echo "Kill switch removed, normal internet access restored."
  exit 0
fi

SSH_HOST="${SSH_HOST:?set SSH_HOST to your panel domain or IP}"
SSH_PORT="${SSH_PORT:?set SSH_PORT to the TCP port from the Config button}"
SSH_USER="${SSH_USER:?set SSH_USER to the panel username}"

apt-get update -y
apt-get install -y autossh redsocks sshpass iptables

echo "SSH password for $SSH_USER@$SSH_HOST:$SSH_PORT (used once, not stored):"
read -rs SSH_PASSWORD
export SSHPASS="$SSH_PASSWORD"

mkdir -p /etc/panel-tunnel
sshpass -e ssh-keyscan -p "$SSH_PORT" "$SSH_HOST" > /etc/panel-tunnel/known_hosts 2>/dev/null || true

cat > /etc/panel-tunnel/askpass.sh <<EOF
#!/bin/sh
echo "$SSH_PASSWORD"
EOF
chmod 700 /etc/panel-tunnel/askpass.sh

cat > /etc/systemd/system/panel-tunnel.service <<EOF
[Unit]
Description=Custom Panel SSH tunnel (SOCKS5 on 127.0.0.1:1080)
After=network-online.target
Wants=network-online.target

[Service]
Environment=SSH_ASKPASS=/etc/panel-tunnel/askpass.sh
Environment=DISPLAY=:0
Environment=AUTOSSH_GATETIME=0
ExecStart=/usr/bin/autossh -M 0 -N -D 127.0.0.1:1080 \
  -o "UserKnownHostsFile=/etc/panel-tunnel/known_hosts" \
  -o "ServerAliveInterval=10" -o "ServerAliveCountMax=3" \
  -o "ExitOnForwardFailure=yes" -o "SetupTimeout=10" \
  -o "PreferredAuthentications=password" \
  -p $SSH_PORT $SSH_USER@$SSH_HOST
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/redsocks.conf <<'EOF'
base {
  log_debug = off;
  log_info = off;
  log = "syslog:daemon";
  daemon = off;
  redirector = iptables;
}
redsocks {
  local_ip = 127.0.0.1;
  local_port = 12345;
  ip = 127.0.0.1;
  port = 1080;
  type = socks5;
}
EOF

# --- Kill switch: only loopback, DNS, and the SSH server itself may leave
# this box directly; everything else is transparently redirected into the
# tunnel, and if the tunnel is down, that traffic is simply dropped.
iptables -t nat -N PANEL_KILLSWITCH 2>/dev/null || true
iptables -t nat -F PANEL_KILLSWITCH
iptables -t nat -A PANEL_KILLSWITCH -p tcp -d "$SSH_HOST" --dport "$SSH_PORT" -j RETURN
iptables -t nat -A PANEL_KILLSWITCH -d 127.0.0.0/8 -j RETURN
iptables -t nat -A PANEL_KILLSWITCH -p tcp -j REDIRECT --to-ports 12345
iptables -t nat -D OUTPUT -j PANEL_KILLSWITCH 2>/dev/null || true
iptables -t nat -A OUTPUT -j PANEL_KILLSWITCH

iptables -N PANEL_KILLSWITCH_OUT 2>/dev/null || true
iptables -F PANEL_KILLSWITCH_OUT
iptables -A PANEL_KILLSWITCH_OUT -o lo -j ACCEPT
iptables -A PANEL_KILLSWITCH_OUT -p udp --dport 53 -j ACCEPT
iptables -A PANEL_KILLSWITCH_OUT -p tcp -d "$SSH_HOST" --dport "$SSH_PORT" -j ACCEPT
iptables -A PANEL_KILLSWITCH_OUT -p tcp --dport 12345 -j ACCEPT
iptables -A PANEL_KILLSWITCH_OUT -j DROP
iptables -D OUTPUT -j PANEL_KILLSWITCH_OUT 2>/dev/null || true
iptables -A OUTPUT -j PANEL_KILLSWITCH_OUT

systemctl daemon-reload
systemctl enable --now redsocks
systemctl enable --now panel-tunnel.service

echo "Done. All outbound traffic now routes through the tunnel to $SSH_HOST."
echo "If the tunnel drops, traffic is blocked (kill switch), not leaked in the clear."
echo "To remove: sudo bash $0 --uninstall"
