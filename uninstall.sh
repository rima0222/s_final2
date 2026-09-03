#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

for service in custom-panel-web custom-panel-gateway custom-panel-manager custom-panel-helper custom-panel-sshd; do
  systemctl disable --now "$service" 2>/dev/null || true
  rm -f "/etc/systemd/system/$service.service"
done

managed_users="$(
  {
    getent group cpusers | awk -F: '{gsub(/,/, "\n", $4); print $4}'
    getent group panelusers | awk -F: '{gsub(/,/, "\n", $4); print $4}'
    getent passwd | awk -F: '$5=="custom-panel-managed"{print $1}'
  } | sed '/^$/d' | sort -u
)"

while read -r user; do
  [[ -z "$user" ]] && continue
  pkill -KILL -u "$user" 2>/dev/null || true
  userdel -r "$user" 2>/dev/null || true
done <<< "$managed_users"

groupdel cpusers 2>/dev/null || true
groupdel panelusers 2>/dev/null || true
userdel custompanel 2>/dev/null || true
groupdel custompanel 2>/dev/null || true

rm -f /etc/ssh/sshd_config.d/98-custom-panel-deny.conf
rm -f /etc/ssh/sshd_config_custom_panel
rm -f /etc/pam.d/custom-panel-sshd
rm -rf /opt/custom-panel /var/lib/custom-panel /run/custom-panel /etc/custom-panel
rm -f /etc/tmpfiles.d/custom-panel.conf
systemctl daemon-reload
/usr/sbin/sshd -t && systemctl restart ssh
echo "Custom Panel removed."
