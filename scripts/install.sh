#!/usr/bin/env bash
# Heardloop installer for Linux and macOS.
#
# Installs what is missing (git, curl, openssl, Docker Engine with the Compose and Buildx plugins on Linux,
# Docker Desktop on macOS), downloads Heardloop at the newest release tag, creates .env with random secrets,
# asks for the LinkedIn app keys (optional), starts the stack, waits until it is healthy and creates the
# owner account. Safe to run again: it keeps an existing clone, .env and database.
#
# Review it before you run it:
#   curl -fsSLo heardloop-install.sh https://raw.githubusercontent.com/HirezRa/heardloop/main/scripts/install.sh
#   less heardloop-install.sh
#   bash heardloop-install.sh
# From a clone:
#   ./scripts/install.sh
#
# Options:
#   --dir PATH         where Heardloop lives (default: this clone, else ~/heardloop)
#   --ref REF          release tag or branch to check out (default: newest vX.Y.Z tag)
#   --lang en|he       interface language (default: ask, en)
#   --with-ai          add a local Ollama service for the optional AI writing assistant
#   --ai-model NAME    model for --with-ai (default: llama3.1:8b)
#   --ollama-tag TAG   Ollama image tag for --with-ai (default below; check hub.docker.com/r/ollama/ollama)
#   --skip-docker      never install Docker, only check it
#   --no-owner         do not create the owner account
#   -y, --yes          accept defaults, no questions (LinkedIn keys and owner are skipped)
#   -h, --help         show this help
#
# Never prints secrets and never exports HEARDLOOP_TOKEN_KEY or POSTGRES_PASSWORD to the shell
# (Compose would prefer them over .env).
set -euo pipefail

REPO_URL="https://github.com/HirezRa/heardloop.git"
WEB_URL="http://localhost:8380"
WEB_PORT=8380
HEALTH_TIMEOUT=420
DEFAULT_AI_MODEL="llama3.1:8b"
DEFAULT_OLLAMA_TAG="0.40.5"

DIR=""
REF=""
LANG_CHOICE=""
WITH_AI=0
AI_MODEL_CHOICE="$DEFAULT_AI_MODEL"
OLLAMA_TAG="$DEFAULT_OLLAMA_TAG"
SKIP_DOCKER=0
NO_OWNER=0
YES=0

# apt-get with a lock wait: fresh Ubuntu holds the lock for unattended-upgrades.
APT=(apt-get -o DPkg::Lock::Timeout=300)

# ---------- output ----------
if [[ -t 1 ]]; then
  C_B=$'\033[1m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_0=$'\033[0m'
else
  C_B=""; C_G=""; C_Y=""; C_R=""; C_0=""
fi
step() { printf '\n%s==> %s%s\n' "$C_B" "$1" "$C_0"; }
info() { printf '    %s\n' "$1"; }
ok()   { printf '    %s%s%s\n' "$C_G" "$1" "$C_0"; }
warn() { printf '    %s%s%s\n' "$C_Y" "$1" "$C_0" >&2; }
die()  { printf '\n%sError:%s %s\n' "$C_R" "$C_0" "$1" >&2; exit 1; }

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ---------- arguments ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="${2:?--dir needs a path}"; shift 2 ;;
    --ref) REF="${2:?--ref needs a tag or branch}"; shift 2 ;;
    --lang) LANG_CHOICE="${2:?--lang needs en or he}"; shift 2 ;;
    --with-ai) WITH_AI=1; shift ;;
    --ai-model) AI_MODEL_CHOICE="${2:?--ai-model needs a name}"; shift 2 ;;
    --ollama-tag) OLLAMA_TAG="${2:?--ollama-tag needs a tag}"; shift 2 ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --no-owner) NO_OWNER=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done
[[ -z "$LANG_CHOICE" || "$LANG_CHOICE" == "en" || "$LANG_CHOICE" == "he" ]] || die "--lang must be en or he"
[[ "$AI_MODEL_CHOICE" =~ ^[A-Za-z0-9._:/-]+$ ]] || die "--ai-model has unexpected characters"
[[ "$OLLAMA_TAG" =~ ^[A-Za-z0-9._-]+$ ]] || die "--ollama-tag has unexpected characters"

# ---------- questions (read from the terminal, so `bash <(curl ...)` works too) ----------
HAVE_TTY=0
if [[ -r /dev/tty ]] && { : < /dev/tty; } 2>/dev/null; then HAVE_TTY=1; fi
[[ $YES -eq 1 || $HAVE_TTY -eq 1 ]] || die "No terminal for questions. Run it in a terminal, or add --yes."

