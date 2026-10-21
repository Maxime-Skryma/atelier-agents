#!/usr/bin/env bash
# Starts Atelier in the background on 127.0.0.1:8765 and installs what is missing.
# Usage: bash scripts/start.sh [public URL]
#   Onyxia (code-server): the URL is deduced from VSCODE_PROXY_URI.
#   Any other Linux VM: no URL needed, open it through an SSH tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."
export ATELIER_DATA="${ATELIER_DATA:-$PWD/data}"
url="${1:-${ATELIER_PUBLIC_URL:-}}"
if [ -z "$url" ] && [ -n "${VSCODE_PROXY_URI:-}" ]; then url="${VSCODE_PROXY_URI//\{\{port\}\}/8765}"; fi
export ATELIER_PUBLIC_URL="$url"
# On ephemeral machines (Onyxia), only the parent folder survives a restart: Antigravity's
# binary and Google session can be kept in ../.atelier-persist and are linked back into $HOME.
PERSIST="$(cd .. && pwd)/.atelier-persist"
export PATH="$PERSIST/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
if [ -x "$PERSIST/bin/agy" ]; then
  mkdir -p "$HOME/.local/bin"
  ln -sfn "$PERSIST/bin/agy" "$HOME/.local/bin/agy"
fi
if [ -d "$PERSIST/gemini" ]; then
  if [ ! -e "$HOME/.gemini" ]; then
    ln -s "$PERSIST/gemini" "$HOME/.gemini"
  elif [ "$(readlink "$HOME/.gemini")" != "$PERSIST/gemini" ]; then
    echo "Warning: $HOME/.gemini already exists and does not point to $PERSIST/gemini. The saved Antigravity session will not be used."
  fi
fi
umask 077
mkdir -p "$ATELIER_DATA"
# Fail before installing if another service already owns the port.
python3 - <<'PY'
import socket
with socket.socket() as s:
    # Like Gunicorn: ignore recently closed connections, still fail on a live listener.
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try: s.bind(('127.0.0.1',8765))
    except OSError: raise SystemExit('Port 8765 is already in use. Try the dashboard link before starting again.')
PY
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# Gunicorn, and Pandoc (bundled) to convert deliverables to PDF, Word, PowerPoint and LaTeX.
if ! .venv/bin/python -c 'import gunicorn, pypandoc' 2>/dev/null; then
  .venv/bin/python -m pip install -q -r requirements.txt
fi
# System tools are lost when an ephemeral VM restarts: reinstalled here when missing (Debian/Ubuntu).
# LibreOffice + Poppler: previews of reference examples and agents checking their own slides.
if ! command -v soffice >/dev/null || ! command -v pdftoppm >/dev/null; then
  if sudo -n true 2>/dev/null; then
    echo 'Installing LibreOffice and Poppler (a few minutes)…'
    { sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends libreoffice-impress libreoffice-writer poppler-utils fonts-crosextra-carlito >/dev/null; } \
      || echo 'Warning: LibreOffice could not be installed. Previews and visual checks of deliverables will be unavailable.'
  else
    echo 'Warning: LibreOffice is missing and sudo is unavailable. Previews and visual checks of deliverables will be unavailable.'
  fi
fi
# XeLaTeX (TinyTeX) for PDF deliverables.
if ! command -v xelatex >/dev/null && command -v quarto >/dev/null; then
  echo 'Installing TinyTeX for PDF output…'
  quarto install tinytex --no-prompt >/dev/null 2>&1 || echo 'Warning: TinyTeX could not be installed. PDF deliverables will fail.'
fi
# Libraries the agents use to build PowerPoint and Word files themselves.
if command -v python3 >/dev/null && ! python3 -c 'import pptx, docx, PIL, matplotlib' 2>/dev/null; then
  python3 -m pip install -q python-pptx python-docx Pillow matplotlib >/dev/null 2>&1 \
    || echo 'Warning: python-pptx could not be installed. Agents will rely on Atelier’s formatting.'
fi
# Claude Code and Codex: installed when missing. Their sessions are never copied or kept by Atelier.
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then set +u; . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true; set -u; fi
if ! command -v claude >/dev/null; then
  echo 'Installing Claude Code…'
  { curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1; } || echo 'Warning: Claude Code could not be installed.'
fi
if ! command -v codex >/dev/null; then
  if ! command -v npm >/dev/null; then
    echo 'Installing Node.js…'
    { curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash >/dev/null 2>&1 && set +u && . "$NVM_DIR/nvm.sh" && nvm install --lts >/dev/null 2>&1; set -u; } || echo 'Warning: Node.js could not be installed.'
  fi
  echo 'Installing Codex…'
  npm install -g @openai/codex >/dev/null 2>&1 || echo 'Warning: Codex could not be installed.'
fi
# Only checks that a login exists; never reads it.
[ -f "$HOME/.claude/.credentials.json" ] || printf '\nTo do: log in to Claude Code with "claude", then /login.\n'
[ -f "$HOME/.codex/auth.json" ] || printf 'To do: log in to Codex with "codex login".\n'
if [ ! -f "$ATELIER_DATA/settings.json" ]; then
  .venv/bin/python server.py --init
fi
nohup .venv/bin/python -m gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8765 --timeout 120 --access-logfile - --error-logfile - server:application > "$ATELIER_DATA/service.log" 2>&1 < /dev/null &
atelier_pid=$!
echo "$atelier_pid" > "$ATELIER_DATA/service.pid"
sleep 2
if ! kill -0 "$atelier_pid" 2>/dev/null; then
  echo 'Start failed. Details:'
  tail -30 "$ATELIER_DATA/service.log"
  exit 1
fi
if [ -n "$ATELIER_PUBLIC_URL" ]; then
  printf '\nAtelier is running. Open:\n%s\n' "$ATELIER_PUBLIC_URL"
else
  printf '\nAtelier is running on http://127.0.0.1:8765/\nFrom your computer: ssh -L 8765:127.0.0.1:8765 <your-vm>, then open http://localhost:8765/\n'
fi
printf 'Log: %s/service.log\n' "$ATELIER_DATA"
