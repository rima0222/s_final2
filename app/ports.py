from flask import current_app
from .db import connect

def _allocate(column, start, end):
    with connect() as conn:
        used = {int(row[column]) for row in conn.execute(
            f"SELECT {column} FROM users WHERE {column} IS NOT NULL"
        )}
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise RuntimeError("ظرفیت پورت‌ها تکمیل شده است.")

def allocate_tcp():
    return _allocate("tcp_port", current_app.config["TCP_PORT_START"], current_app.config["TCP_PORT_END"])

def allocate_ws():
    return _allocate("ws_port", current_app.config["WS_PORT_START"], current_app.config["WS_PORT_END"])
