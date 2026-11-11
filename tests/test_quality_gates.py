"""ruff and mypy are dev-only quality gates for src/ (no runtime dependencies are added)."""

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.conftest import SRC

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

skip_slow = pytest.mark.skipif(os.environ.get("PLANGUARD_SKIP_SLOW") == "1", reason="PLANGUARD_SKIP_SLOW=1")


def have(module):
    return importlib.util.find_spec(module) is not None


# ---- configuration ---------------------------------------------------------------------------------

def test_tools_are_dev_extras_and_never_runtime_dependencies():
    project = PYPROJECT["project"]
    assert project["dependencies"] == []
    dev = project["optional-dependencies"]["dev"]
    assert {"pytest", "ruff", "mypy"} <= set(dev)


def test_ruff_and_mypy_are_configured_in_pyproject():
    ruff, mypy = PYPROJECT["tool"]["ruff"], PYPROJECT["tool"]["mypy"]
    assert ruff["target-version"] == "py311" and ruff["line-length"] == 120
    assert {"E", "F", "I", "UP", "B", "SIM"} <= set(ruff["lint"]["select"])
    assert mypy["strict"] is True and mypy["python_version"] == "3.11" and mypy["files"] == ["src"]


# ---- the CI job -------------------------------------------------------------------------------------

def test_ci_has_a_separate_lint_job_with_pinned_tools_and_a_timeout():
    lint = CI[CI.index("\n  lint:"):]
    assert re.search(r'pip install "?ruff==\d+\.\d+\.\d+"? "?mypy==\d+\.\d+\.\d+"?', lint), "pin exact versions"
    assert "ruff check src" in lint
    assert re.search(r"^\s*run: mypy\s*$", lint, re.M)
    assert "timeout-minutes:" in lint.split("steps:")[0]
    assert "\n  pytest:" in CI  # the test job is separate and still there
    assert CI.count("runs-on:") == CI.count("timeout-minutes:") == 2


# ---- the gates themselves, run on the source tree under test ---------------------------------------------

@skip_slow
@pytest.mark.skipif(not have("ruff"), reason="ruff is not installed (dev tool: pip install ruff)")
def test_ruff_is_clean_on_src():
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", str(SRC), "--config", str(ROOT / "pyproject.toml")],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@skip_slow
@pytest.mark.skipif(not have("mypy"), reason="mypy is not installed (dev tool: pip install mypy)")
def test_mypy_strict_is_clean_on_src():
    proc = subprocess.run([sys.executable, "-m", "mypy", "--config-file", str(ROOT / "pyproject.toml"), str(SRC)],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
