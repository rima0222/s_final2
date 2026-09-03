import base64
import json
import os
import subprocess
from pathlib import Path
from flask import current_app

from .db import connect

def _run(args, check=True):
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result

def active_clients():
    with connect() as conn:
        rows = conn.execute("""
        SELECT xray_uuid,xray_email
        FROM users
        WHERE xray_enabled=1 AND paused=0 AND status='Active'
          AND xray_uuid IS NOT NULL AND xray_email IS NOT NULL
        ORDER BY id
        """).fetchall()
    return [
        {"id": row["xray_uuid"], "alterId": 0, "email": row["xray_email"], "level": 0}
        for row in rows
    ]

def config_object():
    port = current_app.config["XRAY_PORT"]
    return {
        "log": {
            "loglevel": "warning",
            "access": "/var/log/xray/access.log",
            "error": "/var/log/xray/error.log",
        },
        "api": {
            "tag": "api",
            "services": ["StatsService", "HandlerService"],
        },
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                    "statsUserOnline": True,
                    "connIdle": 300,
                    "handshake": 4,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
            },
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 10085,
                "protocol": "tunnel",
                "settings": {"rewriteAddress": "127.0.0.1"},
            },
            {
                "tag": "vmess-in",
                "listen": "0.0.0.0",
                "port": port,
                "protocol": "vmess",
                "settings": {"clients": active_clients()},
                "streamSettings": {"network": "tcp", "security": "none"},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            },
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}
            ]
        },
    }

def regenerate():
    path = Path(current_app.config["XRAY_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config_object(), indent=2))
    os.chmod(tmp, 0o600)

    _run(["/usr/local/bin/xray", "run", "-test", "-config", str(tmp)])
    os.replace(tmp, path)
    _run(["systemctl", "restart", "xray"])

def vmess_uri(user):
    data = {
        "v": "2",
        "ps": user["username"],
        "add": current_app.config["SERVER_HOST"],
        "port": str(current_app.config["XRAY_PORT"]),
        "id": user["xray_uuid"],
        "aid": "0",
        "scy": "auto",
        "net": "tcp",
        "type": "none",
        "host": "",
        "path": "",
        "tls": "",
        "sni": "",
        "alpn": "",
        "fp": "",
    }
    encoded = base64.b64encode(
        json.dumps(data, separators=(",", ":")).encode()
    ).decode()
    return "vmess://" + encoded
