#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

PEERS = Path('/etc/wireguard/custom-panel-peers.json')
INTERFACE = 'wg0'

try:
    data = json.loads(PEERS.read_text())
except Exception:
    data = {}

for peer in data.values():
    pub = peer.get('public_key')
    address = peer.get('address')
    if not pub or not address:
        continue
    subprocess.run(['wg', 'set', INTERFACE, 'peer', pub, 'allowed-ips', address + '/32'], check=False)
