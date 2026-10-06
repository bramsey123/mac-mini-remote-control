# shellcheck shell=bash
# Check that this Mac and this account can run the rest of setup.

step_preflight() {
    step "Preflight checks"
    [ "$(uname -s)" = "Darwin" ] || die "this setup is for macOS"

    local version major
    version=$(sw_vers -productVersion)
    major=${version%%.*}
    if [ "$major" -lt 13 ]; then
        warn "macOS $version is older than 13 (Ventura); untested"
    else
        ok "macOS $version"
    fi

    local me
    me=$(id -un)
    is_admin "$me" || die "run setup from an admin account ('$me' is not an admin)"
    [ "$me" != "$AGENT_USER" ] || die "run setup from your own admin account, not the agent account"
    ok "running as admin account '$me'"

    case "$AGENT_USER" in
        ''|root|*[!a-z0-9_-]*) die "invalid agent account name '$AGENT_USER' (lowercase letters, digits, _ and - only)" ;;
    esac

    if xcode-select -p >/dev/null 2>&1; then
        ok "Xcode Command Line Tools installed"
    else
        die "Xcode Command Line Tools missing; run: xcode-select --install"
    fi

    if [ -n "$(brew_bin)" ]; then
        ok "Homebrew installed"
    else
        die "Homebrew missing; install it from https://brew.sh and re-run"
    fi
}
