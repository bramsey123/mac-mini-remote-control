# shellcheck shell=bash
# shellcheck disable=SC2034  # constants are used by the step files that source this
# Shared helpers and constants for setup scripts.
#
# Compatible with macOS's /bin/bash 3.2: no associative arrays, no ${var,,},
# no mapfile. Every step must be idempotent: running setup twice changes
# nothing the second time.

# --- Install locations (mirror src/macagent/paths.py; tests/test_paths.py checks) ---
MACAGENT_LIB_DIR="/usr/local/lib/macagent"
MACAGENT_SUPPORT_DIR="/Library/Application Support/MacAgent"
MACAGENT_LOG_DIR="/Library/Logs/MacAgent"
MACAGENT_MANAGED_SETTINGS="/Library/Application Support/ClaudeCode/managed-settings.json"

MACAGENT_SERVICE_USER="_agentwatch"
MACAGENT_LAUNCHD_DIR="/Library/LaunchDaemons"
MACAGENT_LAUNCHD_LABELS="local.macagent.eventd local.macagent.healthcheck"
MACAGENT_PYTHON_FORMULA="python@3.12"

# Set by install.sh.
: "${REPO_DIR:?REPO_DIR must be set}"
: "${DRY_RUN:=0}"
: "${AGENT_USER:=agent}"

# --- Output -------------------------------------------------------------------

if [ -t 1 ]; then
    _BOLD=$'\033[1m'; _DIM=$'\033[2m'; _RED=$'\033[31m'; _YEL=$'\033[33m'; _GRN=$'\033[32m'; _RST=$'\033[0m'
else
    _BOLD=""; _DIM=""; _RED=""; _YEL=""; _GRN=""; _RST=""
fi

step()  { printf '\n%s==> %s%s\n' "$_BOLD" "$*" "$_RST"; }
info()  { printf '    %s\n' "$*"; }
ok()    { printf '    %s✓%s %s\n' "$_GRN" "$_RST" "$*"; }
warn()  { printf '    %s!%s %s\n' "$_YEL" "$_RST" "$*" >&2; }
die()   { printf '%serror:%s %s\n' "$_RED" "$_RST" "$*" >&2; exit 1; }

# Manual follow-ups collected during the run and printed at the end.
MANUAL_STEPS=""
manual() { MANUAL_STEPS="${MANUAL_STEPS}  - $*"$'\n'; }

# --- Running commands -----------------------------------------------------------

# run CMD...: execute, or just print it under --dry-run.
run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '    %s[dry-run]%s %s\n' "$_DIM" "$_RST" "$*"
        return 0
    fi
    "$@"
}

# srun CMD...: run with sudo.
srun() { run sudo "$@"; }

# as_agent CMD...: run as the agent account, with its own HOME.
as_agent() { run sudo -u "$AGENT_USER" -H "$@"; }

# root_test ARGS...: `test` with root's view of the filesystem. Under --dry-run
# it never prompts for a password (missing credentials count as "false").
root_test() {
    if [ "$DRY_RUN" = "1" ]; then
        sudo -n test "$@" 2>/dev/null
    else
        sudo test "$@"
    fi
}

# --- Queries --------------------------------------------------------------------

user_exists()  { dscl . -read "/Users/$1" >/dev/null 2>&1; }
group_exists() { dscl . -read "/Groups/$1" >/dev/null 2>&1; }

is_admin() {
    dseditgroup -o checkmember -m "$1" admin 2>/dev/null | grep -q '^yes'
}

home_of() {
    dscl . -read "/Users/$1" NFSHomeDirectory 2>/dev/null | awk '{print $2}'
}

# First unused id in [lo, hi] for the given dscl attribute (UniqueID / PrimaryGroupID).
free_id() {
    local path="$1" attr="$2" lo="$3" hi="$4" id
    local used
    used=$(dscl . -list "$path" "$attr" | awk '{print $2}')
    id=$lo
    while [ "$id" -le "$hi" ]; do
        if ! printf '%s\n' "$used" | grep -qx "$id"; then
            echo "$id"
            return 0
        fi
        id=$((id + 1))
    done
    return 1
}

brew_bin() {
    if command -v brew >/dev/null 2>&1; then command -v brew
    elif [ -x /opt/homebrew/bin/brew ]; then echo /opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then echo /usr/local/bin/brew
    fi
}

python_bin() {
    local prefix
    prefix=$("$(brew_bin)" --prefix "$MACAGENT_PYTHON_FORMULA" 2>/dev/null) || return 1
    echo "$prefix/bin/python3.12"
}

# install_file SRC DEST MODE OWNER:GROUP — copy only if contents differ.
install_file() {
    local src="$1" dest="$2" mode="$3" owner="$4"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        srun chown "$owner" "$dest"
        srun chmod "$mode" "$dest"
        return 0
    fi
    srun install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dest"
    info "installed $dest"
}

# ensure_dir PATH MODE OWNER:GROUP
ensure_dir() {
    srun mkdir -p "$1"
    srun chown "$3" "$1"
    srun chmod "$2" "$1"
}
