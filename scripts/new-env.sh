#!/usr/bin/env bash
# Creates .env with strong random secrets. Refuses to overwrite an existing .env.
# Usage (from repo root):  ./scripts/new-env.sh
# The secrets reach awk through its own environment only; nothing is exported to this shell or printed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
[[ ! -e "$ENV_FILE" ]] || { echo ".env already exists - delete it first if you really want new secrets." >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "openssl is required." >&2; exit 1; }
umask 077
HL_DB="$(openssl rand -hex 24)" HL_KEY="$(openssl rand -base64 32 | tr -d '\n')" awk '
  /^POSTGRES_PASSWORD=/   { print "POSTGRES_PASSWORD=" ENVIRON["HL_DB"]; next }
  /^HEARDLOOP_TOKEN_KEY=/ { print "HEARDLOOP_TOKEN_KEY=" ENVIRON["HL_KEY"]; next }
  { print }
' "$ROOT/.env.example" > "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo ".env created. Fill in the LINKEDIN_* values. Back up HEARDLOOP_TOKEN_KEY somewhere safe."
