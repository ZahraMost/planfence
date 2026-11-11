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


def test_pytest_job_runs_on_python_3_11_3_12_and_3_13_so_packaging_is_tested_on_each():
    block = job_block("pytest")
    match = re.search(r"python-version:\s*\[([^\]]*)\]", block)
    assert match, "the pytest job needs a python-version matrix"
    versions = [v.strip().strip('"') for v in match.group(1).split(",")]
    assert versions == ["3.11", "3.12", "3.13"]
    assert "python-version: ${{ matrix.python-version }}" in block


def test_nothing_in_ci_disables_the_packaging_or_lint_tests():
    assert "PLANGUARD_SKIP_SLOW" not in CI
    assert "--deselect" not in CI and "-k " not in CI and "--ignore" not in CI


def test_packaging_test_needs_build_which_the_pytest_job_installs():
    requirements, _ = installed_requirements(pip_install_lines(job_block("pytest"))[0])
    assert "build" in requirements
    packaging = (ROOT / "tests" / "test_packaging.py").read_text(encoding="utf-8")
    assert "import build" in packaging  # the same module the job installs


def test_lint_job_runs_the_commands_that_the_skippable_quality_gate_tests_run():
    """In the pytest job ruff/mypy are not installed, so tests/test_quality_gates.py skips them there;
    the lint job must run the same checks (ruff on src, mypy with the pyproject configuration)."""
    lint = job_block("lint")
    assert re.search(r"^\s*run: ruff check src\s*$", lint, re.M)
    assert re.search(r"^\s*run: mypy\s*$", lint, re.M)
    gates = (ROOT / "tests" / "test_quality_gates.py").read_text(encoding="utf-8")
    assert '"ruff", "check"' in gates and '"mypy"' in gates
    assert "ruff is not installed" in gates and "mypy is not installed" in gates  # the skip reasons
