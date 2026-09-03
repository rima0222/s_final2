import secrets
from io import BytesIO

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .config import Config
from .crypto import decrypt, encrypt
from .db import connect
from .helper_client import account_action
from .ports import allocate_tcp, allocate_ws
from .security import validate_csrf

users_bp = Blueprint("users", __name__)

def get_user(username):
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None

def human_bytes(value):
    size = float(int(value or 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024

def list_users():
    with connect() as conn:
        users = [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY id DESC")]
    for user in users:
        used = int(user["rx_bytes"] or 0) + int(user["tx_bytes"] or 0)
        user["used_bytes"] = used
        user["used_human"] = human_bytes(used)
        user["limit_gb"] = int(user["limit_bytes"] or 0) / 1024**3
        user["percent"] = min(
            100,
            used / int(user["limit_bytes"]) * 100
            if int(user["limit_bytes"] or 0) > 0 else 0,
        )
    return users

@users_bp.get("/")
@login_required
def index():
    return render_template("index.html", users=list_users(), panel_port=Config.PANEL_PORT)

@users_bp.post("/users")
@login_required
def add():
    validate_csrf()
    try:
        username = request.form["username"].strip()
        password = request.form["password"]
        limit_gb = float(request.form["limit_gb"])
        days = int(request.form["remaining_days"])
        methods = set(request.form.getlist("methods"))
        tcp_enabled = int("tcp" in methods)
        ws_enabled = int("ws" in methods)

        if not tcp_enabled and not ws_enabled:
            raise ValueError("حداقل یک روش اتصال انتخاب کن.")
        if limit_gb < 0 or days < 1:
            raise ValueError("حجم یا زمان نامعتبر است.")
        if get_user(username):
            raise ValueError("این نام کاربری قبلاً وجود دارد.")

        account_action("upsert", username, password)

        with connect() as conn:
            conn.execute("""
            INSERT INTO users(
              username,password_enc,tcp_enabled,ws_enabled,
              tcp_port,ws_port,ws_token,limit_bytes,remaining_days
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                username,
                encrypt(password),
                tcp_enabled,
                ws_enabled,
                allocate_tcp() if tcp_enabled else None,
                allocate_ws() if ws_enabled else None,
                secrets.token_urlsafe(18) if ws_enabled else None,
                int(limit_gb * 1024**3),
                days,
            ))
            conn.commit()
        flash("کاربر ساخته شد.", "success")
    except Exception as exc:
        flash(f"خطا: {exc}", "error")
    return redirect(url_for("users.index"))

@users_bp.post("/users/<username>/edit")
@login_required
def edit(username):
    validate_csrf()
    user = get_user(username)
    if not user:
        return redirect(url_for("users.index"))
    try:
        password = request.form.get("password") or decrypt(user["password_enc"])
        limit_gb = float(request.form.get("limit_gb", int(user["limit_bytes"])/1024**3))
        days = int(request.form.get("remaining_days", user["remaining_days"]))
        methods = set(request.form.getlist("methods"))
        tcp_enabled = int("tcp" in methods)
        ws_enabled = int("ws" in methods)

        if not tcp_enabled and not ws_enabled:
            raise ValueError("حداقل یک روش اتصال انتخاب کن.")
        if limit_gb < 0 or days < 0:
            raise ValueError("مقادیر نامعتبر هستند.")

        account_action("upsert", username, password)

        tcp_port = user["tcp_port"] if tcp_enabled and user["tcp_port"] else (allocate_tcp() if tcp_enabled else None)
        ws_port = user["ws_port"] if ws_enabled and user["ws_port"] else (allocate_ws() if ws_enabled else None)
        ws_token = user["ws_token"] if ws_enabled and user["ws_token"] else (secrets.token_urlsafe(18) if ws_enabled else None)

        with connect() as conn:
            conn.execute("""
            UPDATE users SET
              password_enc=?,tcp_enabled=?,ws_enabled=?,
              tcp_port=?,ws_port=?,ws_token=?,
              limit_bytes=?,remaining_days=?,
              status=CASE WHEN paused=0 AND ?>0 THEN 'Active' ELSE status END,
              updated_at=CURRENT_TIMESTAMP
            WHERE username=?
            """, (
                encrypt(password), tcp_enabled, ws_enabled,
                tcp_port, ws_port, ws_token,
                int(limit_gb * 1024**3), days, days, username,
            ))
            conn.commit()
        flash("ویرایش ذخیره شد.", "success")
    except Exception as exc:
        flash(f"خطا: {exc}", "error")
    return redirect(url_for("users.index"))

@users_bp.post("/users/<username>/<action>")
@login_required
def action(username, action):
    validate_csrf()
    user = get_user(username)
    if not user:
        return redirect(url_for("users.index"))
    try:
        if action == "pause":
            account_action("pause", username)
            with connect() as conn:
                conn.execute("""
                UPDATE users SET paused=1,status='Paused',online_tcp=0,online_ws=0
                WHERE username=?
                """, (username,))
                conn.commit()

        elif action == "resume":
            if int(user["remaining_days"]) <= 0:
                raise ValueError("ابتدا زمان باقی‌مانده را افزایش بده.")
            account_action("resume", username)
            with connect() as conn:
                conn.execute(
                    "UPDATE users SET paused=0,status='Active' WHERE username=?",
                    (username,),
                )
                conn.commit()

        elif action == "reset-traffic":
            with connect() as conn:
                conn.execute("DELETE FROM endpoint_usage WHERE user_id=?", (user["id"],))
                conn.execute("""
                UPDATE users SET rx_bytes=0,tx_bytes=0,online_tcp=0,online_ws=0
                WHERE id=?
                """, (user["id"],))
                conn.commit()

        elif action == "extend":
            add_days = int(request.form.get("days", 0) or 0)
            if add_days <= 0:
                raise ValueError("تعداد روز باید مثبت باشد.")
            was_expired = user["status"] == "Expired"
            with connect() as conn:
                conn.execute("""
                UPDATE users SET remaining_days=remaining_days+?,
                    status=CASE WHEN status='Expired' THEN 'Active' ELSE status END
                WHERE username=?
                """, (add_days, username))
                conn.commit()
            if was_expired and not user["paused"]:
                account_action("resume", username)

        elif action == "delete":
            account_action("delete", username)
            with connect() as conn:
                conn.execute("DELETE FROM users WHERE username=?", (username,))
                conn.commit()

        flash("عملیات انجام شد.", "success")
    except Exception as exc:
        flash(f"خطا: {exc}", "error")
    return redirect(url_for("users.index"))

@users_bp.get("/users/export.csv")
@login_required
def export_csv():
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["username", "status", "tcp_port", "ws_port", "used_GB", "limit_GB", "remaining_days"])
    for user in list_users():
        writer.writerow([
            user["username"], user["status"], user["tcp_port"], user["ws_port"],
            round(user["used_bytes"] / 1024**3, 3), round(user["limit_gb"], 3), user["remaining_days"],
        ])
    payload = buffer.getvalue().encode("utf-8-sig")
    return send_file(
        BytesIO(payload), mimetype="text/csv", as_attachment=True,
        download_name="users.csv",
    )

@users_bp.get("/users/<username>/config")
@login_required
def config(username):
    user = get_user(username)
    if not user:
        return ("Not found", 404)

    host = current_app.config["SERVER_HOST"]
    tls_active = bool(Config.DOMAIN and Config.TLS_CERT and Config.TLS_KEY)
    ws_host = Config.DOMAIN if tls_active else host
    ws_scheme = "wss" if tls_active else "ws"
    lines = [
        f"Username: {user['username']}",
        f"Password: {decrypt(user['password_enc'])}",
        f"Remaining days: {user['remaining_days']}",
        "",
    ]
    if user["tcp_enabled"]:
        lines += ["OpenSSH TCP", f"Host: {host}", f"Port: {user['tcp_port']}", ""]
    if user["ws_enabled"]:
        lines += [
            "SSH WebSocket" + (" (TLS, looks like normal HTTPS to DPI)" if tls_active else ""),
            f"URL: {ws_scheme}://{ws_host}:{user['ws_port']}/ws/{user['ws_token']}",
            f"Host: {ws_host}",
            f"Port: {user['ws_port']}",
            f"Path: /ws/{user['ws_token']}",
            "",
        ]

    return send_file(
        BytesIO("\n".join(lines).encode()),
        as_attachment=True,
        download_name=f"{username}-ssh-config.txt",
        mimetype="text/plain",
    )
