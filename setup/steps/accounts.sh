# shellcheck shell=bash
# Create the agent account (standard, non-admin) and the _agentwatch service
# account that owns the event log and runs the watcher.

step_accounts() {
    step "Accounts"

    if user_exists "$AGENT_USER"; then
        ok "agent account '$AGENT_USER' exists"
    else
        info "creating standard account '$AGENT_USER' — you'll be asked to choose its password"
        srun sysadminctl -addUser "$AGENT_USER" -fullName "Agent" -password - ||
            die "couldn't create '$AGENT_USER'; create it in System Settings → Users & Groups as a Standard user, then re-run"
    fi

    if is_admin "$AGENT_USER"; then
        warn "'$AGENT_USER' is an admin; removing admin rights (the isolation depends on this)"
        srun dseditgroup -o edit -d "$AGENT_USER" -t user admin
    else
        ok "'$AGENT_USER' is not an admin"
    fi

    _ensure_service_account
}

_ensure_service_account() {
    local name="$MACAGENT_SERVICE_USER" gid uid
    if group_exists "$name"; then
        gid=$(dscl . -read "/Groups/$name" PrimaryGroupID | awk '{print $2}')
        ok "group $name exists (gid $gid)"
    else
        gid=$(free_id /Groups PrimaryGroupID 400 499) || die "no free gid in 400-499"
        info "creating group $name (gid $gid)"
        srun dscl . -create "/Groups/$name"
        srun dscl . -create "/Groups/$name" PrimaryGroupID "$gid"
        srun dscl . -create "/Groups/$name" RealName "MacAgent watcher"
        srun dscl . -create "/Groups/$name" Password '*'
    fi

    if user_exists "$name"; then
        ok "service account $name exists"
        return 0
    fi
    uid=$(free_id /Users UniqueID 400 499) || die "no free uid in 400-499"
    info "creating hidden service account $name (uid $uid)"
    srun dscl . -create "/Users/$name"
    srun dscl . -create "/Users/$name" UniqueID "$uid"
    srun dscl . -create "/Users/$name" PrimaryGroupID "$gid"
    srun dscl . -create "/Users/$name" RealName "MacAgent watcher"
    srun dscl . -create "/Users/$name" UserShell /usr/bin/false
    srun dscl . -create "/Users/$name" NFSHomeDirectory /var/empty
    srun dscl . -create "/Users/$name" Password '*'
    srun dscl . -create "/Users/$name" IsHidden 1
}
