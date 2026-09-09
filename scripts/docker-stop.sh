#!/usr/bin/env bash
# Stop the Dataset Genie container (Mac / Linux). Data is kept in the `genie-data` volume.
#   scripts/docker-stop.sh            # stop and remove the container
#   scripts/docker-stop.sh --reset    # also delete the database + secrets volume (exports/ is untouched)
set -euo pipefail
cd "$(dirname "$0")/.."
if ! docker info >/dev/null 2>&1; then echo "Docker is not running; nothing to stop."; exit 0; fi
if [ "${1:-}" = "--reset" ]; then
  read -r -p "This deletes the database and stored tokens (exports/ is kept). Continue? [y/N] " ans
  case "$ans" in y|Y|yes|YES) docker compose down -v; echo "Dataset Genie stopped and data reset." ;; *) echo "Cancelled." ;; esac
else
  docker compose down
  echo "Dataset Genie stopped. Data kept; start again with scripts/docker-start.sh"
fi
