# shellcheck shell=bash
# Prepare the agent's home: work folder, private secrets file, Claude Code.

step_agent_home() {
    step "Agent home"
    local home
    home=$(home_of "$AGENT_USER")
    if [ -z "$home" ]; then
        [ "$DRY_RUN" = "1" ] || die "agent account '$AGENT_USER' not found; run the accounts step"
        home="/Users/$AGENT_USER"
    fi

    if ! root_test -d "$home"; then
        srun createhomedir -c -u "$AGENT_USER"
    fi
    as_agent mkdir -p "$home/work" "$home/.config/macagent"
    as_agent chmod 700 "$home/.config/macagent"

    local env_file="$home/.config/macagent/agent.env"
    if root_test -f "$env_file"; then
        ok "agent.env exists"
    else
        srun install -m 600 -o "$AGENT_USER" -g staff "$REPO_DIR/config/agent.env.example" "$env_file"
        manual "Put the agent's 1Password service-account token in $env_file (see docs/setup-guide.md)"
    fi
    srun chmod 600 "$env_file"

    if root_test -x "$home/.local/bin/claude"; then
        ok "Claude Code installed for $AGENT_USER"
    else
        info "installing Claude Code for $AGENT_USER (per-user, in ~/.local/bin)"
        as_agent bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
    fi
    # Put ~/.local/bin on the agent's PATH for interactive logins.
    # shellcheck disable=SC2016  # expanded by the agent's shell, not this one
    as_agent sh -c 'grep -qs "\.local/bin" "$HOME/.zprofile" ||
        printf "%s\n" "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.zprofile"'

    manual "Log the agent in once, interactively: ssh in, run: sudo -iu $AGENT_USER, then cd ~/work && claude, use /login with a claude.ai account, and trust the folder"
    manual "Start the session: sudo -iu $AGENT_USER agent-session start — then open it in the Claude app on your phone"
}
