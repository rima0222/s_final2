import os

class Config:
    SECRET_KEY = os.environ["CUSTOM_PANEL_SECRET_KEY"]
    ADMIN_USERNAME = os.getenv("CUSTOM_PANEL_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH = os.environ["CUSTOM_PANEL_ADMIN_PASSWORD_HASH"]
    DATA_KEY = os.environ["CUSTOM_PANEL_DATA_KEY"]
    DB_PATH = os.getenv("CUSTOM_PANEL_DB", "/etc/custom-panel/data/panel.db")
    SERVER_HOST = os.getenv("CUSTOM_PANEL_SERVER_HOST", "SERVER_IP")
    INTERNAL_SSH_PORT = int(os.getenv("CUSTOM_PANEL_INTERNAL_SSH_PORT", "2222"))
    TCP_PORT_START = int(os.getenv("CUSTOM_PANEL_TCP_PORT_START", "20000"))
    TCP_PORT_END = int(os.getenv("CUSTOM_PANEL_TCP_PORT_END", "24999"))
    WS_PORT_START = int(os.getenv("CUSTOM_PANEL_WS_PORT_START", "25000"))
    WS_PORT_END = int(os.getenv("CUSTOM_PANEL_WS_PORT_END", "29999"))
    HELPER_SOCKET = os.getenv("CUSTOM_PANEL_HELPER_SOCKET", "/run/custom-panel/helper.sock")
    LIVE_PATH = os.getenv("CUSTOM_PANEL_LIVE_PATH", "/run/custom-panel/live.json")
    PANEL_PORT = int(os.getenv("PANEL_PORT", "443"))
    DOMAIN = os.getenv("CUSTOM_PANEL_DOMAIN", "")
    TLS_CERT = os.getenv("CUSTOM_PANEL_TLS_CERT", "")
    TLS_KEY = os.getenv("CUSTOM_PANEL_TLS_KEY", "")
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