# ask "Question" "default" -> REPLY
ask() {
  local q="$1" def="${2:-}"
  if [[ $YES -eq 1 || $HAVE_TTY -eq 0 ]]; then REPLY="$def"; return; fi
  if [[ -n "$def" ]]; then printf '    %s [%s]: ' "$q" "$def" > /dev/tty; else printf '    %s: ' "$q" > /dev/tty; fi
  IFS= read -r REPLY < /dev/tty || REPLY=""
  [[ -n "$REPLY" ]] || REPLY="$def"
}
# ask_secret "Question" -> REPLY (not echoed)
ask_secret() {
  REPLY=""
  [[ $YES -eq 1 || $HAVE_TTY -eq 0 ]] && return
  printf '    %s: ' "$1" > /dev/tty
  IFS= read -rs REPLY < /dev/tty || REPLY=""
  printf '\n' > /dev/tty
}
# confirm "Question" y|n -> 0 for yes
confirm() {
  local def="${2:-y}" hint="Y/n"
  [[ "$def" == "n" ]] && hint="y/N"
  ask "$1 ($hint)" ""
  [[ -n "$REPLY" ]] || REPLY="$def"
  [[ "$REPLY" =~ ^[Yy] ]]
}

# ---------- system ----------
OS="$(uname -s)"
SUDO=""
if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
fi
need_root() {
  [[ $EUID -eq 0 || -n "$SUDO" ]] || die "Installing packages needs root, and sudo is not available. Install $1 by hand and run this again."
}

DISTRO=""; DISTRO_LIKE=""; CODENAME=""; UBUNTU_CODENAME_VALUE=""
if [[ "$OS" == "Linux" && -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO="${ID:-}"; DISTRO_LIKE="${ID_LIKE:-}"; CODENAME="${VERSION_CODENAME:-}"
  UBUNTU_CODENAME_VALUE="${UBUNTU_CODENAME:-}"
fi

pkg_family() {
  case " $DISTRO $DISTRO_LIKE " in
    *" ubuntu "*|*" debian "*) echo apt ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*) echo dnf ;;
    *) echo unknown ;;
  esac
}

is_wsl() { [[ "$OS" == "Linux" ]] && grep -qi microsoft /proc/version 2>/dev/null; }

install_packages() {
  # install_packages <package...>
  case "$(pkg_family)" in
    apt) need_root "$*"; $SUDO "${APT[@]}" update -qq; $SUDO env DEBIAN_FRONTEND=noninteractive "${APT[@]}" install -y -qq "$@" ;;
    dnf) need_root "$*"; $SUDO dnf install -y -q "$@" ;;
    *) die "Unsupported Linux distribution (${DISTRO:-unknown}). Install $* by hand and run this again." ;;
  esac
}

