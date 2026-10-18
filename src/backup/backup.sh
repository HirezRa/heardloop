#!/bin/sh
# One backup "set": heardloop-YYYYMMDDTHHMMSSZ
#   <set>.dump             pg_dump custom format (restore with pg_restore)
#   <set>-media.tar.gz     the media volume
#   (both get a .enc suffix when BACKUP_PASSPHRASE is set)
#   <set>.sha256           checksums of the data files
#   <set>.json             manifest (status ok | failed); latest.json is an atomic copy of the newest one
# Nothing from .env is included. Plain-text intermediates live in /tmp (tmpfs) when encrypting.
set -eu
umask 077
# pipefail is not in every POSIX shell; use it where available.
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi

# Compose passes POSTGRES_PASSWORD (same as the db service). libpq reads PGPASSWORD; never echo it.
# `?` (no colon): the variable must be set; empty is allowed (throwaway CI databases with trust auth).
export PGPASSWORD="${POSTGRES_PASSWORD?POSTGRES_PASSWORD is not set}"

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
MEDIA_ROOT="${MEDIA_ROOT:-/data/media}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
KEEP_MIN="${BACKUP_KEEP_MIN:-7}"
PASSPHRASE_SET=0
if [ -n "${BACKUP_PASSPHRASE:-}" ]; then PASSPHRASE_SET=1; fi

jlog() {
  # jlog <event> [extra JSON members, each starting with a comma]
  printf '{"at":"%s","event":"%s"%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${2:-}"
}

json_str() {
  # Escapes a short text for a JSON string: line breaks become spaces, anything but printable ASCII is dropped.
  printf '%s' "$1" | tr '\n\r\t' '   ' | tr -cd ' -~' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

is_uint() {
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

if ! is_uint "$KEEP_DAYS" || ! is_uint "$KEEP_MIN"; then
  jlog "backup.config_error" ',"error":"BACKUP_KEEP_DAYS and BACKUP_KEEP_MIN must be whole numbers"'
  exit 2
fi
if [ "$KEEP_MIN" -lt 1 ]; then KEEP_MIN=1; fi # the newest good set is never pruned

if [ ! -d "$BACKUP_ROOT" ] || [ ! -w "$BACKUP_ROOT" ]; then
  jlog "backup.config_error" ',"error":"backup directory is missing or not writable"'
  exit 1
fi

SET="heardloop-$(date -u +%Y%m%dT%H%M%SZ)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$PASSPHRASE_SET" -eq 1 ]; then
  STAGE="/tmp/stage-$SET"
else
  STAGE="$BACKUP_ROOT/.stage-$SET" # same filesystem as the target: the final move is a rename
fi
mkdir -p "$STAGE"
FINISHED_OK=0

cleanup() {
  rm -rf "$STAGE"
  if [ "$FINISHED_OK" -ne 1 ]; then
    rm -f "$BACKUP_ROOT/$SET".dump* "$BACKUP_ROOT/$SET"-media* "$BACKUP_ROOT/$SET".sha256*
  fi
}
trap cleanup EXIT
trap 'exit 143' TERM INT

write_manifest() {
  # write_manifest <status> <error or empty> <files JSON lines> <db_bytes> <media_bytes>
  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -n "$2" ]; then err="\"$(json_str "$2")\""; else err="null"; fi
  if [ "$PASSPHRASE_SET" -eq 1 ]; then enc="true"; else enc="false"; fi
  version="$(pg_dump --version 2>/dev/null | awk '{print $NF}')"
  tmp="$BACKUP_ROOT/.manifest-$SET.tmp"
  {
    printf '{\n'
    printf '  "set": "%s",\n' "$SET"
    printf '  "started_at": "%s",\n' "$STARTED"
    printf '  "finished_at": "%s",\n' "$finished"
    printf '  "status": "%s",\n' "$1"
    printf '  "error": %s,\n' "$err"
    printf '  "files": [\n%s\n  ],\n' "$3"
    printf '  "db_bytes": %s,\n' "$4"
    printf '  "media_bytes": %s,\n' "$5"
    printf '  "encrypted": %s,\n' "$enc"
    printf '  "pg_dump_version": "%s"\n' "$(json_str "$version")"
    printf '}\n'
  } >"$tmp"
  # Manifests hold no secrets (names, sizes, checksums); the app reads latest.json as another user.
  chmod 0644 "$tmp"
  cp "$tmp" "$BACKUP_ROOT/.latest.tmp"
  chmod 0644 "$BACKUP_ROOT/.latest.tmp" # cp applies the umask to the copy
  mv "$tmp" "$BACKUP_ROOT/$SET.json"
  mv -f "$BACKUP_ROOT/.latest.tmp" "$BACKUP_ROOT/latest.json" # atomic replace
}

fail() {
  # Error texts are fixed strings: no hostnames, paths or secrets reach the manifest or the UI.
  rm -f "$BACKUP_ROOT/$SET".dump* "$BACKUP_ROOT/$SET"-media* "$BACKUP_ROOT/$SET".sha256*
  write_manifest "failed" "$1" "" 0 0
  jlog "backup.failed" ",\"set\":\"$SET\",\"error\":\"$(json_str "$1")\""
  exit 1
}

show_stderr() {
  # Detail for `docker compose logs backup`; never in the manifest.
  if [ -s "$1" ]; then sed 's/^/backup: /' "$1" >&2; fi
}

file_bytes() { wc -c <"$1" | tr -d ' '; }

DUMP="$SET.dump"
MEDIA="$SET-media.tar.gz"
jlog "backup.start" ",\"set\":\"$SET\",\"encrypted\":$([ "$PASSPHRASE_SET" -eq 1 ] && echo true || echo false)"

