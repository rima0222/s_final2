import asyncio
import json
import logging
import os
import signal
import socket
import sqlite3
import ssl
import time
from dataclasses import dataclass
from pathlib import Path

import websockets

from .config import Config
from .db import connect, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s proxy_runtime[%(process)d] %(levelname)s %(message)s",
)
log = logging.getLogger("proxy_runtime")

def build_ws_ssl_context():
    """Terminate WebSocket connections with a real (Let's Encrypt) certificate
    so the TLS handshake is indistinguishable from ordinary HTTPS traffic to
    DPI middleboxes. Falls back to plain (unencrypted) WS if no cert was
    provisioned during install (e.g. CUSTOM_PANEL_DOMAIN was not set)."""
    if not (Config.TLS_CERT and Config.TLS_KEY):
        return None
    if not (os.path.exists(Config.TLS_CERT) and os.path.exists(Config.TLS_KEY)):
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(Config.TLS_CERT, Config.TLS_KEY)
    context.set_alpn_protocols(["http/1.1"])
    return context

@dataclass(frozen=True)
class Endpoint:
    user_id: int
    username: str
    kind: str
    port: int
    token: str | None = None

class Runtime:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.state = {}
        self.tcp_servers = {}
        self.ws_servers = {}
        self.stop = asyncio.Event()
        self.live_path = Path(Config.LIVE_PATH)
        self.ws_ssl_context = build_ws_ssl_context()
        self.port_failures = {}

    def desired(self):
        with connect() as conn:
            users = [dict(row) for row in conn.execute("""
            SELECT id,username,tcp_enabled,ws_enabled,tcp_port,ws_port,ws_token
            FROM users
            WHERE paused=0 AND status='Active' AND remaining_days>0
            """)]

        tcp, ws = {}, {}
        for user in users:
            if user["tcp_enabled"] and user["tcp_port"]:
                endpoint = Endpoint(
                    int(user["id"]), user["username"], "tcp", int(user["tcp_port"])
                )
                tcp[endpoint.port] = endpoint
                self.state.setdefault((endpoint.user_id, "tcp"), {
                    "user_id": endpoint.user_id, "username": endpoint.username,
                    "kind": "tcp", "rx_pending": 0, "tx_pending": 0,
                    "rx_live": 0, "tx_live": 0, "online": 0, "last_seen": 0,
                })
            if user["ws_enabled"] and user["ws_port"]:
                endpoint = Endpoint(
                    int(user["id"]), user["username"], "ws",
                    int(user["ws_port"]), user["ws_token"]
                )
                ws[endpoint.port] = endpoint
                self.state.setdefault((endpoint.user_id, "ws"), {
                    "user_id": endpoint.user_id, "username": endpoint.username,
                    "kind": "ws", "rx_pending": 0, "tx_pending": 0,
                    "rx_live": 0, "tx_live": 0, "online": 0, "last_seen": 0,
                })
        return tcp, ws

    async def change(self, endpoint, rx=0, tx=0, online_delta=0):
        async with self.lock:
            key = (endpoint.user_id, endpoint.kind)
            item = self.state.setdefault(key, {
                "user_id": endpoint.user_id,
                "username": endpoint.username,
                "kind": endpoint.kind,
                "rx_pending": 0,
                "tx_pending": 0,
                "rx_live": 0,
                "tx_live": 0,
                "online": 0,
                "last_seen": 0,
            })
            item["rx_pending"] += rx
            item["tx_pending"] += tx
            item["rx_live"] += rx
            item["tx_live"] += tx
            item["online"] = max(0, item["online"] + online_delta)
            if rx or tx or item["online"] > 0:
                item["last_seen"] = int(time.time())

    async def relay(self, reader, writer, endpoint, direction):
        try:
            while True:
                data = await reader.read(131072)
                if not data:
                    return
                writer.write(data)
                await writer.drain()
                if direction == "rx":
                    await self.change(endpoint, rx=len(data))
                else:
                    await self.change(endpoint, tx=len(data))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def tcp_connection(self, client_reader, client_writer, endpoint):
        await self.change(endpoint, online_delta=1)
        try:
            server_reader, server_writer = await asyncio.open_connection(
                "127.0.0.1", Config.INTERNAL_SSH_PORT
            )
            await asyncio.gather(
                self.relay(client_reader, server_writer, endpoint, "rx"),
                self.relay(server_reader, client_writer, endpoint, "tx"),
            )
        except Exception:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass
        finally:
            await self.change(endpoint, online_delta=-1)

    async def ws_connection(self, websocket, endpoint):
        if websocket.request.path != f"/ws/{endpoint.token}":
            await websocket.close(code=1008, reason="invalid path")
            return

        await self.change(endpoint, online_delta=1)
        backend_writer = None
        try:
            backend_reader, backend_writer = await asyncio.open_connection(
                "127.0.0.1", Config.INTERNAL_SSH_PORT
            )

            async def ws_to_ssh():
                async for message in websocket:
                    if isinstance(message, str):
                        message = message.encode()
                    backend_writer.write(message)
                    await backend_writer.drain()
                    await self.change(endpoint, rx=len(message))

            async def ssh_to_ws():
                while True:
                    data = await backend_reader.read(131072)
                    if not data:
                        return
                    await websocket.send(data)
                    await self.change(endpoint, tx=len(data))

            await asyncio.gather(ws_to_ssh(), ssh_to_ws())
        finally:
            await self.change(endpoint, online_delta=-1)
            if backend_writer:
                backend_writer.close()
                try:
                    await backend_writer.wait_closed()
                except Exception:
                    pass

    async def open_tcp(self, endpoint):
        return await asyncio.start_server(
            lambda r, w, ep=endpoint: self.tcp_connection(r, w, ep),
            "0.0.0.0", endpoint.port,
            backlog=256, reuse_address=True,
        )

    async def open_ws(self, endpoint):
        return await websockets.serve(
            lambda ws, ep=endpoint: self.ws_connection(ws, ep),
            "0.0.0.0", endpoint.port,
            max_size=None,
            ping_interval=25,
            ping_timeout=20,
            compression=None,
            ssl=self.ws_ssl_context,
        )

    async def reconcile(self):
        wanted_tcp, wanted_ws = self.desired()

        for port in set(self.tcp_servers) - set(wanted_tcp):
            server = self.tcp_servers.pop(port)
            try:
                server.close()
                await server.wait_closed()
            except Exception:
                log.exception("error closing stale TCP listener on port %s", port)
        for port in set(self.ws_servers) - set(wanted_ws):
            server = self.ws_servers.pop(port)
            try:
                server.close()
                await server.wait_closed()
            except Exception:
                log.exception("error closing stale WS listener on port %s", port)

        # Each port is opened independently: a single stuck/conflicting port
        # (e.g. still held by the OS right after a crash) must never prevent
        # every other user's endpoint from coming up. Previously a single
        # failure here aborted the whole reconcile pass, silently, every
        # cycle, until the box was rebooted.
        for port in set(wanted_tcp) - set(self.tcp_servers):
            try:
                self.tcp_servers[port] = await self.open_tcp(wanted_tcp[port])
                self.port_failures.pop(("tcp", port), None)
            except Exception as exc:
                self._log_port_failure("tcp", port, wanted_tcp[port].username, exc)
        for port in set(wanted_ws) - set(self.ws_servers):
            try:
                self.ws_servers[port] = await self.open_ws(wanted_ws[port])
                self.port_failures.pop(("ws", port), None)
            except Exception as exc:
                self._log_port_failure("ws", port, wanted_ws[port].username, exc)

    def _log_port_failure(self, kind, port, username, exc):
        key = (kind, port)
        count = self.port_failures.get(key, 0) + 1
        self.port_failures[key] = count
        # Log immediately, then only every ~1 minute (30 cycles) after that,
        # so a persistently stuck port doesn't spam the journal.
        if count == 1 or count % 30 == 0:
            log.warning(
                "could not open %s listener on port %s for user %s (attempt %s): %s",
                kind, port, username, count, exc,
            )

    async def reconcile_loop(self):
        while not self.stop.is_set():
            try:
                await self.reconcile()
            except Exception:
                log.exception("reconcile() failed (unexpected — reconcile() should not raise)")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def snapshot(self):
        async with self.lock:
            entries = [dict(value) for value in self.state.values()]

        users = {}
        for item in entries:
            user = users.setdefault(item["username"], {
                "tcp_online": 0,
                "ws_online": 0,
                "pending_rx": 0,
                "pending_tx": 0,
                "live_rx": 0,
                "live_tx": 0,
                "last_seen": 0,
            })
            user[f"{item['kind']}_online"] = item["online"]
            user["pending_rx"] += item["rx_pending"]
            user["pending_tx"] += item["tx_pending"]
            user["live_rx"] += item["rx_live"]
            user["live_tx"] += item["tx_live"]
            user["last_seen"] = max(user["last_seen"], item["last_seen"])

        payload = {
            "updated_at": int(time.time()),
            "users": users,
        }

        self.live_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.live_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.chmod(temp, 0o660)
        os.replace(temp, self.live_path)

    async def flush_db(self):
        async with self.lock:
            pending = []
            for value in self.state.values():
                if value["rx_pending"] or value["tx_pending"]:
                    pending.append((
                        value["rx_pending"],
                        value["tx_pending"],
                        value["online"],
                        value["last_seen"],
                        value["user_id"],
                        value["kind"],
                    ))
                    value["rx_pending"] = 0
                    value["tx_pending"] = 0
                else:
                    pending.append((
                        0, 0, value["online"], value["last_seen"],
                        value["user_id"], value["kind"],
                    ))

        if not pending:
            return

        for attempt in range(5):
            try:
                with connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    for rx, tx, online, last_seen, user_id, kind in pending:
                        online_column = "online_tcp" if kind == "tcp" else "online_ws"
                        conn.execute(f"""
                        UPDATE users
                        SET rx_bytes=rx_bytes+?,
                            tx_bytes=tx_bytes+?,
                            {online_column}=?,
                            last_seen=MAX(last_seen,?),
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """, (rx, tx, online, last_seen, user_id))
                    conn.commit()
                return
            except sqlite3.OperationalError:
                await asyncio.sleep(0.15 * (attempt + 1))
            except Exception:
                await asyncio.sleep(0.15 * (attempt + 1))

        # Restore unsaved byte deltas so they are retried later.
        async with self.lock:
            for rx, tx, _online, _last_seen, user_id, kind in pending:
                item = self.state.get((user_id, kind))
                if item:
                    item["rx_pending"] += rx
                    item["tx_pending"] += tx

    async def snapshot_loop(self):
        while not self.stop.is_set():
            try:
                await self.snapshot()
            except Exception:
                log.exception("snapshot() failed")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def database_loop(self):
        while not self.stop.is_set():
            try:
                await self.flush_db()
            except Exception:
                log.exception("flush_db() failed")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        try:
            await self.flush_db()
        except Exception:
            log.exception("final flush_db() failed")

    async def reset_online_on_start(self):
        with connect() as conn:
            conn.execute("UPDATE users SET online_tcp=0,online_ws=0")
            conn.commit()
        self.live_path.unlink(missing_ok=True)

    async def run(self):
        await self.reset_online_on_start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)

        # reconcile() already handles per-port failures internally now, but
        # keep this belt-and-braces: a first-run reconcile must never take
        # the whole process down, or systemd will crash-loop it forever with
        # nothing coming back up until a reboot clears the stuck state.
        try:
            await self.reconcile()
        except Exception:
            log.exception("initial reconcile() failed, continuing anyway")
        await asyncio.gather(self.reconcile_loop(), self.snapshot_loop(), self.database_loop())

async def main():
    init_db(Config.DB_PATH)
    await Runtime().run()

if __name__ == "__main__":
    asyncio.run(main())