ensure_base_tools() {
  step "Checking basic tools"
  local missing=()
  for t in git curl openssl; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
  if [[ ${#missing[@]} -eq 0 ]]; then ok "git, curl and openssl are installed"; return; fi
  info "Missing: ${missing[*]}"
  if [[ "$OS" == "Darwin" ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install "${missing[@]}"
    else
      die "Install ${missing[*]} first (for git: xcode-select --install, or Homebrew from brew.sh), then run this again."
    fi
  else
    install_packages "${missing[@]}" ca-certificates
  fi
  ok "Installed: ${missing[*]}"
}

# ---------- Docker ----------
DOCKER=(docker)

docker_ready() { "${DOCKER[@]}" info >/dev/null 2>&1; }
compose_ready() { "${DOCKER[@]}" compose version >/dev/null 2>&1; }

install_docker_apt() {
  local dist codename
  if [[ " $DISTRO $DISTRO_LIKE " == *" ubuntu "* ]]; then
    dist="ubuntu"; codename="${UBUNTU_CODENAME_VALUE:-$CODENAME}"
  else
    dist="debian"; codename="$CODENAME"
  fi
  [[ -n "$codename" ]] || die "Cannot tell the ${dist} release name. Install Docker by hand: https://docs.docker.com/engine/install/"
  info "Adding Docker's official apt repository (${dist} ${codename})"
  $SUDO "${APT[@]}" update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive "${APT[@]}" install -y -qq ca-certificates curl
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO curl -fsSL "https://download.docker.com/linux/${dist}/gpg" -o /etc/apt/keyrings/docker.asc
  $SUDO chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$dist" "$codename" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO "${APT[@]}" update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive "${APT[@]}" install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_dnf() {
  local repo
  # Exact ID only: Rocky/Alma list "fedora" in ID_LIKE and must use the centos rebuild repo.
  case "$DISTRO" in
    fedora) repo="fedora" ;;
    rhel) repo="rhel" ;;
    *) repo="centos" ;;  # CentOS Stream, Rocky, AlmaLinux and other RHEL rebuilds
  esac
  info "Adding Docker's official dnf repository (${repo})"
  $SUDO curl -fsSL "https://download.docker.com/linux/${repo}/docker-ce.repo" -o /etc/yum.repos.d/docker-ce.repo
  $SUDO dnf install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

ensure_docker_linux() {
  if command -v docker >/dev/null 2>&1 && compose_ready; then
    ok "Docker and the Compose plugin are installed"
  elif [[ $SKIP_DOCKER -eq 1 ]]; then
    die "Docker or the Compose plugin is missing and --skip-docker was given. See https://docs.docker.com/engine/install/"
  elif is_wsl; then
    die "This is WSL. Install Docker Desktop on Windows (scripts/install.ps1 does it), turn on WSL integration for this distribution, then run this again."
  else
    command -v docker >/dev/null 2>&1 && info "Docker is installed but the Compose plugin is missing"
    confirm "Install Docker Engine with the Compose and Buildx plugins from Docker's official repository?" y \
      || die "Docker is required. See https://docs.docker.com/engine/install/"
    need_root "Docker"
    case "$(pkg_family)" in
      apt) install_docker_apt ;;
      dnf) install_docker_dnf ;;
      *) die "Unsupported Linux distribution (${DISTRO:-unknown}). Install Docker by hand: https://docs.docker.com/engine/install/" ;;
    esac
    if command -v systemctl >/dev/null 2>&1; then $SUDO systemctl enable --now docker >/dev/null; fi
    ok "Docker installed"
  fi

  if docker_ready; then return; fi
  if [[ -n "$SUDO" ]] && $SUDO docker info >/dev/null 2>&1; then
    DOCKER=("$SUDO" docker)
    warn "Your user cannot reach Docker yet; using sudo for this run."
    if [[ $YES -eq 0 ]] && confirm "Add $(id -un) to the docker group, so you can run docker without sudo after you sign in again? (members of this group have root-level access)" n; then
      $SUDO usermod -aG docker "$(id -un)"
      info "Done. Sign out and in again for it to take effect."
    fi
    return
  fi
  if command -v systemctl >/dev/null 2>&1; then $SUDO systemctl start docker || true; fi
  docker_ready || { [[ -n "$SUDO" ]] && $SUDO docker info >/dev/null 2>&1 && DOCKER=("$SUDO" docker); } \
    || die "Docker is installed but not running. Start it (sudo systemctl start docker) and run this again."
}

ensure_docker_macos() {
  if ! command -v docker >/dev/null 2>&1; then
    [[ $SKIP_DOCKER -eq 0 ]] || die "Docker Desktop is missing and --skip-docker was given."
    command -v brew >/dev/null 2>&1 \
      || die "Install Docker Desktop from https://www.docker.com/products/docker-desktop/ (or install Homebrew from brew.sh), then run this again."
    confirm "Install Docker Desktop with Homebrew?" y || die "Docker Desktop is required."
    brew install --cask docker-desktop 2>/dev/null || brew install --cask docker
  fi
  if ! docker_ready; then
    info "Starting Docker Desktop. The first time, accept its terms in the window that opens."
    open -a Docker || true
    local waited=0
    until docker_ready; do
      sleep 5; waited=$((waited + 5))
      [[ $waited -lt 300 ]] || die "Docker Desktop did not start within 5 minutes. Start it, wait for \"Engine running\", then run this again."
    done
  fi
  compose_ready || die "Docker Compose is missing. Update Docker Desktop and run this again."
  ok "Docker Desktop is running"
}

ensure_docker() {
  step "Checking Docker"
  case "$OS" in
    Linux) ensure_docker_linux ;;
    Darwin) ensure_docker_macos ;;
    *) die "Unsupported system: $OS. On Windows use scripts/install.ps1." ;;
  esac
  local v; v="$("${DOCKER[@]}" compose version --short 2>/dev/null || true)"
  ok "Docker Compose ${v:-found}"
}

