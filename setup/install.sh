#!/bin/bash
# Set up this Mac to host the agent. See docs/setup-guide.md first.
#
# Run from your own admin account (not root, not the agent account):
#   ./setup/install.sh              # everything
#   ./setup/install.sh --dry-run    # show what would change
#   ./setup/install.sh --only runtime --only services
#   ./setup/install.sh --list
#
# Safe to re-run: every step checks before it changes anything.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0
AGENT_USER="agent"
ONLY=""

STEPS="preflight packages accounts power runtime config claude-settings services agent-home verify"

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "Steps (in order): $STEPS"
    echo "Options: --dry-run, --only STEP (repeatable), --agent-user NAME (default: agent), --list"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --only) shift; ONLY="$ONLY ${1:?--only needs a step name}" ;;
        --agent-user) shift; AGENT_USER="${1:?--agent-user needs a name}" ;;
        --list) echo "$STEPS"; exit 0 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

export REPO_DIR DRY_RUN AGENT_USER
# shellcheck source=setup/lib/common.sh
. "$REPO_DIR/setup/lib/common.sh"

for name in $ONLY; do
    case " $STEPS " in
        *" $name "*) ;;
        *) die "unknown step '$name' (steps: $STEPS)" ;;
    esac
done

for name in $STEPS; do
    # shellcheck source=/dev/null
    . "$REPO_DIR/setup/steps/$name.sh"
done

[ "$(id -u)" -ne 0 ] || die "run as your admin account, not root; the script uses sudo when it needs to"
[ "$DRY_RUN" = "1" ] || sudo -v || die "sudo is required"

for name in $STEPS; do
    if [ -n "$ONLY" ]; then
        case " $ONLY " in *" $name "*) ;; *) continue ;; esac
    fi
    # step names may contain dashes; functions use underscores
    "step_$(echo "$name" | tr '-' '_')"
done

if [ -n "$MANUAL_STEPS" ]; then
    printf '\n%sManual steps remaining:%s\n%s' "$_BOLD" "$_RST" "$MANUAL_STEPS"
    echo "Details: docs/setup-guide.md"
fi
