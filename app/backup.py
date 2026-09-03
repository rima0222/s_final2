import json
from datetime import datetime, timezone
from io import BytesIO

from flask import Blueprint, flash, redirect, request, send_file, url_for

from .auth import login_required
from .crypto import decrypt, encrypt
from .db import connect
from .helper_client import account_action
from .security import validate_csrf

backup_bp = Blueprint("backup", __name__, url_prefix="/backup")

@backup_bp.get("/download")
@login_required
def download():
    with connect() as conn:
        users = [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY id")]
        usage = [dict(row) for row in conn.execute("SELECT * FROM endpoint_usage")]
    for user in users:
        user["password"] = decrypt(user.pop("password_enc"))
    payload = {
        "format": "custom-panel-openssh-ws",
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "users": users,
        "endpoint_usage": usage,
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    return send_file(
        BytesIO(data),
        as_attachment=True,
        download_name="custom-panel-backup.json",
        mimetype="application/json",
    )

@backup_bp.post("/restore")
@login_required
def restore():
    validate_csrf()
    try:
        uploaded = request.files.get("backup_file")
        if not uploaded:
            raise ValueError("فایل بکاپ انتخاب نشده است.")
        payload = json.load(uploaded.stream)
        users = payload.get("users", [])
        usage = payload.get("endpoint_usage", [])

        with connect() as conn:
            for user in users:
                account_action("upsert", user["username"], user["password"])
                conn.execute("""
                INSERT INTO users(
                  id,username,password_enc,tcp_enabled,ws_enabled,
                  tcp_port,ws_port,ws_token,limit_bytes,remaining_days,
                  paused,status,rx_bytes,tx_bytes,online_tcp,online_ws,last_seen
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)
                ON CONFLICT(username) DO UPDATE SET
                  password_enc=excluded.password_enc,
                  tcp_enabled=excluded.tcp_enabled,
                  ws_enabled=excluded.ws_enabled,
                  tcp_port=excluded.tcp_port,
                  ws_port=excluded.ws_port,
                  ws_token=excluded.ws_token,
                  limit_bytes=excluded.limit_bytes,
                  remaining_days=excluded.remaining_days,
                  paused=excluded.paused,
                  status=excluded.status,
                  rx_bytes=excluded.rx_bytes,
                  tx_bytes=excluded.tx_bytes,
                  online_tcp=0,
                  online_ws=0,
                  last_seen=excluded.last_seen
                """, (
                    user.get("id"), user["username"], encrypt(user["password"]),
                    user.get("tcp_enabled", 1), user.get("ws_enabled", 1),
                    user.get("tcp_port"), user.get("ws_port"), user.get("ws_token"),
                    user.get("limit_bytes", 0), user.get("remaining_days", 0),
                    user.get("paused", 0), user.get("status", "Active"),
                    user.get("rx_bytes", 0), user.get("tx_bytes", 0),
                    user.get("last_seen", 0),
                ))
            conn.execute("DELETE FROM endpoint_usage")
            for item in usage:
                conn.execute("""
                INSERT OR REPLACE INTO endpoint_usage(
                  user_id,endpoint,rx_bytes,tx_bytes,online,last_seen
                ) VALUES(?,?,?,?,0,?)
                """, (
                    item["user_id"], item["endpoint"],
                    item.get("rx_bytes", 0), item.get("tx_bytes", 0),
                    item.get("last_seen", 0),
                ))
            conn.commit()
        flash("بکاپ بازیابی شد.", "success")
    except Exception as exc:
        flash(f"خطا: {exc}", "error")
    return redirect(url_for("users.index"))
