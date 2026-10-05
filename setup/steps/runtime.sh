# shellcheck shell=bash
# Install the macagent package into a root-owned virtualenv.

step_runtime() {
    step "Runtime"
    local python venv="$MACAGENT_LIB_DIR/venv" wheel_dir
    python=$(python_bin) || die "$MACAGENT_PYTHON_FORMULA not found; run the packages step"
    [ "$DRY_RUN" = "1" ] || [ -x "$python" ] || die "$python missing; run the packages step"

    ensure_dir "$MACAGENT_LIB_DIR" 755 root:wheel
    ensure_dir "$MACAGENT_LIB_DIR/bin" 755 root:wheel

    if [ -x "$venv/bin/python" ] && "$venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
        ok "virtualenv present"
    else
        srun "$python" -m venv --clear "$venv"
    fi

    # Build the wheel as you (so the checkout stays yours), install it as root.
    # The venv's own pip is used because Homebrew's Python is "externally
    # managed" and discourages using its pip directly.
    wheel_dir=$(mktemp -d)
    run "$venv/bin/python" -m pip wheel --quiet --disable-pip-version-check --no-deps \
        --wheel-dir "$wheel_dir" "$REPO_DIR"
    if [ "$DRY_RUN" = "1" ]; then
        srun "$venv/bin/python" -I -m pip install --force-reinstall --no-deps "$wheel_dir/macagent-*.whl"
    else
        srun "$venv/bin/python" -I -m pip install --quiet --disable-pip-version-check --force-reinstall \
            --no-deps "$wheel_dir"/macagent-*.whl
        ok "installed macagent $("$venv/bin/python" -I -c 'import macagent; print(macagent.__version__)')"
    fi
    rm -rf "$wheel_dir"

    local tool
    for tool in macagent macagent-hook agent-session; do
        install_file "$REPO_DIR/bin/$tool" "$MACAGENT_LIB_DIR/bin/$tool" 755 root:wheel
    done
    srun mkdir -p /usr/local/bin
    srun ln -sfn "$MACAGENT_LIB_DIR/bin/macagent" /usr/local/bin/macagent
    srun ln -sfn "$MACAGENT_LIB_DIR/bin/agent-session" /usr/local/bin/agent-session

    # Nothing under the lib dir may be writable by anyone but root.
    srun chown -R root:wheel "$MACAGENT_LIB_DIR"
    srun chmod -R go-w "$MACAGENT_LIB_DIR"
    ok "runtime is root-owned and read-only to other accounts"
}
