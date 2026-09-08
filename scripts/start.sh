#!/usr/bin/env bash
# Start Dataset Genie as a single local app (API + built UI) in the background.
#   scripts/start.sh            # production mode on http://localhost:8765
#   scripts/start.sh --dev      # backend with reload + Vite dev server (http://localhost:5173)
#   PORT=9000 scripts/start.sh  # different port
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:$PATH"
GENIE_HOME="${GENIE_HOME:-$HOME/.dataset-genie}"
PORT="${PORT:-8765}"
mkdir -p "$GENIE_HOME"
PIDFILE="$GENIE_HOME/genie.pid"
LOG="$GENIE_HOME/genie.log"

if [ ! -e .venv ]; then
  echo "No virtualenv yet - running 'make install' first"; make install
fi
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Dataset Genie already running (pid $(cat "$PIDFILE")) - http://localhost:$PORT"; exit 0
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use (another server?). Run scripts/stop.sh first or set PORT=..."; exit 1
fi

if [ "${1:-}" = "--dev" ]; then
  echo "Starting backend (reload) on :$PORT and Vite on :5173 ..."
  (uv run --no-sync uvicorn genie.main:app --reload --port "$PORT" --app-dir backend >"$LOG" 2>&1 & echo $! >"$PIDFILE")
  (cd frontend && npm run dev -- --port 5173 --strictPort >"$GENIE_HOME/vite.log" 2>&1 & echo $! >"$GENIE_HOME/vite.pid")
  URL="http://localhost:5173"
else
  if [ ! -f frontend/dist/index.html ]; then
    echo "Building the UI (first run) ..."; (cd frontend && npm run build >/dev/null)
  fi
  echo "Starting Dataset Genie on :$PORT ..."
  (uv run --no-sync genie serve --port "$PORT" >"$LOG" 2>&1 & echo $! >"$PIDFILE")
  URL="http://localhost:$PORT"
fi

for _ in $(seq 1 40); do
  if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    echo "Ready: $URL   (log: $LOG)"
    command -v open >/dev/null && open "$URL" || true
    exit 0
  fi
  sleep 0.5
done
echo "Backend did not become healthy; see $LOG"; tail -20 "$LOG"; exit 1
