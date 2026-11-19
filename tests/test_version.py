from pathlib import Path

from click.testing import CliRunner

from gmi import __version__
from gmi.cli import main

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_package_version_agrees():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["version"] == __version__ == "1.0.2"


def test_no_old_version_in_source_or_tests():
    old_version = ".".join(("0", "1", "0"))
    for directory in ("src", "tests"):
        for path in (ROOT / directory).rglob("*"):
            if (path.is_file() and "__pycache__" not in path.parts
                    and not any(part.endswith(".egg-info") for part in path.parts)):
                assert old_version not in path.read_text(), str(path)


def test_version_option():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output == "gmi 1.0.2\n"
