"""planguard --version"""

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import planguard
from planguard import __main__ as entry
from planguard import cli
from tests.conftest import SRC

ROOT = Path(__file__).resolve().parent.parent


def pyproject_version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_package_version_matches_pyproject():
    assert planguard.__version__ == pyproject_version()


def expected_version():
    """What `--version` must print, computed independently of the code under test: the installed
    distribution's version if planguard is installed, else the module constant (a bare checkout)."""
    try:
        return importlib.metadata.version("planguard")
    except importlib.metadata.PackageNotFoundError:
        return planguard.__version__


def test_version_flag_prints_the_version_and_exits_0(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr()
    assert out.out == f"planguard {expected_version()}\n" and out.err == ""


def test_when_installed_the_metadata_version_equals_pyproject():
    try:
        installed = importlib.metadata.version("planguard")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("planguard is not installed in this environment (running from a bare checkout, as in CI)")
    assert installed == pyproject_version()


def test_version_flag_needs_no_other_arguments_and_wins_over_them(capsys, tmp_path):
    assert cli.main(["--version", "--config", str(tmp_path / "missing.toml"), "nope.json"]) == 0
    assert capsys.readouterr().out.startswith("planguard ")


def test_run_entry_point_exit_code_is_0(capsys):
    assert entry.run(["--version"]) == 0
    assert capsys.readouterr().out.startswith("planguard ")


def test_version_in_a_real_process():
    import os

    env = dict(os.environ, PYTHONPATH=str(SRC))
    proc = subprocess.run([sys.executable, "-m", "planguard", "--version"], capture_output=True, text=True, env=env)
    assert proc.returncode == 0 and proc.stdout.startswith("planguard ") and proc.stderr == ""


def test_falls_back_to_the_module_version_when_not_installed(monkeypatch, capsys):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == f"planguard {planguard.__version__}\n"


def test_falls_back_when_the_metadata_lookup_breaks_in_any_way(monkeypatch, capsys):
    def broken(name):
        raise RuntimeError("corrupt metadata")

    monkeypatch.setattr(importlib.metadata, "version", broken)
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == f"planguard {planguard.__version__}\n"


def test_help_still_exits_0_and_missing_plan_is_still_2(capsys):
    assert cli.main(["--help"]) == 0
    assert "--version" in capsys.readouterr().out
    assert cli.main([]) == 2


@pytest.mark.parametrize("argv", [["--version=1"], ["--versions"], ["--vers"]])
def test_no_abbreviations_or_values(capsys, argv):
    assert cli.main(argv) == 2