# ---------- source ----------
latest_tag() {
  # Portable sort: macOS may lack sort -V.
  git ls-remote --tags --refs "$REPO_URL" 2>/dev/null | awk -F/ '{print $3}' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1 | sed 's/^/v/'
}

get_source() {
  step "Getting Heardloop"
  local here=""
  # Prefer this clone when the script lives inside a Heardloop tree; ignore failure if dirname is unset.
  if here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && cd .. && pwd)"; then :; else here=""; fi
  if [[ -z "$DIR" && -n "$here" && -f "$here/docker-compose.yml" ]] && grep -q '^name: heardloop' "$here/docker-compose.yml"; then
    DIR="$here"
    info "Using this clone: $DIR"
    [[ -z "$REF" ]] && { ok "Keeping the checked-out version ($(tr -d '[:space:]' < "$DIR/VERSION"))"; return; }
  fi
  [[ -n "$DIR" ]] || DIR="$HOME/heardloop"

  if [[ -z "$REF" ]]; then
    REF="$(latest_tag)"
    [[ -n "$REF" ]] || die "Could not read the release tags from GitHub. Check the network, or pass --ref main."
  fi

  if [[ -d "$DIR/.git" ]]; then
    info "Found an existing clone in $DIR"
    git -C "$DIR" fetch -q --tags origin
    if [[ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]]; then
      warn "The clone has local changes; leaving the checked-out version as it is."
      return
    fi
  elif [[ -e "$DIR" && -n "$(ls -A "$DIR" 2>/dev/null)" ]]; then
    die "$DIR exists and is not empty. Choose another folder with --dir."
  else
    info "Cloning into $DIR"
    git clone -q "$REPO_URL" "$DIR"
  fi
  git -C "$DIR" -c advice.detachedHead=false checkout -q "$REF"
  ok "Heardloop ${REF} in $DIR"
}

# Refuse a second folder against the same compose project / DB volume (name: heardloop is fixed).
check_existing_install() {
  local wd
  wd="$("${DOCKER[@]}" ps -a --filter label=com.docker.compose.project=heardloop \
        --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u | head -n1 || true)"
  if [[ -n "$wd" && "$wd" != "$DIR" ]]; then
    die "Heardloop is already installed on this machine, in $wd. Run the installer from that folder (--dir $wd), or update it with $wd/scripts/update.sh."
  fi
  if [[ -z "$wd" && ! -f "$DIR/.env" ]] && "${DOCKER[@]}" volume inspect heardloop_db-data >/dev/null 2>&1; then
    die "A Heardloop database (Docker volume heardloop_db-data) already exists on this machine, but $DIR has no .env. Use the folder and .env it was created with; a new .env cannot open it."
  fi
  return 0
}

# ---------- .env ----------
ENV_FILE=""

