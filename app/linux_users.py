import fcntl
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path("/run/lock/custom-panel-passwd.lock")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")

@contextmanager
def passwd_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

def command(args, input_text=None, check=True):
    last = None
    for attempt in range(4):
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 or not check:
            return result
        last = result
        message = ((result.stderr or "") + (result.stdout or "")).lower()
        if "cannot lock" not in message and "failure while writing" not in message:
            break
        time.sleep(1 + attempt)
    raise RuntimeError((last.stderr or last.stdout).strip() if last else "Linux user command failed")

def validate(username):
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("نام کاربری فقط باید شامل حروف کوچک انگلیسی، عدد، خط تیره یا زیرخط باشد.")

def exists(username):
    return command(["getent", "passwd", username], check=False).returncode == 0

def create_or_update(username, password):
    validate(username)
    with passwd_lock():
        if not exists(username):
            command([
                "useradd", "-M", "-N", "-G", "panelusers",
                "-s", "/usr/local/bin/panel-hold", username
            ])
        else:
            command([
                "usermod", "-a", "-G", "panelusers",
                "-s", "/usr/local/bin/panel-hold", username
            ])
        command(["chpasswd"], f"{username}:{password}\n")
        command(["usermod", "-U", username], check=False)

def pause(username):
    validate(username)
    with passwd_lock():
        command(["usermod", "-L", username], check=False)
    command(["pkill", "-KILL", "-u", username], check=False)

def resume(username):
    validate(username)
    with passwd_lock():
        command(["usermod", "-U", username], check=False)

def delete(username):
    validate(username)
    command(["pkill", "-KILL", "-u", username], check=False)
    with passwd_lock():
        command(["userdel", "-r", username], check=False)
