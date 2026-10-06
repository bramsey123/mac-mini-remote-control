# shellcheck shell=bash
# Create config, secrets, state and log directories with the right owners.

step_config() {
    step "Configuration"
    local svc="$MACAGENT_SERVICE_USER" config="$MACAGENT_SUPPORT_DIR/config.toml"

    ensure_dir "$MACAGENT_SUPPORT_DIR" 755 root:wheel
    ensure_dir "$MACAGENT_SUPPORT_DIR/secrets" 750 "root:$svc"
    ensure_dir "$MACAGENT_SUPPORT_DIR/run" 755 "$svc:$svc"
    ensure_dir "$MACAGENT_SUPPORT_DIR/state" 700 root:wheel
    ensure_dir "$MACAGENT_LOG_DIR" 750 "$svc:admin"

    if [ -f "$config" ]; then
        ok "keeping existing $config"
        srun chown "root:$svc" "$config"
        srun chmod 640 "$config"
    else
        install_file "$REPO_DIR/config/macagent.example.toml" "$config" 640 "root:$svc"
        if [ "$AGENT_USER" != "agent" ]; then
            srun sed -i '' "s/^agent_user = \"agent\"/agent_user = \"$AGENT_USER\"/" "$config"
        fi
        manual "Edit $config: set up [notify] so alerts reach your phone, then: sudo macagent notify-test"
    fi

    if [ "$DRY_RUN" != "1" ]; then
        sudo "$MACAGENT_LIB_DIR/bin/macagent" config-check || die "fix $config and re-run"
    fi
}
