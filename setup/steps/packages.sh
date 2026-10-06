# shellcheck shell=bash
# Install the tools the runtime and the agent session depend on.

step_packages() {
    step "Packages"
    local brew formula
    brew=$(brew_bin)
    for formula in "$MACAGENT_PYTHON_FORMULA" tmux; do
        if "$brew" list --versions "$formula" >/dev/null 2>&1; then
            ok "$formula installed"
        else
            run "$brew" install "$formula"
        fi
    done

    if "$brew" list --cask 1password-cli >/dev/null 2>&1 || command -v op >/dev/null 2>&1; then
        ok "1Password CLI installed"
    else
        run "$brew" install --cask 1password-cli
    fi

    if [ -d /Applications/Tailscale.app ]; then
        ok "Tailscale installed"
    else
        warn "Tailscale not installed"
        manual "Install Tailscale (Mac App Store or https://tailscale.com/download), sign in, and enable it; install it on your phone too"
    fi
}