env_get() { awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"; }

# env_set KEY VALUE: replace or append one line; the value goes through the environment of awk only.
env_set() {
  local tmp
  [[ "$2" != *$'\n'* ]] || die "Value for $1 contains a line break."
  tmp="$(umask 077 && mktemp "$DIR/.env.XXXXXX")"
  HL_K="$1" HL_V="$2" awk '
    BEGIN { k = ENVIRON["HL_K"]; v = ENVIRON["HL_V"]; done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

create_env() {
  step "Creating .env"
  ENV_FILE="$DIR/.env"
  if [[ -f "$ENV_FILE" ]]; then
    ok ".env already exists; keeping its secrets"
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    return
  fi
  [[ -f "$DIR/.env.example" ]] || die ".env.example is missing in $DIR"
  (
    umask 077
    HL_DB="$(openssl rand -hex 24)" HL_KEY="$(openssl rand -base64 32 | tr -d '\n')" awk '
      /^POSTGRES_PASSWORD=/   { print "POSTGRES_PASSWORD=" ENVIRON["HL_DB"]; next }
      /^HEARDLOOP_TOKEN_KEY=/ { print "HEARDLOOP_TOKEN_KEY=" ENVIRON["HL_KEY"]; next }
      { print }
    ' "$DIR/.env.example" > "$ENV_FILE"
  )
  chmod 600 "$ENV_FILE"
  # Shape checks only — never print the values. Avoid a KEY=<charset> regex that secret scanners treat as a password.
  local db_pw tok body
  db_pw="$(env_get POSTGRES_PASSWORD)"
  tok="$(env_get HEARDLOOP_TOKEN_KEY)"
  [[ ${#db_pw} -eq 48 && -z "$(printf '%s' "$db_pw" | tr -d '0-9a-f')" ]] \
    || die "Could not write the database password to .env"
  body="${tok%=}"
  [[ ${#tok} -eq 44 && "$tok" == *= && ${#body} -eq 43 && -z "$(printf '%s' "$body" | tr -d 'A-Za-z0-9+/')" ]] \
    || die "Could not write the encryption key to .env"
  ok ".env created with a random database password and encryption key (not shown)"
  warn "Back up $ENV_FILE now (password manager or encrypted USB). Without HEARDLOOP_TOKEN_KEY a backup cannot be restored."
}

configure_env() {
  step "Settings"
  local cur
  cur="$(env_get UI_LANGUAGE)"
  if [[ -z "$LANG_CHOICE" ]]; then
    ask "Interface language, en or he" "${cur:-en}"
    LANG_CHOICE="$REPLY"
  fi
  [[ "$LANG_CHOICE" == "en" || "$LANG_CHOICE" == "he" ]] || { warn "Unknown language '$LANG_CHOICE', using en"; LANG_CHOICE="en"; }
  env_set UI_LANGUAGE "$LANG_CHOICE"
  ok "Interface language: $LANG_CHOICE"

  cur="$(env_get LINKEDIN_CLIENT_ID)"
  if [[ -n "$cur" && "$cur" != "<client-id>" ]]; then
    ok "LinkedIn app keys are already set"
    return
  fi
  info "Heardloop needs your own LinkedIn developer app to publish (free)."
  info "Create it at https://www.linkedin.com/developers/apps with the products"
  info "\"Sign In with LinkedIn using OpenID Connect\" and \"Share on LinkedIn\", and the redirect URL"
  info "${WEB_URL}/api/v1/linkedin/callback   (steps: docs/INSTALL.md, section 4)"
  if [[ $YES -eq 0 ]] && confirm "Do you have the Client ID and Client Secret now?" n; then
    ask "Client ID" ""
    local id="$REPLY"
    [[ "$id" =~ ^[A-Za-z0-9]+$ ]] || die "That Client ID does not look right (letters and digits only)."
    ask_secret "Client Secret (not shown)"
    local secret="$REPLY"
    [[ -n "$secret" ]] || die "The Client Secret is empty."
    env_set LINKEDIN_CLIENT_ID "$id"
    env_set LINKEDIN_CLIENT_SECRET "$secret"
    secret=""
    ok "LinkedIn app keys saved in .env"
  else
    info "Skipped. Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to .env later, then run: docker compose up -d"
  fi
}

# ---------- optional AI ----------
setup_ai() {
  if [[ $WITH_AI -eq 0 ]]; then
    [[ $YES -eq 1 ]] && return
    confirm "Add the optional AI writing assistant with a local Ollama model? (needs several GB of disk and memory)" n || return 0
    WITH_AI=1
  fi
  step "AI writing assistant (Ollama, local)"
  local override="$DIR/docker-compose.override.yml"
  if [[ -f "$override" ]]; then
    if grep -q '^  ollama:' "$override"; then
      ok "docker-compose.override.yml already has an ollama service"
    else
      warn "docker-compose.override.yml exists; not changing it. Add the ollama service from docs/INSTALL.md, section 13, then run this again."
      return 0
    fi
  else
    cat > "$override" <<EOF
# Created by scripts/install.sh. Personal and git-ignored.
services:
  ollama:
    image: ollama/ollama:${OLLAMA_TAG}
    restart: unless-stopped
    volumes:
      - ollama:/root/.ollama
volumes:
  ollama:
EOF
    ok "docker-compose.override.yml created (ollama/ollama:${OLLAMA_TAG})"
  fi
  (cd "$DIR" && "${DOCKER[@]}" compose up -d ollama) || die "Could not start Ollama. Check the image tag with --ollama-tag."
  info "Downloading the model ${AI_MODEL_CHOICE} (can take a while)"
  (cd "$DIR" && "${DOCKER[@]}" compose exec -T ollama ollama pull "$AI_MODEL_CHOICE") || die "Could not download ${AI_MODEL_CHOICE}."
  env_set AI_BASE_URL "http://ollama:11434/v1"
  env_set AI_MODEL "$AI_MODEL_CHOICE"
  ok "AI assistant set to ${AI_MODEL_CHOICE}. Post text stays on this machine."
}

# ---------- start ----------
port_busy() {
  if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${WEB_PORT}\$"
  elif command -v lsof >/dev/null 2>&1; then lsof -nP -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1
  else return 1
  fi
}

start_stack() {
  step "Building and starting Heardloop (the first build takes a few minutes)"
  cd "$DIR"
  if port_busy && [[ -z "$("${DOCKER[@]}" compose ps -q web 2>/dev/null)" ]]; then
    die "Port ${WEB_PORT} is already used by another program. Free it, or map another port in docker-compose.override.yml."
  fi
  local version sha
  version="$(tr -d '[:space:]' < VERSION)"
  sha="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  VERSION="$version" GIT_SHA="$sha" "${DOCKER[@]}" compose build --build-arg "GIT_SHA=${sha}" --build-arg "VERSION=${version}" \
    || die "The build failed. Run 'docker compose build' in $DIR to see the full output."
  VERSION="$version" GIT_SHA="$sha" "${DOCKER[@]}" compose up -d || die "Could not start the stack."

  info "Waiting until the API is healthy"
  local waited=0 body=""
  until body="$(curl -fsS --max-time 5 "${WEB_URL}/api/v1/health" 2>/dev/null)" \
        && printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"'; do
    sleep 5; waited=$((waited + 5))
    if [[ $waited -ge $HEALTH_TIMEOUT ]]; then
      "${DOCKER[@]}" compose ps || true
      "${DOCKER[@]}" compose logs --tail 40 api || true
      die "Heardloop did not become healthy in time. See the logs above; docs/INSTALL.md, section 12 helps."
    fi
  done
  ok "Heardloop ${version} is running at ${WEB_URL}"
}

# ---------- owner ----------
detect_time_zone() {
  local tz=""
  if command -v timedatectl >/dev/null 2>&1; then tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"; fi
  if [[ -z "$tz" && -L /etc/localtime ]]; then tz="$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')"; fi
  if [[ -z "$tz" && -r /etc/timezone ]]; then tz="$(tr -d '[:space:]' < /etc/timezone)"; fi
  [[ "$tz" =~ ^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$ ]] || tz="UTC"
  printf '%s' "$tz"
}

create_owner() {
  [[ $NO_OWNER -eq 1 || $YES -eq 1 ]] && { info "Owner account skipped (see docs/INSTALL.md, section 7)."; return; }
  step "Owner account"
  confirm "Create the owner account now? (skip if you already have one)" y || return 0
  local email name workspace tz
  ask "E-mail" ""; email="$REPLY"
  [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "That e-mail address does not look right."
  ask "Your name" ""; name="$REPLY"
  [[ -n "$name" ]] || die "The name is empty."
  ask "Workspace name" "My workspace"; workspace="$REPLY"
  ask "Workspace time zone (IANA name)" "$(detect_time_zone)"; tz="$REPLY"
  info "Choose a password of at least 12 characters. It is not shown while you type."
  if ! "${DOCKER[@]}" compose exec api python -m heardloop.devtools.create_owner \
        --email "$email" --name "$name" --workspace "$workspace" --time-zone "$tz" < /dev/tty; then
    warn "The owner was not created. Run it again later from $DIR:"
    warn "  docker compose exec api python -m heardloop.devtools.create_owner --email <e-mail> --name \"<name>\" --workspace \"<workspace>\""
    return 0
  fi
  ok "Owner account created"
}

# ---------- main ----------
printf '%sHeardloop installer%s\n' "$C_B" "$C_0"
ensure_base_tools
ensure_docker
get_source
check_existing_install
create_env
configure_env
start_stack
setup_ai
[[ $WITH_AI -eq 1 ]] && { (cd "$DIR" && "${DOCKER[@]}" compose up -d api worker) >/dev/null; }
create_owner

step "Done"
cat <<EOF
    Open ${WEB_URL} and sign in.
    1. Turn on two-step verification (Settings).
    2. Connect your LinkedIn profile (Accounts). Missing app keys? docs/INSTALL.md, section 4.
    3. Write your first post.

    Folder:   ${DIR}
    Update:   ${DIR}/scripts/update.sh
    Back up:  ${DIR}/.env (keep it private) and the backups folder
    Guide:    ${DIR}/docs/GUIDE.md
EOF
