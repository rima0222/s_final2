import fcntl
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

LOCK = Path("/run/lock/custom-panel-linux-users.lock")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")

@contextmanager
def account_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def run(args, input_text=None, check=True):
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result

def validate(name):
    if not USER_RE.fullmatch(name):
        raise ValueError("نام کاربری لینوکس نامعتبر است.")

def exists(name):
    return run(["getent", "passwd", name], check=False).returncode == 0

def create_or_update(name, password):
    validate(name)
    with account_lock():
        if not exists(name):
            run(["useradd", "-M", "-N", "-s", "/usr/sbin/nologin", name])
        else:
            run(["usermod", "-s", "/usr/sbin/nologin", name])
        run(["chpasswd"], f"{name}:{password}\n")
        run(["usermod", "-U", name], check=False)

def pause(name):
    validate(name)
    with account_lock():
        run(["usermod", "-L", name], check=False)
    run(["pkill", "-KILL", "-u", name], check=False)

def resume(name):
    validate(name)
    with account_lock():
        run(["usermod", "-U", name], check=False)

def delete(name):
    validate(name)
    run(["pkill", "-KILL", "-u", name], check=False)
    with account_lock():
        run(["userdel", "-r", name], check=False)
