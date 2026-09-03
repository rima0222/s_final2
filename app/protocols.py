import fcntl
import ipaddress
import json
import os
import re
import socket
import subprocess
from pathlib import Path

from flask import current_app

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
ACCOUNT_LOCK = Path("/run/lock/custom-panel-accounts.lock")
PROTOCOL_LOCK = Path("/run/lock/custom-panel-protocols.lock")

class file_lock:
    def __init__(self, path):
        self.path = path
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("a+")
        fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        return self
    def __exit__(self, *_):
        fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()

def run(args, input_text=None, check=True, timeout=30):
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {args[0]}")
    return result

def valid_user(name):
    if not USER_RE.fullmatch(name):
        raise ValueError("نام کاربری نامعتبر است.")

class SSH:
    name = "ssh"
    def create(self, user):
        valid_user(user["username"])
        name = user["username"]
        with file_lock(ACCOUNT_LOCK):
            exists = run(["getent", "passwd", name], check=False).returncode == 0
            if not exists:
                run(["useradd", "-M", "-N", "-s", "/usr/sbin/nologin", name])
            else:
                run(["usermod", "-s", "/usr/sbin/nologin", name])
            run(["chpasswd"], f"{name}:{user['password']}\n")
            run(["usermod", "-U", name])
        return {"identifier": name, "config": {}}
    update = create
    def pause(self, user, meta=None):
        with file_lock(ACCOUNT_LOCK):
            run(["usermod", "-L", user["username"]], check=False)
        run(["pkill", "-KILL", "-u", user["username"]], check=False)
    def resume(self, user, meta=None):
        with file_lock(ACCOUNT_LOCK):
            run(["usermod", "-U", user["username"]], check=False)
    def delete(self, user, meta=None):
        run(["pkill", "-KILL", "-u", user["username"]], check=False)
        with file_lock(ACCOUNT_LOCK):
            run(["userdel", "-r", user["username"]], check=False)
    def client(self, user, meta=None):
        content = (
            f"Host: {current_app.config['SERVER_HOST']}\n"
            f"Port: 22\nUsername: {user['username']}\nPassword: {user['password']}\n"
        )
        return {"filename": f"{user['username']}-ssh.txt", "content": content}

class IKEv2:
    name = "ikev2"
    users_file = Path("/etc/swanctl/conf.d/custom-panel-users.conf")
    state_file = Path("/etc/swanctl/custom-panel-users.json")

    def _all(self):
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return {}

    def _write(self, data):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_file)

        lines = ["secrets {"]
        for name, item in sorted(data.items()):
            if item.get("disabled"):
                continue
            lines += [
                f"  eap-{name} {{",
                f"    id = {name}",
                f"    secret = \"{item['password'].replace(chr(34), '')}\"",
                "  }",
            ]
        lines.append("}")
        tmp_conf = self.users_file.with_suffix(".tmp")
        tmp_conf.write_text("\n".join(lines) + "\n")
        os.chmod(tmp_conf, 0o600)
        os.replace(tmp_conf, self.users_file)
        run(["swanctl", "--load-creds"], check=False)

    def create(self, user):
        valid_user(user["username"])
        with file_lock(PROTOCOL_LOCK):
            data = self._all()
            data[user["username"]] = {"password": user["password"], "disabled": False}
            self._write(data)
        return {"identifier": user["username"], "config": {"eap_id": user["username"]}}

    update = create

    def pause(self, user, meta=None):
        with file_lock(PROTOCOL_LOCK):
            data = self._all()
            if user["username"] in data:
                data[user["username"]]["disabled"] = True
                self._write(data)
        # Reloading credentials prevents new logins. Existing SAs are terminated
        # by connection name; this may disconnect other IKEv2 users too.
        run(["swanctl", "--terminate", "--ike", "custom-panel-eap"], check=False)

    def resume(self, user, meta=None):
        with file_lock(PROTOCOL_LOCK):
            data = self._all()
            if user["username"] in data:
                data[user["username"]]["disabled"] = False
                data[user["username"]]["password"] = user["password"]
                self._write(data)

    def delete(self, user, meta=None):
        with file_lock(PROTOCOL_LOCK):
            data = self._all()
            data.pop(user["username"], None)
            self._write(data)
        run(["swanctl", "--terminate", "--ike", "custom-panel-eap"], check=False)

    def client(self, user, meta=None):
        ca = Path("/etc/swanctl/x509ca/custom-panel-ca.crt")
        content = (
            f"Server: {current_app.config['SERVER_HOST']}\n"
            f"VPN type: IKEv2\n"
            f"Remote ID: {current_app.config['IKE_REMOTE_ID']}\n"
            f"Username: {user['username']}\n"
            f"Password: {user['password']}\n"
            f"CA certificate: /users/{user['username']}/config/ikev2-ca\n"
        )
        return {"filename": f"{user['username']}-ikev2.txt", "content": content}

REGISTRY = {
    "ssh": SSH(),
    "ikev2": IKEv2(),
}
