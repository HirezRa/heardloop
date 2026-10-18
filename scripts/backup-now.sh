#!/usr/bin/env bash
# Runs one backup now (same as the nightly one) and prints the result.
# Usage (from anywhere):  bash scripts/backup-now.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

exit_code=0
docker compose run --rm backup now || exit_code=$?

# The manifest of the newest set (success or failure), read through the backup container.
if ! manifest="$(docker compose run --rm --no-deps --entrypoint cat backup /backups/latest.json 2>/dev/null)"; then
  echo "Backup finished with exit code $exit_code and no latest.json was found." >&2
  exit 1
fi
field() { printf '%s\n' "$manifest" | sed -n "s/^  \"$1\": \"\{0,1\}\([^\",]*\).*/\1/p" | head -n 1; }
status="$(field status)"
db="$(field db_bytes)"
media="$(field media_bytes)"
echo
echo "Set:       $(field set)"
echo "Status:    $status"
echo "Finished:  $(field finished_at)"
echo "Size:      $(( (db + media) / 1048576 )) MB (database $((db / 1048576)) MB, media $((media / 1048576)) MB)"
echo "Encrypted: $(field encrypted)"
if [ "$status" != "ok" ] || [ "$exit_code" -ne 0 ]; then
  echo "Error:     $(printf '%s\n' "$manifest" | sed -n 's/^  "error": "\(.*\)",$/\1/p')"
  exit 1
fi
echo "Files are in BACKUP_DIR (default: ./backups). .env is not included: keep your own copy of HEARDLOOP_TOKEN_KEY."
