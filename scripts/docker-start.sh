#!/usr/bin/env bash
# Start Dataset Genie in Docker (Mac / Linux). Windows users: scripts\docker-start.ps1
#   scripts/docker-start.sh              # build if needed, start, open http://localhost:8765
#   GENIE_PORT=9000 scripts/docker-start.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker Desktop, wait for it to say 'running', then try again."; exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "The 'docker compose' plugin is missing. Docker Desktop includes it; on Linux install docker-compose-plugin."; exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Add your OpenRouter key there, or enter it later in Settings."
fi
# read GENIE_PORT from .env unless already set in the shell
PORT="${GENIE_PORT:-$(grep -E '^GENIE_PORT=' .env 2>/dev/null | cut -d= -f2)}"
PORT="${PORT:-8765}"
export GENIE_PORT="$PORT"
mkdir -p exports

echo "Building and starting Dataset Genie (the first build takes a few minutes) ..."
docker compose up -d --build

URL="http://localhost:$PORT"
for _ in $(seq 1 120); do
  if curl -sf "$URL/api/health" >/dev/null 2>&1; then
    echo "Ready: $URL"
    echo "Exports land in $(pwd)/exports. Stop with scripts/docker-stop.sh"
    if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
    exit 0
  fi
  sleep 1
done
echo "The app did not become healthy within two minutes. Last log lines:"
docker compose logs --tail 40 genie
exit 1
