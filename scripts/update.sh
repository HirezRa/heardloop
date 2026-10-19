#!/usr/bin/env bash
# Updates Heardloop on this machine to a release tag (or the newest vX.Y.Z).
# Usage:  ./scripts/update.sh
#         ./scripts/update.sh v0.9.0 -y
#         ./scripts/update.sh --dry-run
# Compares the RUNNING API version (image label or app_version()), not only VERSION on disk.
# Never prints environment variables. Never touches .env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

YES=0
DRY=0
TAG=""

for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    -n|--dry-run|--what-if) DRY=1 ;;
    v*.*.*) TAG="$arg" ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

fail() { echo "$1" >&2; exit 1; }

files_version_tag() {
  local ver
  ver="$(tr -d '[:space:]' < VERSION)"
  [[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "VERSION is not semver: ${ver}"
  printf 'v%s' "$ver"
}

running_version_tag() {
  # Prints vX.Y.Z or empty if stack down / unknown.
  local cid label from_app
  cid="$(docker compose ps -q api 2>/dev/null | head -n1 || true)"
  [[ -n "$cid" ]] || { printf ''; return 0; }

  label="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$cid" 2>/dev/null || true)"
  label="$(printf '%s' "$label" | tr -d '[:space:]')"
  if [[ "$label" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'v%s' "$label"
    return 0
  fi
  if [[ "$label" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s' "$label"
    return 0
  fi

  from_app="$(docker compose exec -T api python -c 'from heardloop.version import app_version; print(app_version())' 2>/dev/null | tail -n1 || true)"
  from_app="$(printf '%s' "$from_app" | tr -d '[:space:]')"
  if [[ "$from_app" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'v%s' "$from_app"
    return 0
  fi
  if [[ "$from_app" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s' "$from_app"
    return 0
  fi
  printf ''
}

rebuild_and_up() {
  local sha="$1" version_plain="$2" rollback_hint="$3" backup_set="$4"
  export GIT_SHA="$sha"
  export VERSION="$version_plain"
  echo "Building (VERSION=${version_plain} GIT_SHA=${sha})..."
  if ! docker compose build --build-arg "GIT_SHA=${sha}" --build-arg "VERSION=${version_plain}"; then
    echo "Build failed. Rollback: ${rollback_hint}"
    [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
    exit 1
  fi
  if ! docker compose up -d; then
    echo "Compose up failed. Rollback: ${rollback_hint}"
    [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
    exit 1
  fi

  echo "Waiting for api (up to 120s)..."
  local deadline=$((SECONDS + 120)) healthy=0
  while (( SECONDS < deadline )); do
    sleep 3
    if docker compose ps --format json 2>/dev/null | grep -q '"Service":"api"' \
      && docker compose ps --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
      healthy=1
      break
    fi
    if docker compose ps 2>/dev/null | grep -E 'api[[:space:]].*\(healthy\)' >/dev/null; then
      healthy=1
      break
    fi
  done
  if [[ "$healthy" -ne 1 ]]; then
    echo "API not healthy in time. Rollback: ${rollback_hint}"
    [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
    exit 1
  fi

  if ! curl -fsS "http://localhost:8380/api/v1/health" >/dev/null; then
    echo "Health check failed. Rollback: ${rollback_hint}"
    [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
    exit 1
  fi
}

[[ -f VERSION ]] || fail "Run this script from a Heardloop checkout (VERSION file missing)."

files_tag="$(files_version_tag)"

if [[ "$DRY" -eq 0 ]]; then
  echo "Fetching tags..."
  git fetch --tags --prune
fi

if [[ -z "$TAG" ]]; then
  TAG="$(git tag -l 'v*.*.*' --sort=-v:refname | head -n1 || true)"
  [[ -n "$TAG" ]] || TAG="$files_tag"
fi
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Tag must look like v1.2.3 (got: $TAG)."

running_tag="$(running_version_tag)"
stack_down=0
if [[ -z "$running_tag" ]]; then
  echo "Heardloop is not running"
  running_display="unknown"
  stack_down=1
else
  running_display="$running_tag"
fi

release_url="https://github.com/HirezRa/heardloop/releases/tag/${TAG}"
echo "Running: ${running_display}"
echo "Files:   ${files_tag}"
echo "Target:  ${TAG}"
echo "Release: ${release_url}"

need_checkout=0
need_rebuild=0
[[ "$files_tag" != "$TAG" ]] && need_checkout=1
if [[ "$stack_down" -eq 1 || "$running_tag" != "$TAG" ]]; then
  need_rebuild=1
fi

if [[ "$need_rebuild" -eq 0 ]]; then
  echo "already up to date"
  exit 0
fi

plan_rebuild_only=0
if [[ "$need_checkout" -eq 0 && "$need_rebuild" -eq 1 ]]; then
  plan_rebuild_only=1
fi

if [[ "$DRY" -eq 1 ]]; then
  echo ""
  echo "Dry-run plan (no changes):"
  if [[ "$plan_rebuild_only" -eq 1 ]]; then
    echo "  Files are already at ${TAG}; rebuilding containers"
    echo "  1. Refuse if tracked files are dirty (untracked .env / override / backups/ ok)"
    echo "  2. git fetch --tags --prune"
    echo "  3. scripts/backup-now.sh (abort unless status=ok)"
    echo "  4. docker compose build --build-arg VERSION=... --build-arg GIT_SHA=... ; docker compose up -d"
    echo "  5. Wait up to 120s for api healthy; confirm running version is ${TAG}"
  else
    echo "  1. Refuse if tracked files are dirty (untracked .env / override / backups/ ok)"
    echo "  2. git fetch --tags --prune"
    echo "  3. scripts/backup-now.sh (abort unless status=ok)"
    echo "  4. git checkout ${TAG}"
    echo "  5. docker compose build --build-arg VERSION=... --build-arg GIT_SHA=... ; docker compose up -d"
    echo "  6. Wait up to 120s for api healthy; confirm running version is ${TAG}"
    echo "On failure after checkout: git checkout ${files_tag} ; docker compose up -d --build"
  fi
  exit 0
fi

blocked="$(git status --porcelain | while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  path="${line:3}"
  path="${path#\"}"
  path="${path%\"}"
  case "$path" in
    docker-compose.override.yml|.env|backups|backups/*) continue ;;
    *) printf '%s\n' "$line" ;;
  esac
done)"
if [[ -n "$blocked" ]]; then
  fail "Working tree has tracked changes. Commit or stash them first:
$blocked"
fi

if [[ "$YES" -eq 0 ]]; then
  read -r -p "Continue? (y/N) " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

echo "Running backup..."
bash "$ROOT/scripts/backup-now.sh" || fail "Backup did not report ok; update aborted."

backup_set=""
if json="$(docker compose run -T --rm --no-deps --entrypoint cat backup /backups/latest.json 2>/dev/null)"; then
  backup_set="$(printf '%s' "$json" | sed -n 's/.*"set"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
fi

rollback_files="$files_tag"
if [[ "$plan_rebuild_only" -eq 1 ]]; then
  echo "Files are already at ${TAG}; rebuilding containers"
else
  echo "Checking out ${TAG}..."
  git checkout "$TAG"
fi

sha="$(git rev-parse --short HEAD)"
version_plain="$(tr -d '[:space:]' < VERSION)"
if [[ "$plan_rebuild_only" -eq 1 ]]; then
  rollback_hint="docker compose up -d --build"
else
  rollback_hint="git checkout ${rollback_files} ; docker compose up -d --build"
fi

rebuild_and_up "$sha" "$version_plain" "$rollback_hint" "$backup_set"

got="$(running_version_tag)"
if [[ "$got" != "$TAG" ]]; then
  [[ -n "$got" ]] || got="unknown"
  echo "Running version after update is ${got}, expected ${TAG}. Rollback: ${rollback_hint}"
  [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
  exit 1
fi

echo "Updated to Heardloop ${TAG} (running confirmed)."
[[ -n "$backup_set" ]] && echo "Backup set kept: ${backup_set}"
