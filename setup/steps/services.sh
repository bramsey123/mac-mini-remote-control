# shellcheck shell=bash
# Install and (re)start the launchd services.

step_services() {
    step "Services"
    local label plist
    for label in $MACAGENT_LAUNCHD_LABELS; do
        plist="$MACAGENT_LAUNCHD_DIR/$label.plist"
        plutil -lint -s "$REPO_DIR/config/launchd/$label.plist" || die "invalid plist: $label"
        install_file "$REPO_DIR/config/launchd/$label.plist" "$plist" 644 root:wheel
        # Restart so new code and config take effect. bootout fails harmlessly
        # when the service isn't loaded yet.
        run sudo launchctl bootout "system/$label" 2>/dev/null || true
        srun launchctl enable "system/$label"
        srun launchctl bootstrap system "$plist"
        ok "$label loaded"
    done

    [ "$DRY_RUN" = "1" ] && return 0
    local tries=0
    until sudo "$MACAGENT_LIB_DIR/bin/macagent" check --only eventd >/dev/null 2>&1; do
        tries=$((tries + 1))
        [ "$tries" -lt 10 ] || die "event daemon did not come up; see $MACAGENT_LOG_DIR/eventd.err.log"
        sleep 1
    done
    ok "event daemon is answering"
}
