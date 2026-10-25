#!/usr/bin/env bash
# Reload the running Atelier after a code change, without stopping the service.
# Refuses while a mission is running: a reload would interrupt its agents.
set -euo pipefail
cd "$(dirname "$0")/.."
DATA="${ATELIER_DATA:-$PWD/data}"
out="$(.venv/bin/python -m unittest discover -s tests 2>&1)" || { echo "$out" | tail -40; echo "Tests failed: reload cancelled."; exit 1; }
echo "$out" | tail -1
if command -v node >/dev/null; then node --check static/app.js; fi
.venv/bin/python - "$DATA" <<'PY'
import json, sqlite3, sys
db = sqlite3.connect('file:' + sys.argv[1] + '/atelier.sqlite3?mode=ro', uri=True)
busy = [m['name'] for m in (json.loads(d) for (d,) in db.execute("SELECT data FROM objects WHERE kind='missions'")) if m['status'] in ('queued', 'running')]
if busy: raise SystemExit('Mission running: ' + ', '.join(busy) + '. Reload cancelled.')
PY
pid="$(cat "$DATA/service.pid" 2>/dev/null || true)"
if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
  echo "The service is not running. Start it with: bash scripts/start.sh"; exit 1
fi
kill -HUP "$pid"
sleep 3
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz || true)"
[ "$code" = 200 ] && echo "Atelier reloaded." || { echo "The service is not responding ($code). Log:"; tail -30 "$DATA/service.log"; exit 1; }
