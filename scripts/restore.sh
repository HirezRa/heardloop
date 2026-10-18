#!/usr/bin/env bash
# Restores a backup set into the running stack: stops api and worker, restores the database and the media
# files, starts api and worker again. This REPLACES the current data.
# Usage:  bash scripts/restore.sh heardloop-20261018T010000Z [--yes]
# Encrypted set: export BACKUP_PASSPHRASE=<passphrase used for that backup> first (it is read from .env
# otherwise). Without the original HEARDLOOP_TOKEN_KEY in .env, LinkedIn tokens and two-step secrets in the
# restored database cannot be read: see docs/GUIDE.md, section 10.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

set_name="${1:-}"
yes="${2:-}"
if [[ ! "$set_name" =~ ^heardloop-[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Usage: bash scripts/restore.sh heardloop-YYYYMMDDTHHMMSSZ [--yes]" >&2
  exit 2
fi
if [ "$yes" != "--yes" ]; then
  echo "This replaces the database and all media files with the contents of $set_name."
  read -r -p "Type yes to continue: " answer
  if [ "$answer" != "yes" ]; then echo "Cancelled."; exit 1; fi
fi

echo "Stopping api and worker..."
docker compose stop api worker

# The `restore` service: media volume read-write, root with three capabilities (the media files belong to the
# application user, the backup files to the backup user). The container is removed afterwards (--rm).
if ! docker compose run --rm -e CONFIRM_RESTORE=yes restore "$set_name"; then
  echo "Restore failed. api and worker are still stopped. Fix the problem above, then run this script again or start them with: docker compose start api worker" >&2
  exit 1
fi

echo "Starting api and worker..."
docker compose start api worker
echo "Done. Open http://localhost:8380 and check your data."
