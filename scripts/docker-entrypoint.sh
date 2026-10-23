#!/usr/bin/env bash
# Container entry point. Usage (CMD): serve (default) | init | any command (bash, python3…).
#   serve: sets the dashboard password if needed, then runs Gunicorn on ATELIER_BIND (0.0.0.0:8765).
#   init:  asks for the dashboard password interactively (docker compose run --rm atelier init).
# Inside the container the server listens on every interface; docker-compose.yml publishes it on 127.0.0.1 only.
set -euo pipefail
cd /app
export ATELIER_DATA="${ATELIER_DATA:-/data}"
umask 077
mkdir -p "$ATELIER_DATA"
# Antigravity keeps its Google session in ~/.gemini (volume) and its binary in /opt/agy/bin (volume).
[ -x /opt/agy/bin/agy ] || command -v agy >/dev/null || printf 'Optional: install Gemini · Antigravity (agy) following Google’s documentation, binary in /opt/agy/bin.\n'
[ -f "$CLAUDE_CONFIG_DIR/.credentials.json" ] || printf 'To do: log in to Claude Code with "docker exec -it atelier claude", then /login.\n'
[ -f "$CODEX_HOME/auth.json" ] || printf 'To do: log in to Codex with "docker exec -it atelier codex login".\n'

set_password() {
  if [ -n "${ATELIER_PASSWORD:-}" ]; then
    # Hashed with server.password_hash, read from the environment, never written in clear.
    python3 - <<'PY'
import json, os, sys
from pathlib import Path
from server import password_hash
password = os.environ['ATELIER_PASSWORD']
if len(password) < 12: sys.exit('ATELIER_PASSWORD must be at least 12 characters long.')
p = Path(os.environ['ATELIER_DATA']) / 'settings.json'
p.write_text(json.dumps({'password': password_hash(password)})); os.chmod(p, 0o600)
print('Dashboard password saved from ATELIER_PASSWORD. You can now remove it from your .env file.')
PY
  elif [ -t 0 ]; then
    python3 server.py --init --data "$ATELIER_DATA"
  else
    printf 'No dashboard password yet. Run once:\n  docker compose run --rm atelier init\nor set ATELIER_PASSWORD (12 characters minimum) for the first start.\n' >&2
    exit 1
  fi
}

case "${1:-serve}" in
  init)
    [ -f "$ATELIER_DATA/settings.json" ] && { echo 'A dashboard password already exists.'; exit 0; }
    unset ATELIER_PASSWORD; set_password ;;
  serve)
    [ -f "$ATELIER_DATA/settings.json" ] || set_password
    unset ATELIER_PASSWORD
    exec python3 -m gunicorn --workers 1 --threads 8 --bind "${ATELIER_BIND:-0.0.0.0:8765}" --timeout 120 \
      --access-logfile - --error-logfile - server:application ;;
  *)
    unset ATELIER_PASSWORD; exec "$@" ;;
esac
