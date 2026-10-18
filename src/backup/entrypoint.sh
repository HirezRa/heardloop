#!/bin/sh
# Backup service entrypoint.
#   (no argument)       sleep until the next BACKUP_HOUR_UTC:00, run backup.sh, repeat (default service mode)
#   now                 one backup, then exit with its status (manual script and CI)
#   restore <set>       restore a set (see restore.sh; needs CONFIRM_RESTORE=yes)
# One JSON line per event on stdout. Never logs the passphrase or the database password.
set -eu

# Compose passes POSTGRES_PASSWORD (same as the db service). libpq reads PGPASSWORD; never echo it.
# `?` (no colon): the variable must be set; empty is allowed (throwaway CI databases with trust auth).
export PGPASSWORD="${POSTGRES_PASSWORD?POSTGRES_PASSWORD is not set}"

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"

jlog() {
  # jlog <event> [extra JSON members, each starting with a comma]
  printf '{"at":"%s","event":"%s"%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${2:-}"
}

mode="${1:-run}"

case "$mode" in
  now)
    exec /usr/local/bin/backup.sh
    ;;
  restore)
    shift
    exec /usr/local/bin/restore.sh "$@"
    ;;
  run) ;;
  *)
    jlog "backup.usage" ',"error":"unknown argument; use: now | restore <set> | (none)"'
    exit 2
    ;;
esac

hour="${BACKUP_HOUR_UTC:-01}"
case "$hour" in
  [0-9] | 0[0-9] | 1[0-9] | 2[0-3]) ;;
  *)
    jlog "backup.config_error" ',"error":"BACKUP_HOUR_UTC must be 0-23"'
    exit 2
    ;;
esac
hour=$(printf '%s' "$hour" | sed 's/^0*//')
hour="${hour:-0}"

if [ ! -d "$BACKUP_ROOT" ] || [ ! -w "$BACKUP_ROOT" ]; then
  jlog "backup.config_error" ',"error":"backup directory is missing or not writable for the backup user (uid 70)"'
  exit 1
fi

stop=0
child=""
on_term() {
  stop=1
  if [ -n "$child" ]; then kill -TERM "$child" 2>/dev/null || true; fi
}
trap on_term TERM INT

seconds_until_next_run() {
  now=$(date -u +%s)
  target=$(( now - now % 86400 + hour * 3600 ))
  if [ "$target" -le "$now" ]; then target=$(( target + 86400 )); fi
  echo $(( target - now ))
}

jlog "backup.service_started" ",\"hour_utc\":$hour"
while [ "$stop" -eq 0 ]; do
  wait_for=$(seconds_until_next_run)
  jlog "backup.scheduled" ",\"in_seconds\":$wait_for"
  sleep "$wait_for" &
  child=$!
  wait "$child" || true
  child=""
  [ "$stop" -eq 0 ] || break
  /usr/local/bin/backup.sh &
  child=$!
  status=0
  wait "$child" || status=$?
  child=""
  if [ "$status" -ne 0 ] && [ "$stop" -eq 0 ]; then
    jlog "backup.run_failed" ",\"exit_code\":$status"
  fi
done
jlog "backup.service_stopped"
exit 0
