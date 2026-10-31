#!/bin/sh
set -eu

usage() {
    cat <<'HELP'
Install gmi from GitHub. Requires Python 3.10 or newer.
Usage: sh install.sh [--mcp] [--help]
  --mcp    Include the optional MCP SDK.
  --help   Show this help without installing.
GMI_REF overrides the default tag v1.0.2.
GMI_INSTALL_MCP=1 also includes the MCP SDK.
Installer order: uv tool install, pipx install, then pip or a private virtual environment.
HELP
}

install_mcp=${GMI_INSTALL_MCP:-0}
for argument in "$@"; do
    case "$argument" in
        --mcp) install_mcp=1 ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$argument" >&2; usage >&2; exit 1 ;;
    esac
done

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10 or newer is required. Install python3 and try again.' >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    printf '%s\n' 'Python 3.10 or newer is required. Upgrade python3 and try again.' >&2
    exit 1
fi

ref=${GMI_REF:-v1.0.2}
package="git+https://github.com/zkasuran/gmi@$ref"
if [ "$install_mcp" = 1 ]; then
    package="gmi[mcp] @ $package"
fi

run_command() {
    printf 'Running:'
    for argument in "$@"; do
        printf " '%s'" "$(printf '%s' "$argument" | sed "s/'/'\\\\''/g")"
    done
    printf '\n'
    "$@"
}

venv_install=0
if command -v uv >/dev/null 2>&1; then
    set -- uv tool install "$package"
elif command -v pipx >/dev/null 2>&1; then
    set -- pipx install "$package"
elif python3 -m pip --version >/dev/null 2>&1; then
    stdlib=$(python3 -c 'import sysconfig; print(sysconfig.get_path("stdlib"))' 2>/dev/null || true)
    if [ -n "$stdlib" ] && [ -f "$stdlib/EXTERNALLY-MANAGED" ]; then
        venv_install=1
        gmi_home=${GMI_HOME:-$HOME/.local/share/gmi}
        venv_path=$gmi_home/venv
        set -- python3 -m venv "$venv_path"
    else
        set -- python3 -m pip install --user "$package"
    fi
else
    printf '%s\n' 'No installer found. Install uv, pipx or Python pip and try again.' >&2
    exit 1
fi

if [ "$venv_install" = 1 ]; then
    if ! run_command "$@"; then
        printf '%s\n' 'Could not create the private virtual environment.' >&2
        printf '%s\n' 'Install the missing package and run this installer again:' >&2
        printf '%s\n' 'sudo apt install python3-venv' >&2
        exit 1
    fi
    run_command "$venv_path/bin/python" -m pip install --upgrade "$package"
    run_command mkdir -p "$HOME/.local/bin"
    run_command ln -sf "$venv_path/bin/gmi" "$HOME/.local/bin/gmi"
    printf 'Created gmi in %s and linked %s\n' "$venv_path" "$HOME/.local/bin/gmi"
    printf 'To uninstall, remove %s and %s\n' "$venv_path" "$HOME/.local/bin/gmi"
else
    run_command "$@"
fi

if command -v gmi >/dev/null 2>&1; then
    gmi --version
elif [ "$venv_install" = 1 ]; then
    case ":${PATH:-}:" in
        *":$HOME/.local/bin:"*) ;;
        *) printf 'Add this line to your shell profile: export PATH="$HOME/.local/bin:$PATH"\n' ;;
    esac
else
    user_bin=$(python3 -c 'import site; print(site.getuserbase() + "/bin")')
    printf 'Add the pip user bin directory to PATH: export PATH="%s:$PATH"\n' "$user_bin"
    printf '%s\n' 'For uv or pipx, also check the PATH instructions printed by that installer.'
fi
