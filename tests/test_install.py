from pathlib import Path
import shlex
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"
BASE = "git+https://github.com/zkasuran/gmi@"


def run_installer(tmp_path, installers=(), arguments=(), **overrides):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"

    def executable(name, body):
        path = bin_dir / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    record = 'printf "%s" "{name}" >> "$CALLS"\n'
    record += 'printf "|%s" "$@" >> "$CALLS"\nprintf "\\n" >> "$CALLS"\n'
    for name in (*installers, "sudo"):
        executable(name, record.format(name=name) + 'printf "EXEC %s\\n" "' + name + '"\n')
    executable("python3", record.format(name="python3") + '''
if [ "$1" = -c ]; then
    case "$2" in
        *getuserbase*) printf '/fake/user/bin\n' ;;
        *) exit "$PYTHON_OLD" ;;
    esac
elif [ "$1" = -m ] && [ "$2" = pip ]; then
    shift 2
    if command -v pip >/dev/null 2>&1; then pip "$@"; else exit 1; fi
fi
''')
    for name in ("cat", "sed"):
        (bin_dir / name).symlink_to("/bin/" + name)
    environment = {"PATH": str(bin_dir), "CALLS": str(log), "PYTHON_OLD": "0"}
    environment.update(overrides)
    result = subprocess.run(["/bin/sh", str(SCRIPT), *arguments], env=environment,
                            text=True, capture_output=True)
    calls = log.read_text().splitlines() if log.exists() else []
    assert not any(call.startswith("sudo") for call in calls)
    return result, calls


@pytest.mark.parametrize(("installers", "command"), [
    (("uv", "pipx", "pip"), ["uv", "tool", "install"]),
    (("pipx", "pip"), ["pipx", "install"]),
    (("pip",), ["python3", "-m", "pip", "install", "--user"]),
])
@pytest.mark.parametrize(("arguments", "environment", "package"), [
    ((), {}, BASE + "v1.0.2"),
    (("--mcp",), {}, "gmi[mcp] @ " + BASE + "v1.0.2"),
    ((), {"GMI_INSTALL_MCP": "1"}, "gmi[mcp] @ " + BASE + "v1.0.2"),
    ((), {"GMI_REF": "release/test"}, BASE + "release/test"),
    (("--mcp",), {"GMI_REF": "release/test"}, "gmi[mcp] @ " + BASE + "release/test"),
    ((), {"GMI_REF": "test'quote"}, BASE + "test'quote"),
])
def test_installer_choice(tmp_path, installers, command, arguments, environment, package):
    result, calls = run_installer(tmp_path, installers, arguments, **environment)
    assert result.returncode == 0, result.stderr
    assert "|".join([*command, package]) in calls
    printed = next(line.removeprefix("Running: ") for line in result.stdout.splitlines()
                   if line.startswith("Running:"))
    assert shlex.split(printed) == [*command, package]
    assert result.stdout.index("Running:") < result.stdout.index("EXEC ")
    assert 'export PATH="/fake/user/bin:$PATH"' in result.stdout


def test_python_too_old(tmp_path):
    result, calls = run_installer(tmp_path, ("uv", "pipx", "pip"), PYTHON_OLD="1")
    assert result.returncode == 1
    assert "Python 3.10 or newer is required" in result.stderr
    assert not any(call.startswith(("uv|", "pipx|", "pip|")) for call in calls)


def test_no_installer(tmp_path):
    result, calls = run_installer(tmp_path)
    assert result.returncode == 1
    assert "No installer found" in result.stderr
    assert "Running:" not in result.stdout


def test_missing_python(tmp_path):
    result = subprocess.run(["/bin/sh", str(SCRIPT)], env={"PATH": str(tmp_path)},
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "Python 3.10 or newer is required" in result.stderr


def test_installer_help_and_unknown_option(tmp_path):
    result, calls = run_installer(tmp_path, arguments=("--help",))
    assert result.returncode == 0
    assert "GMI_REF" in result.stdout
    assert calls == []
    result = subprocess.run(["/bin/sh", str(SCRIPT), "--unknown"],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "Unknown option" in result.stderr


def test_installed_version_on_path(tmp_path):
    result, calls = run_installer(tmp_path, ("uv", "gmi"))
    assert result.returncode == 0
    assert "gmi|--version" in calls


def test_shell_syntax():
    subprocess.run(["/bin/sh", "-n", str(SCRIPT)], check=True)


def test_externally_managed_uses_private_venv(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    marker = tmp_path / "stdlib"
    marker.mkdir()
    (marker / "EXTERNALLY-MANAGED").write_text("")
    python = bin_dir / "python3"
    python.write_text(f'''#!/bin/sh
printf 'python3|%s\\n' "$*" >> "$CALLS"
if [ "$1" = -c ]; then
  case "$2" in
    *sys.version_info*) exit 0 ;;
    *sysconfig*) printf '%s\\n' "{marker}" ;;
    *getuserbase*) printf '%s\\n' "{tmp_path}/user" ;;
  esac
elif [ "$1" = -m ] && [ "$2" = pip ]; then
  [ "$3" = --version ] && exit 0
  exit 1
elif [ "$1" = -m ] && [ "$2" = venv ]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/python" <<'PY'
#!/bin/sh
printf 'venv-python|%s\\n' "$*" >> "$CALLS"
touch "$(dirname "$0")/gmi"
PY
  chmod +x "$3/bin/python"
fi
''')
    python.chmod(0o755)
    for name in ("mkdir", "ln", "sed", "cat", "chmod", "touch", "dirname"):
        (bin_dir / name).symlink_to("/bin/" + name)
    environment = {"PATH": str(bin_dir), "CALLS": str(calls), "HOME": str(tmp_path)}
    result = subprocess.run(["/bin/sh", str(SCRIPT)], env=environment,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".local/bin/gmi").is_symlink()
    recorded = calls.read_text()
    assert "pip install --user" not in recorded
    assert "venv-python|-m pip install --upgrade" in recorded
    second = subprocess.run(["/bin/sh", str(SCRIPT)], env=environment,
                            text=True, capture_output=True)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / ".local/bin/gmi").is_symlink()


def test_venv_failure_prints_apt_hint_without_sudo(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "stdlib"
    marker.mkdir()
    (marker / "EXTERNALLY-MANAGED").write_text("")
    python = bin_dir / "python3"
    python.write_text(f'''#!/bin/sh
if [ "$1" = -c ]; then
  case "$2" in *sys.version_info*) exit 0 ;; *sysconfig*) printf '%s\\n' "{marker}" ;; esac
elif [ "$1" = -m ] && [ "$2" = pip ]; then exit 0
elif [ "$1" = -m ] && [ "$2" = venv ]; then exit 1
fi
''')
    python.chmod(0o755)
    (bin_dir / "sed").symlink_to("/bin/sed")
    result = subprocess.run(["/bin/sh", str(SCRIPT)],
                            env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert "sudo apt install python3-venv" in result.stderr
    assert "sudo|" not in result.stderr
