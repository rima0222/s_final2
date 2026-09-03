#!/usr/bin/env bash
set -Eeuo pipefail
APP=/etc/custom-panel
[[ "$EUID" -eq 0 ]] || { echo "Run as root."; exit 1; }
PASS="${1:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)}"
HASH="$("$APP/venv/bin/python" - <<PY
from werkzeug.security import generate_password_hash
print(generate_password_hash("""$PASS"""))
PY
)"
sed -i "s|^CUSTOM_PANEL_ADMIN_PASSWORD_HASH=.*|CUSTOM_PANEL_ADMIN_PASSWORD_HASH=$HASH|" "$APP/.env"
printf 'Username: admin\nPassword: %s\n' "$PASS" > "$APP/admin-credentials.txt"
chmod 600 "$APP/admin-credentials.txt"
systemctl restart custom-panel
cat "$APP/admin-credentials.txt"
