#!/usr/bin/env bash
# End-to-end check of src/backup against a throwaway PostgreSQL (used by the CI job `backup-smoke`).
# Builds the image, backs up a seeded database plus a media folder, verifies the set, restores it into a
# second empty database and media folder, and repeats that with encryption. Also checks the failure
# manifest, retention and the clean stop of the scheduler loop.
#
# Needs: docker, bash. Environment:
#   PGHOST (required)  host of the database as seen from the backup container
#   PGUSER / PGDATABASE / POSTGRES_PASSWORD   default: ci / heardloop_test / empty (trust auth in CI)
#   DOCKER_NETWORK     network the containers join (default: host, as on a CI runner)
#   PGPORT             port (default 5432)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${PGHOST:?set PGHOST to the database host}"
export PGUSER="${PGUSER:-ci}" PGDATABASE="${PGDATABASE:-heardloop_test}" PGPORT="${PGPORT:-5432}"
# Empty is fine with trust auth; the backup scripts require the variable to be set.
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
NET="${DOCKER_NETWORK:-host}"
IMG="heardloop-backup:smoke"
# Fresh throwaway values each run (not secrets; never logged). Avoid fixed strings that secret scanners flag.
PASS_ONE="$(openssl rand -hex 16)"
PASS_TWO="$(openssl rand -hex 16)"

work="$(mktemp -d)"
if command -v cygpath >/dev/null 2>&1; then
  # Git Bash on Windows: hand Docker Windows-style paths and do not rewrite container paths.
  export MSYS_NO_PATHCONV=1
  work="$(cygpath -m "$work")"
fi
chmod 777 "$work" # the container user (not the runner user) must be able to clean up in here
cleanup() {
  docker rm -f hl-smoke-loop >/dev/null 2>&1 || true
  # Files written by the container's user are removed through the container itself.
  docker run --rm -v "$work:/w" --entrypoint sh "$IMG" -c 'rm -rf /w/* /w/.[!.]* 2>/dev/null; true' >/dev/null 2>&1 || true
  rm -rf "$work" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo "== $*"; }

# Same hardening as the compose service.
hardening=(--read-only --tmpfs /tmp --tmpfs /var/lib/postgresql/data --cap-drop ALL
  --security-opt no-new-privileges:true --pids-limit 64)
# Pass POSTGRES_PASSWORD by value: `docker -e NAME` drops an empty host value, and the scripts require it set.
env_flags=(-e PGHOST -e PGUSER -e PGDATABASE -e PGPORT
  -e "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" -e BACKUP_KEEP_DAYS -e BACKUP_KEEP_MIN)
export PGHOST

new_dir() { mkdir -p "$work/$1"; chmod 777 "$work/$1"; echo "$work/$1"; }

# docker run helpers: img <dir-mounts...> -- <entrypoint args>
img() { docker run --rm --network "$NET" "${hardening[@]}" "${env_flags[@]}" "$@"; }
sql() { img --entrypoint psql "$IMG" --no-psqlrc -Atq -d "${2:-$PGDATABASE}" -c "$1"; }
in_img() { # in_img <backups dir> <command> [extra docker args]: run a shell command with the backup dir read-only
  local dir="$1" cmd="$2"
  shift 2
  img -e BACKUP_PASSPHRASE "$@" -v "$dir:/backups:ro" --entrypoint sh "$IMG" -c "$cmd"
}

step "build image"
docker build -q -t "$IMG" src/backup >/dev/null

step "seed the database and a media folder"
sql "DROP TABLE IF EXISTS smoke; CREATE TABLE smoke (id int PRIMARY KEY, note text); INSERT INTO smoke VALUES (1, 'row-before-backup');"
media="$(new_dir media)"
mkdir -p "$media/ab/cd"
echo "media-file-one" >"$media/ab/cd/one.txt"
chmod -R a+rX "$media"

