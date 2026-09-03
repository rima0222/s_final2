#!/bin/bash
systemctl stop custom-panel custom-panel-agent 2>/dev/null || true
systemctl disable custom-panel custom-panel-agent 2>/dev/null || true
rm -f /etc/systemd/system/custom-panel*.service
rm -rf /opt/custom-panel
rm -rf /var/lib/custom-panel
rm -rf /run/custom-panel
rm -rf /etc/custom-panel
systemctl daemon-reload
echo 'Removed'