# 1. Database
if ! pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$STAGE/$DUMP" 2>"$STAGE/pg_dump.err"; then
  show_stderr "$STAGE/pg_dump.err"
  fail "pg_dump failed"
fi
if ! pg_restore --list "$STAGE/$DUMP" >/dev/null 2>"$STAGE/list.err"; then
  show_stderr "$STAGE/list.err"
  fail "the database dump could not be read back (pg_restore --list failed)"
fi

# 2. Media (an empty volume gives an empty but valid archive). One retry: a file may vanish while it is read.
if [ ! -d "$MEDIA_ROOT" ]; then fail "media directory not found"; fi
tar_media() { tar -C "$MEDIA_ROOT" -czf "$STAGE/$MEDIA" . 2>"$STAGE/tar.err"; }
if ! tar_media; then
  sleep 2
  if ! tar_media; then
    show_stderr "$STAGE/tar.err"
    fail "media archive failed"
  fi
fi
if ! tar -tzf "$STAGE/$MEDIA" >/dev/null 2>"$STAGE/tar.err"; then
  show_stderr "$STAGE/tar.err"
  fail "media archive failed verification"
fi

# 3. Encrypt (optional) and move into the backup directory; the plain-text copies never leave /tmp.
if [ "$PASSPHRASE_SET" -eq 1 ]; then
  for f in "$DUMP" "$MEDIA"; do
    part="$BACKUP_ROOT/$f.enc.partial"
    if ! openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -pass env:BACKUP_PASSPHRASE \
      -in "$STAGE/$f" -out "$part" 2>"$STAGE/openssl.err"; then
      show_stderr "$STAGE/openssl.err"
      fail "encryption failed"
    fi
    # Read the encrypted file back and compare with the plain text before the plain text is dropped.
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:BACKUP_PASSPHRASE -in "$part" 2>/dev/null |
      cmp -s - "$STAGE/$f"; then
      fail "encrypted file failed verification"
    fi
    mv "$part" "$BACKUP_ROOT/$f.enc"
    rm -f "$STAGE/$f"
  done
  DUMP="$DUMP.enc"
  MEDIA="$MEDIA.enc"
else
  mv "$STAGE/$DUMP" "$BACKUP_ROOT/$DUMP"
  mv "$STAGE/$MEDIA" "$BACKUP_ROOT/$MEDIA"
fi

# 4. Checksums and manifest
db_bytes="$(file_bytes "$BACKUP_ROOT/$DUMP")"
media_bytes="$(file_bytes "$BACKUP_ROOT/$MEDIA")"
db_sum="$(sha256sum "$BACKUP_ROOT/$DUMP" | cut -d' ' -f1)"
media_sum="$(sha256sum "$BACKUP_ROOT/$MEDIA" | cut -d' ' -f1)"
printf '%s  %s\n%s  %s\n' "$db_sum" "$DUMP" "$media_sum" "$MEDIA" >"$BACKUP_ROOT/$SET.sha256"
files_json="$(printf '    {"name": "%s", "bytes": %s, "sha256": "%s"},\n    {"name": "%s", "bytes": %s, "sha256": "%s"}' \
  "$DUMP" "$db_bytes" "$db_sum" "$MEDIA" "$media_bytes" "$media_sum")"
write_manifest "ok" "" "$files_json" "$db_bytes" "$media_bytes"
FINISHED_OK=1
jlog "backup.done" ",\"set\":\"$SET\",\"bytes\":$((db_bytes + media_bytes)),\"encrypted\":$([ "$PASSPHRASE_SET" -eq 1 ] && echo true || echo false)"

# 5. Retention. A problem here never turns a good backup into a failed one.
set_epoch() {
  # heardloop-20261018T010000Z -> seconds since the epoch (empty when the name does not parse)
  date -u -D '%Y%m%dT%H%M%SZ' -d "${1#heardloop-}" +%s 2>/dev/null || true
}

prune() {
  now="$(date -u +%s)"
  keep_seconds=$((KEEP_DAYS * 86400))
  ok_seen=0
  for manifest in $(ls -1 "$BACKUP_ROOT"/heardloop-*.json 2>/dev/null | sort -r); do
    name="$(basename "$manifest" .json)"
    epoch="$(set_epoch "$name")"
    [ -n "$epoch" ] || continue
    old=0
    if [ $((now - epoch)) -gt "$keep_seconds" ]; then old=1; fi
    if grep -q '"status": "ok"' "$manifest"; then
      ok_seen=$((ok_seen + 1))
      # newest first: the first KEEP_MIN good sets always stay, however old
      if [ "$ok_seen" -le "$KEEP_MIN" ] || [ "$old" -eq 0 ]; then continue; fi
    elif [ "$old" -eq 0 ]; then
      continue
    fi
    rm -f "$BACKUP_ROOT/$name".* "$BACKUP_ROOT/$name"-*
    jlog "backup.pruned" ",\"set\":\"$name\""
  done
  # Leftovers of a crashed run (no manifest) older than a day.
  find "$BACKUP_ROOT" -maxdepth 1 \( -name '.stage-*' -o -name '.manifest-*.tmp' -o -name '.latest.tmp' \) \
    -mmin +1440 -exec rm -rf {} + 2>/dev/null || true
  for orphan in $(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'heardloop-*' -mmin +1440 2>/dev/null); do
    base="$(basename "$orphan")"
    owner="$(printf '%s' "$base" | cut -c1-26)"
    if [ ! -f "$BACKUP_ROOT/$owner.json" ]; then
      rm -f "$orphan"
      jlog "backup.partial_removed" ",\"file\":\"$(json_str "$base")\""
    fi
  done
}
prune || jlog "backup.prune_warning" ',"error":"retention step failed"'
exit 0