check_set() { # check_set <backups dir> <encrypted: yes|no> <passphrase or empty>
  local dir="$1" enc="$2" pass="$3" set
  grep -q '"status": "ok"' "$dir/latest.json" || fail "latest.json is not ok: $(cat "$dir/latest.json")"
  grep -q '"error": null' "$dir/latest.json" || fail "latest.json has an error"
  set="$(sed -n 's/^  "set": "\(heardloop-[0-9TZ]*\)".*/\1/p' "$dir/latest.json")"
  [ -n "$set" ] || fail "no set name in latest.json"
  [ -f "$dir/$set.json" ] && [ -f "$dir/$set.sha256" ] || fail "manifest or checksum file missing"
  cmp -s "$dir/$set.json" "$dir/latest.json" || fail "latest.json differs from the set manifest"
  # File permissions are only meaningful on a Linux bind mount (Docker Desktop shows everything as open).
  if [ "$(uname -s)" = Linux ]; then
    # The API and worker read latest.json as another user (uid 10001 in the application image) ...
    img --user 10001 --entrypoint cat -v "$dir:/backups:ro" "$IMG" /backups/latest.json >/dev/null \
      || fail "latest.json is not readable by the application user"
    # ... while the data files stay private to the backup user.
    if img --user 10001 --entrypoint cat -v "$dir:/backups:ro" "$IMG" "/backups/$set.sha256" >/dev/null 2>&1; then
      fail "backup data files are readable by other users"
    fi
  fi
  if [ "$enc" = yes ]; then
    grep -q '"encrypted": true' "$dir/latest.json" || fail "manifest does not say encrypted"
    [ -f "$dir/$set.dump.enc" ] && [ -f "$dir/$set-media.tar.gz.enc" ] || fail ".enc files missing"
    [ ! -e "$dir/$set.dump" ] && [ ! -e "$dir/$set-media.tar.gz" ] || fail "plain-text files left in the backup dir"
  else
    grep -q '"encrypted": false' "$dir/latest.json" || fail "manifest says encrypted"
    [ -f "$dir/$set.dump" ] && [ -f "$dir/$set-media.tar.gz" ] || fail "set files missing"
  fi
  # No staging leftovers or partial files.
  ls -A "$dir" | grep -E '^\.stage-|\.partial$|^\.manifest-|^\.latest\.tmp$' && fail "leftover temporary files"
  in_img "$dir" "cd /backups && sha256sum -c $set.sha256" -e BACKUP_PASSPHRASE="$pass" >/dev/null || fail "sha256 check failed"
  if [ "$enc" = yes ]; then
    in_img "$dir" "openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:BACKUP_PASSPHRASE -in /backups/$set.dump.enc >/tmp/d && pg_restore --list /tmp/d >/dev/null" \
      -e BACKUP_PASSPHRASE="$pass" || fail "decrypt + pg_restore --list failed"
  else
    in_img "$dir" "pg_restore --list /backups/$set.dump >/dev/null && tar -tzf /backups/$set-media.tar.gz >/dev/null" \
      || fail "pg_restore --list or tar -t failed"
  fi
  echo "$set"
}

restore_into() { # restore_into <backups dir> <set> <db> <media dir> <passphrase or empty>
  sql "DROP DATABASE IF EXISTS $3" postgres
  sql "CREATE DATABASE $3" postgres
  img -e CONFIRM_RESTORE=yes -e BACKUP_PASSPHRASE="$5" -e PGDATABASE="$3" \
    -v "$1:/backups:ro" -v "$4:/data/media" "$IMG" restore "$2"
  [ "$(sql "SELECT note FROM smoke WHERE id = 1" "$3")" = "row-before-backup" ] || fail "restored row is missing"
  [ "$(img --entrypoint cat -v "$4:/data/media:ro" "$IMG" /data/media/ab/cd/one.txt)" = "media-file-one" ] \
    || fail "restored media file is missing"
}

# ---- 1. plain set ----
step "backup (plain)"
plain="$(new_dir plain)"
log="$(img -v "$plain:/backups" -v "$media:/data/media:ro" "$IMG" now)"
echo "$log"
echo "$log" | grep -q '"event":"backup.done"' || fail "no backup.done log line"
set_plain="$(check_set "$plain" no "")"

step "restore (plain) into a second database and media folder"
media_restored="$(new_dir media_restored)"
echo "stale-file" >"$media_restored/stale.txt"
restore_into "$plain" "$set_plain" restored_plain "$media_restored" ""
img --entrypoint test -v "$media_restored:/data/media:ro" "$IMG" ! -e /data/media/stale.txt || fail "restore did not wipe the old media files"

