#!/usr/bin/env bash
# Stop Dataset Genie (production or --dev servers started by scripts/start.sh, plus any stray
# uvicorn/vite/genie processes bound to the app ports).
set -uo pipefail
GENIE_HOME="${GENIE_HOME:-$HOME/.dataset-genie}"
PORT="${PORT:-8765}"
stopped=0
for f in "$GENIE_HOME/genie.pid" "$GENIE_HOME/vite.pid"; do
  if [ -f "$f" ]; then
    pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null && stopped=1; fi
    rm -f "$f"
  fi
done
# anything else listening on the app ports (e.g. `make dev` started by hand)
for p in "$PORT" 5173; do
  for pid in $(lsof -nP -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null); do
    if ps -o command= -p "$pid" | grep -qE "genie|uvicorn|vite|node"; then kill "$pid" 2>/dev/null && stopped=1; fi
  done
done
sleep 1
for p in "$PORT" 5173; do
  for pid in $(lsof -nP -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
done
if [ "$stopped" = 1 ]; then echo "Dataset Genie stopped."; else echo "Dataset Genie was not running."; fi
