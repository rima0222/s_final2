import json
import os
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from .auth import login_required
from .db import connect
from .users import human_bytes

api_bp = Blueprint("api", __name__, url_prefix="/api")

def memory_percent():
    values = {}
    for line in open("/proc/meminfo"):
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0])
    return round((1 - values["MemAvailable"] / values["MemTotal"]) * 100, 1)

def live_state():
    path = Path(current_app.config["LIVE_PATH"])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(time.time()) - int(payload.get("updated_at", 0)) > 5:
            return {}
        return payload.get("users", {})
    except Exception:
        return {}

@api_bp.get("/stats")
@login_required
def stats():
    with connect() as conn:
        users = [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY id DESC")]

    live = live_state()
    response_users = {}
    total_used = 0
    online_users = 0

    for user in users:
        current = live.get(user["username"], {})
        pending_rx = int(current.get("pending_rx", 0))
        pending_tx = int(current.get("pending_tx", 0))
        used = int(user["rx_bytes"] or 0) + int(user["tx_bytes"] or 0) + pending_rx + pending_tx

        tcp_online = int(current.get("tcp_online", user["online_tcp"] or 0)) > 0
        ws_online = int(current.get("ws_online", user["online_ws"] or 0)) > 0
        if tcp_online or ws_online:
            online_users += 1

        total_used += used
        limit_bytes = int(user["limit_bytes"] or 0)
        response_users[user["username"]] = {
            "used": human_bytes(used),
            "quota": human_bytes(limit_bytes) if limit_bytes > 0 else "نامحدود",
            "used_bytes": used,
            "limit_bytes": limit_bytes,
            "online_tcp": tcp_online,
            "online_ws": ws_online,
            "remaining_days": int(user["remaining_days"] or 0),
        }

    return jsonify({
        "total_users": len(users),
        "active_users": sum(1 for user in users if not user["paused"]),
        "online_users": online_users,
        "total_limit_gb": round(
            sum(int(user["limit_bytes"] or 0) for user in users) / 1024**3, 2
        ),
        "total_used": human_bytes(total_used),
        "memory_percent": memory_percent(),
        "load": round(os.getloadavg()[0], 2),
        "gateway_live": bool(live),
        "users": response_users,
    })
