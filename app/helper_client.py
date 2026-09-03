import json
import socket
from flask import current_app

def _call_helper(payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(20)
        sock.connect(current_app.config["HELPER_SOCKET"])
        sock.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk

    result = json.loads(data.decode() or "{}")
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Account helper failed"))

def account_action(action, username, password=None):
    payload = {"action": action, "username": username}
    if password is not None:
        payload["password"] = password
    _call_helper(payload)

def panel_action(action, **params):
    payload = {"action": action}
    payload.update(params)
    _call_helper(payload)