step "restore refuses without confirmation and on a damaged set"
if img -e PGDATABASE=restored_plain -v "$plain:/backups:ro" -v "$media_restored:/data/media" "$IMG" restore "$set_plain" 2>/dev/null; then
  fail "restore ran without CONFIRM_RESTORE=yes"
fi
img --entrypoint sh -v "$plain:/backups" "$IMG" -c "printf x >> /backups/$set_plain.dump"
if img -e CONFIRM_RESTORE=yes -e PGDATABASE=restored_plain -v "$plain:/backups:ro" -v "$media_restored:/data/media" "$IMG" restore "$set_plain" 2>/dev/null; then
  fail "restore accepted a damaged backup"
fi

# ---- 2. encrypted set ----
step "backup (encrypted)"
enc_dir="$(new_dir enc)"
log="$(img -e BACKUP_PASSPHRASE="$PASS_ONE" -v "$enc_dir:/backups" -v "$media:/data/media:ro" "$IMG" now)"
echo "$log" | grep -q '"event":"backup.done"' || fail "no backup.done log line (encrypted)"
echo "$log" | grep -q "$PASS_ONE" && fail "the passphrase appears in the log"
set_enc="$(check_set "$enc_dir" yes "$PASS_ONE")"

step "restore (encrypted)"
media_restored2="$(new_dir media_restored2)"
if img -e CONFIRM_RESTORE=yes -e BACKUP_PASSPHRASE="$PASS_TWO" -e PGDATABASE=restored_enc \
  -v "$enc_dir:/backups:ro" -v "$media_restored2:/data/media" "$IMG" restore "$set_enc" 2>/dev/null; then
  fail "restore accepted a wrong passphrase"
fi
restore_into "$enc_dir" "$set_enc" restored_enc "$media_restored2" "$PASS_ONE"

# ---- 3. failure manifest ----
step "failure writes a failed manifest and no set files"
bad="$(new_dir bad)"
if img -e PGPORT=1 -v "$bad:/backups" -v "$media:/data/media:ro" "$IMG" now >/dev/null 2>&1; then
  fail "backup succeeded against a closed port"
fi
grep -q '"status": "failed"' "$bad/latest.json" || fail "latest.json is not marked failed"
grep -q '"error": "pg_dump failed"' "$bad/latest.json" || fail "unexpected error text: $(cat "$bad/latest.json")"
ls -A "$bad" | grep -E '\.dump|-media|\.sha256|^\.stage' && fail "files left behind after a failed backup"

# ---- 4. retention ----
step "retention keeps the newest BACKUP_KEEP_MIN sets"
keep="$(new_dir keep)"
for _ in 1 2 3; do
  img -e BACKUP_KEEP_DAYS=0 -e BACKUP_KEEP_MIN=2 -v "$keep:/backups" -v "$media:/data/media:ro" "$IMG" now >/dev/null
  sleep 1.2
done
count="$(ls "$keep"/heardloop-*.json | wc -l | tr -d ' ')"
[ "$count" = "2" ] || fail "expected 2 sets after pruning, found $count"
newest="$(sed -n 's/^  "set": "\(heardloop-[0-9TZ]*\)".*/\1/p' "$keep/latest.json")"
[ -f "$keep/$newest.json" ] || fail "the newest set was pruned"

# ---- 5. scheduler loop stops cleanly on TERM ----
step "service mode stops cleanly"
loop="$(new_dir loop)"
docker run -d --name hl-smoke-loop --network "$NET" "${hardening[@]}" "${env_flags[@]}" -e BACKUP_HOUR_UTC=03 \
  -v "$loop:/backups" -v "$media:/data/media:ro" "$IMG" >/dev/null
sleep 2
docker logs hl-smoke-loop 2>&1 | grep -q '"event":"backup.scheduled"' || fail "scheduler did not log the next run"
docker stop -t 5 hl-smoke-loop >/dev/null
[ "$(docker inspect -f '{{.State.ExitCode}}' hl-smoke-loop)" = "0" ] || fail "scheduler did not exit 0 on TERM"
docker rm hl-smoke-loop >/dev/null

echo "backup smoke test passed"
