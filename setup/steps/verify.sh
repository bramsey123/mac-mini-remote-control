# shellcheck shell=bash
# Run the health checks against what was just installed.

step_verify() {
    step "Verify"
    if [ "$DRY_RUN" = "1" ]; then
        info "skipped in dry-run"
        return 0
    fi
    if sudo "$MACAGENT_LIB_DIR/bin/macagent" check; then
        ok "all checks passed"
    else
        warn "some checks need attention (expected until the manual steps are done)"
    fi
}
