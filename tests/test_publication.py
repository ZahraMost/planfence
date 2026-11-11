"""Pre-publication checks: placeholders, action versions, README status and real example, privacy."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planguard import cli

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
PROVIDERS = FIX / "providers" / "terraform"
CI = ROOT / ".github" / "workflows" / "ci.yml"
EXAMPLE = ROOT / "examples" / "github-actions" / "planguard-example.yml"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is required to list tracked files")


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    paths = [ROOT / p for p in out.decode("utf-8").split("\0") if p]
    if not paths or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    return paths


def read_text_or_none(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# ---- 1. placeholders ------------------------------------------------------------------------------------

@needs_git
def test_no_unexplained_your_org_placeholder_remains_in_tracked_files():
    offenders = []
    for path in tracked_files():
        if "tests" in path.relative_to(ROOT).parts[:1]:
            continue  # the tests quote the placeholder name
        text = read_text_or_none(path)
        for n, line in enumerate((text or "").splitlines(), 1):
            if "your-org" in line.lower() and "placeholder" not in line.lower():
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert offenders == []


def test_codeowners_example_uses_the_real_owner():
    lines = [ln.split() for ln in (ROOT / "examples" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert {parts[0] for parts in lines} == {"/planguard.toml", "/.github/", "/CODEOWNERS"}
    assert all(parts[1:] == ["@ZahraMost"] for parts in lines), lines


def test_readme_codeowners_block_matches_the_example_file():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = [ln for ln in (ROOT / "examples" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.startswith("#")]
    for line in example:
        assert " ".join(line.split()) in " ".join(readme.split()), line


def test_example_workflow_install_url_and_the_missing_tag_comment():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert 'pip install "git+https://github.com/ZahraMost/planguard.git@v0.1.0"' in text
    idx = text.index("git+https://github.com/ZahraMost/planguard.git@v0.1.0")
    comment_before = text[:idx].splitlines()[-6:]
    assert any("does not exist yet" in ln and ln.strip().startswith("#") for ln in comment_before), comment_before


# ---- 2. identical action versions ------------------------------------------------------------------------

def action_versions(path, prefix):
    return set(re.findall(rf"uses:\s*{re.escape(prefix)}@(\S+)", path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("action", ["actions/checkout", "actions/setup-python"])
def test_example_and_ci_use_identical_action_versions(action):
    in_ci, in_example = action_versions(CI, action), action_versions(EXAMPLE, action)
    assert len(in_ci) == 1 and len(in_example) == 1, (action, in_ci, in_example)
    assert in_ci == in_example, f"{action}: ci.yml uses {in_ci}, the example uses {in_example}"


# ---- 3. README status block --------------------------------------------------------------------------------

def test_readme_starts_with_the_status_block_before_any_heading():
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    first_heading = next(i for i, ln in enumerate(lines) if ln.startswith("#"))
    block = " ".join(ln.lstrip("> ").strip() for ln in lines[:first_heading] if ln.strip())
    assert lines[0].startswith(">"), "the status block is the very first thing in the README"
    assert "Status: pre-release (0.0.x), experimental." in block
    assert "narrow guardrail, not a security audit" in block
    assert "'this plan stays inside the contract you wrote', nothing more" in block
    assert "(see [What planguard does NOT claim](#what-planguard-does-not-claim))" in block
    assert "## What planguard does NOT claim" in "\n".join(lines)
