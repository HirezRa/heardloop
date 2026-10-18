#!/bin/sh
# Restore one backup set into the database named by PGDATABASE and into the media directory.
#   CONFIRM_RESTORE=yes restore.sh <set-name>      (set name: heardloop-YYYYMMDDTHHMMSSZ)
# Stop `api` and `worker` first. The database objects are replaced (pg_restore --clean) in one
# transaction; the media directory is emptied and refilled. Everything is verified before anything is
# changed: checksums, decryption, archive readability. The media directory must be mounted read-write.
set -eu
umask 077
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi

# Compose passes POSTGRES_PASSWORD (same as the db service). libpq reads PGPASSWORD; never echo it.
# `?` (no colon): the variable must be set; empty is allowed (throwaway CI databases with trust auth).
export PGPASSWORD="${POSTGRES_PASSWORD?POSTGRES_PASSWORD is not set}"

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
MEDIA_ROOT="${MEDIA_ROOT:-/data/media}"

say() { printf 'restore: %s\n' "$*"; }
die() {
  printf 'restore: ERROR: %s\n' "$*" >&2
  exit 1
}

SET="${1:-}"
[ -n "$SET" ] || die "usage: CONFIRM_RESTORE=yes restore <set-name>   (for example heardloop-20261018T010000Z)"
case "$SET" in
  heardloop-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) die "not a valid set name: expected heardloop-YYYYMMDDTHHMMSSZ" ;;
esac
if [ "${CONFIRM_RESTORE:-}" != "yes" ]; then
  die "refusing to run: this replaces the database and the media files. Set CONFIRM_RESTORE=yes to go on."
fi

MANIFEST="$BACKUP_ROOT/$SET.json"
[ -f "$MANIFEST" ] || die "no manifest for $SET in the backup directory"
grep -q '"status": "ok"' "$MANIFEST" || die "$SET is not a successful backup (see its manifest)"

if [ -f "$BACKUP_ROOT/$SET.dump.enc" ]; then
  ENC=1
  DUMP="$SET.dump.enc"
  MEDIA="$SET-media.tar.gz.enc"
  [ -n "${BACKUP_PASSPHRASE:-}" ] || die "this set is encrypted: set BACKUP_PASSPHRASE to the passphrase used for the backup"
else
  ENC=0
  DUMP="$SET.dump"
  MEDIA="$SET-media.tar.gz"
fi
[ -f "$BACKUP_ROOT/$DUMP" ] || die "database file $DUMP is missing"
[ -f "$BACKUP_ROOT/$MEDIA" ] || die "media file $MEDIA is missing"

# 1. Verify before touching anything
[ -f "$BACKUP_ROOT/$SET.sha256" ] || die "checksum file $SET.sha256 is missing"
(cd "$BACKUP_ROOT" && sha256sum -c "$SET.sha256" >/dev/null 2>&1) || die "checksum mismatch: the backup files are damaged or were changed"
say "checksums match"

[ -d "$MEDIA_ROOT" ] || die "media directory not found"
# Really write a file: `test -w` says yes to root even on a read-only mount.
probe="$MEDIA_ROOT/.restore-probe-$$"
if ! (: >"$probe") 2>/dev/null; then
  die "the media directory is read-only: use scripts/restore (or the restore service), which mounts it read-write"
fi
rm -f "$probe"

WORK="/tmp/restore-$$"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT
trap 'exit 143' TERM INT

openssl_dec() {
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:BACKUP_PASSPHRASE -in "$1"
}
media_stream() {
  if [ "$ENC" -eq 1 ]; then openssl_dec "$BACKUP_ROOT/$MEDIA"; else cat "$BACKUP_ROOT/$MEDIA"; fi
}

if [ "$ENC" -eq 1 ]; then
  openssl_dec "$BACKUP_ROOT/$DUMP" >"$WORK/db.dump" 2>/dev/null || die "could not decrypt the database file: wrong passphrase?"
else
  cp "$BACKUP_ROOT/$DUMP" "$WORK/db.dump"
fi
pg_restore --list "$WORK/db.dump" >/dev/null 2>&1 || die "the database file is not a valid dump (wrong passphrase?)"
media_stream 2>/dev/null | tar -tzf - >/dev/null 2>&1 || die "the media archive cannot be read (wrong passphrase?)"
say "files verified"

# 2. Other connections would block --clean or see a half-restored database.
others="$(psql --no-psqlrc -Atc "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()" 2>/dev/null || echo "?")"
if [ "$others" != "0" ]; then
  say "WARNING: $others other connection(s) to the database. Stop api and worker before restoring."
fi

# 3. Database: one transaction, so a failure leaves the old data in place.
say "restoring the database (replaces existing objects)"
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --dbname="${PGDATABASE:-heardloop}" "$WORK/db.dump" ||
  die "pg_restore failed; the database was left as it was"
say "database restored"

# 4. Media: empty the directory (not the mount point itself), then unpack.
say "replacing the media files"
find "$MEDIA_ROOT" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
umask 022 # the application reads these files as another user
media_stream | tar -xzf - -C "$MEDIA_ROOT" || die "media extraction failed after the database was restored; re-run the restore"
files="$(find "$MEDIA_ROOT" -type f | wc -l | tr -d ' ')"
say "media restored ($files files)"
say "done: $SET restored. Start api and worker again."
