#!/usr/bin/env bash
# Shared shell helpers for deploy.sh and friends.
# Source this from any script that needs logging, error handling, or secrets.

set -euo pipefail

# -------- Logging -----------------------------------------------------------
# Logs go to stderr so that the script's stdout can be captured separately.
_log_ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log_info()  { echo "[$(_log_ts)] [INFO]  $*" >&2; }
log_warn()  { echo "[$(_log_ts)] [WARN]  $*" >&2; }
log_error() { echo "[$(_log_ts)] [ERROR] $*" >&2; }

die() { log_error "$*"; exit 1; }

# -------- Sanity checks -----------------------------------------------------
require_root() {
    [ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

ensure_dir() {
    local d="$1" mode="${2:-0755}"
    [ -d "$d" ] || { mkdir -p "$d"; chmod "$mode" "$d"; }
}

# -------- .env handling -----------------------------------------------------
# Loads KEY=VALUE pairs from a file into the current shell (exporting each).
# Lines starting with # and blank lines are ignored. Existing env wins.
load_env() {
    local f="$1"
    [ -f "$f" ] || die "env file not found: $f"
    set -a
    # shellcheck disable=SC1090
    . "$f"
    set +a
}

# Returns 0 if the .env file already has a non-empty value for the given key.
env_has_key() {
    local f="$1" key="$2"
    [ -f "$f" ] || return 1
    grep -qE "^${key}=" "$f" && \
        ! grep -qE "^${key}=" "$f" | grep -qE "^${key}=$"
}

# Writes KEY=VALUE to a file, creating it with mode 600 if missing.
# Does not overwrite an existing value for the same key.
save_secret() {
    local f="$1" key="$2" value="$3"
    if [ -f "$f" ] && grep -qE "^${key}=" "$f"; then
        log_warn "secret $key already present in $f; not overwriting"
        return 0
    fi
    [ -f "$f" ] || touch "$f"
    chmod 600 "$f"
    printf '%s=%s\n' "$key" "$value" >> "$f"
}

# Reads a key's value from an .env file (returns the empty string if missing).
read_secret() {
    local f="$1" key="$2"
    [ -f "$f" ] || { echo ""; return 0; }
    awk -F= -v k="$key" '$1==k {sub(/^[^=]+=/,"",$0); print; exit}' "$f"
}
