import time
from collections import defaultdict, deque
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .config import Config
from .db import connect
from .helper_client import panel_action
from .security import validate_csrf

auth_bp = Blueprint("auth", __name__)
_ATTEMPTS = defaultdict(deque)
WINDOW = 300
MAX_ATTEMPTS = 8

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped

def client_key():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()

def limited(key):
    now = time.time()
    queue = _ATTEMPTS[key]
    while queue and now - queue[0] > WINDOW:
        queue.popleft()
    return len(queue) >= MAX_ATTEMPTS

def credentials():
    with connect() as conn:
        row = conn.execute("SELECT username,password_hash FROM admin_settings WHERE id=1").fetchone()
    if row:
        return row["username"], row["password_hash"]
    return current_app.config["ADMIN_USERNAME"], current_app.config["ADMIN_PASSWORD_HASH"]

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    key = client_key()
    if request.method == "POST":
        if limited(key):
            return render_template("login.html", error="تلاش ورود بیش از حد است. چند دقیقه بعد دوباره امتحان کن."), 429
        username, password_hash = credentials()
        valid = (
            request.form.get("username") == username
            and check_password_hash(password_hash, request.form.get("password", ""))
        )
        if valid:
            _ATTEMPTS.pop(key, None)
            session.clear()
            session["admin"] = True
            return redirect(url_for("users.index"))
        _ATTEMPTS[key].append(time.time())
        error = "نام کاربری یا رمز عبور اشتباه است."
    return render_template("login.html", error=error)

@auth_bp.post("/admin/credentials")
@login_required
def change_credentials():
    validate_csrf()
    username = request.form.get("admin_username", "").strip()
    password = request.form.get("admin_password", "")
    if not username or len(password) < 10:
        flash("نام مدیر لازم است و رمز باید حداقل ۱۰ کاراکتر باشد.", "error")
        return redirect(url_for("users.index"))
    with connect() as conn:
        conn.execute("""
        INSERT INTO admin_settings(id,username,password_hash)
        VALUES(1,?,?)
        ON CONFLICT(id) DO UPDATE SET
          username=excluded.username,
          password_hash=excluded.password_hash
        """, (username, generate_password_hash(password)))
        conn.commit()
    session.clear()
    flash("اطلاعات مدیر تغییر کرد؛ دوباره وارد شو.", "success")
    return redirect(url_for("auth.login"))

@auth_bp.post("/admin/port")
@login_required
def change_port():
    validate_csrf()
    try:
        port = int(request.form.get("panel_port", ""))
        if not (1024 <= port <= 65535):
            raise ValueError("پورت باید بین 1024 تا 65535 باشد.")
        panel_action("panel.set_port", port=port)
        flash(
            f"پورت پنل به {port} تغییر کرد. حدود ۲ ثانیه صبر کن و بعد با آدرس "
            f"جدید (پورت {port}) دوباره وارد شو.",
            "success",
        )
    except Exception as exc:
        flash(f"خطا: {exc}", "error")
    return redirect(url_for("users.index"))

@auth_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
