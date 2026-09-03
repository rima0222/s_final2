import datetime as dt
import json
import logging
import socket
import time
from pathlib import Path

from .config import Config
from .db import connect, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("accounting")

def helper_pause(username):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(15)
        sock.connect(Config.HELPER_SOCKET)
        sock.sendall((json.dumps({"action": "pause", "username": username}) + "\n").encode())
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            response += chunk
    result = json.loads(response.decode())
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "helper failed"))

def live_usage():
    try:
        payload = json.loads(Path(Config.LIVE_PATH).read_text(encoding="utf-8"))
        if int(time.time()) - int(payload.get("updated_at", 0)) > 5:
            return {}
        return payload.get("users", {})
    except Exception:
        return {}

def apply_rollover():
    today = dt.date.today()
    with connect() as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key='last_rollover'").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO metadata(key,value) VALUES('last_rollover',?)",
                (today.isoformat(),),
            )
            conn.commit()
            return

        last = dt.date.fromisoformat(row["value"])
        elapsed = (today - last).days
        if elapsed <= 0:
            return

        conn.execute("""
        UPDATE users
        SET remaining_days=MAX(0,remaining_days-?),
            updated_at=CURRENT_TIMESTAMP
        """, (elapsed,))
        conn.execute(
            "UPDATE metadata SET value=? WHERE key='last_rollover'",
            (today.isoformat(),),
        )
        conn.commit()

def enforce():
    live = live_usage()
    with connect() as conn:
        users = [dict(row) for row in conn.execute("SELECT * FROM users")]
        for user in users:
            current = live.get(user["username"], {})
            pending = int(current.get("pending_rx", 0)) + int(current.get("pending_tx", 0))
            used = int(user["rx_bytes"] or 0) + int(user["tx_bytes"] or 0) + pending
            over = int(user["limit_bytes"] or 0) > 0 and used >= int(user["limit_bytes"])
            expired = int(user["remaining_days"] or 0) <= 0

            if not user["paused"] and (over or expired):
                try:
                    helper_pause(user["username"])
                except Exception:
                    log.exception("Could not pause %s", user["username"])
                conn.execute("""
                UPDATE users
                SET paused=1,status='Expired',online_tcp=0,online_ws=0
                WHERE id=?
                """, (user["id"],))
        conn.commit()

def main():
    init_db(Config.DB_PATH)
    while True:
        try:
            apply_rollover()
            enforce()
        except Exception:
            log.exception("Accounting cycle failed")
        time.sleep(5)

if __name__ == "__main__":
    main()
