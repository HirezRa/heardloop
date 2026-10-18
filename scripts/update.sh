#!/usr/bin/env bash
# Updates Heardloop on this machine to a release tag (or the newest vX.Y.Z).
# Usage:  ./scripts/update.sh
#         ./scripts/update.sh v0.9.0 -y
#         ./scripts/update.sh --dry-run
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

[[ -f VERSION ]] || fail "Run this script from a Heardloop checkout (VERSION file missing)."

current_version="$(tr -d '[:space:]' < VERSION)"
current_tag="v${current_version}"

if [[ "$DRY" -eq 0 ]]; then
  echo "Fetching tags..."
  git fetch --tags --prune
fi

if [[ -z "$TAG" ]]; then
  TAG="$(git tag -l 'v*.*.*' --sort=-v:refname | head -n1 || true)"
  [[ -n "$TAG" ]] || TAG="$current_tag"
fi
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Tag must look like v1.2.3 (got: $TAG)."

release_url="https://github.com/HirezRa/heardloop/releases/tag/${TAG}"
echo "Current: ${current_tag}"
echo "Target:  ${TAG}"
echo "Release: ${release_url}"

if [[ "$DRY" -eq 1 ]]; then
  echo ""
  echo "Dry-run plan (no changes):"
  echo "  1. Refuse if tracked files are dirty (untracked .env / override / backups/ ok)"
  echo "  2. git fetch --tags --prune"
  echo "  3. scripts/backup-now.sh (abort unless status=ok)"
  echo "  4. git checkout ${TAG}"
  echo "  5. docker compose build --build-arg GIT_SHA=<short> ; docker compose up -d"
  echo "  6. Wait up to 120s for api healthy; GET /api/v1/health; print VERSION"
  echo "On failure after checkout: git checkout ${current_tag} ; docker compose up -d --build"
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

echo "Checking out ${TAG}..."
git checkout "$TAG"

sha="$(git rev-parse --short HEAD)"
export GIT_SHA="$sha"
echo "Building (GIT_SHA=${sha})..."
if ! docker compose build --build-arg "GIT_SHA=${sha}"; then
  echo "Build failed. Rollback: git checkout ${current_tag} ; docker compose up -d --build"
  [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
  exit 1
fi
if ! docker compose up -d; then
  echo "Compose up failed. Rollback: git checkout ${current_tag} ; docker compose up -d --build"
  [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
  exit 1
fi

echo "Waiting for api (up to 120s)..."
deadline=$((SECONDS + 120))
healthy=0
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
  echo "API not healthy in time. Rollback: git checkout ${current_tag} ; docker compose up -d --build"
  [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
  exit 1
fi

if ! curl -fsS "http://localhost:8380/api/v1/health" >/dev/null; then
  echo "Health check failed. Rollback: git checkout ${current_tag} ; docker compose up -d --build"
  [[ -n "$backup_set" ]] && echo "Backup set: ${backup_set}"
  exit 1
fi

new_version="$(tr -d '[:space:]' < VERSION)"
echo "Updated to Heardloop v${new_version} (${TAG})."
[[ -n "$backup_set" ]] && echo "Backup set kept: ${backup_set}"
