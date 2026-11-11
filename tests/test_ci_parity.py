"""CI must run the tests the way a fresh checkout does: no editable install of planguard."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def job_block(name):
    start = CI.index(f"\n  {name}:")
    rest = CI[start + 1:]
    nxt = re.search(r"\n  [\w-]+:\n", rest[len(f"  {name}:"):])
    return rest if nxt is None else rest[: len(f"  {name}:") + nxt.start()]


def pip_install_lines(block):
    return [ln.strip().removeprefix("run:").strip() for ln in block.splitlines() if "pip install" in ln]


def installed_requirements(line):
    args = line.split("pip install", 1)[1].split()
    return [a.strip('"') for a in args if not a.startswith("-")], [a for a in args if a.startswith("-")]


def test_pytest_job_installs_exactly_pytest_and_build():
    lines = pip_install_lines(job_block("pytest"))
    assert len(lines) == 1, lines
    requirements, options = installed_requirements(lines[0])
    assert sorted(requirements) == ["build", "pytest"]
    assert options == []


def test_pytest_job_never_installs_planguard_itself():
    block = "\n".join(ln for ln in job_block("pytest").splitlines() if not ln.strip().startswith("#"))
    for bad in ("planguard", "pip install .", "pip install -e", "--editable", "pip install src", "setup.py", "PYTHONPATH"):
        assert bad not in block.replace("pytest:", ""), bad
    assert "python -m pytest" in block


def test_lint_job_does_not_install_planguard_either():
    requirements, _ = installed_requirements(pip_install_lines(job_block("lint"))[0])
    assert all(r.split("==")[0] in ("ruff", "mypy") for r in requirements)


def test_job_block_helper_splits_jobs_correctly():
    assert "lint:" not in job_block("pytest") and "matrix:" in job_block("pytest")
    assert "matrix:" not in job_block("lint")
