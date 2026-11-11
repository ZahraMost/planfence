"""Behavioural and structural tests of examples/github-actions/planguard-example.yml.

The workflow is read as plain text (no YAML dependency). Its "Load contract from the base
branch" step is extracted and actually executed with bash against a throwaway git setup.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from planguard import cli
from tests.conftest import fixture_path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "github-actions" / "planguard-example.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
STEP_NAME = "Load contract from the base branch"

STRICT = 'enabled_rules = ["scope"]\n[scope]\nallow = ["aws_s3_bucket.*"]\n'
WEAK = 'enabled_rules = ["scope"]\n[scope]\nallow = ["*"]\n'

needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="bash and git are required to execute the workflow step",
)


# ---- plain-text extraction ---------------------------------------------------------

def step_script(text: str, step_name: str = STEP_NAME) -> str:
    """The shell script of the `run: |` block of the step called `step_name`."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"- name: {step_name}"), None)
    assert start is not None, f"step {step_name!r} not found"
    run_at = next(i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("run:"))
    assert lines[run_at].strip() == "run: |", "expected a literal block script"
    indent = len(lines[run_at]) - len(lines[run_at].lstrip())
    body = []
    for ln in lines[run_at + 1:]:
        if ln.strip() == "":
            body.append("")
        elif len(ln) - len(ln.lstrip()) > indent:
            body.append(ln)
        else:
            break
    block_indent = min(len(ln) - len(ln.lstrip()) for ln in body if ln)
    return "\n".join(ln[block_indent:] for ln in body).strip() + "\n"


# ---- a throwaway origin / pull-request checkout ----------------------------------------

def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    env.update(extra)
    return env


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=_env(), check=True, capture_output=True, text=True)


@pytest.fixture
def pr_checkout(tmp_path):
    """main has the strict contract; branch `pr` weakens it; the checkout holds only `pr`
    (like a CI checkout of a pull request: origin/main is not present until fetched)."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    url = f"file://{origin}"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    (seed / "planguard.toml").write_text(STRICT, encoding="utf-8")
    git(seed, "add", "planguard.toml")
    git(seed, "commit", "-m", "strict contract")
    git(seed, "remote", "add", "origin", url)
    git(seed, "push", "origin", "main")
    git(seed, "checkout", "-b", "pr")
    (seed / "planguard.toml").write_text(WEAK, encoding="utf-8")
    git(seed, "commit", "-am", "weaken the contract")
    git(seed, "push", "origin", "pr")

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-b", "pr")
    git(checkout, "remote", "add", "origin", url)
    git(checkout, "fetch", "--no-tags", "--depth=1", "origin", "pr")
    git(checkout, "checkout", "FETCH_HEAD")
    runner = tmp_path / "runner"
    runner.mkdir()
    return checkout, runner


def run_step(script, checkout, runner, base_ref="main"):
    # GitHub's default shell for `run:` is: bash --noprofile --norc -eo pipefail {0}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=checkout, env=_env(BASE_REF=base_ref, RUNNER_TEMP=str(runner)),
        capture_output=True, text=True,
    )


def planguard_exit(contract: Path) -> int:
    return cli.main([str(fixture_path("delete")), "--config", str(contract)])


# ---- tests ---------------------------------------------------------------------------

def test_extraction_finds_the_expected_commands():
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    assert 'git fetch --no-tags --depth=1 origin "$BASE_REF"' in script
    assert 'git show "origin/$BASE_REF:planguard.toml"' in script and "$RUNNER_TEMP/planguard.toml" in script


@needs_tools
def test_step_produces_the_base_branch_contract_not_the_pr_one(pr_checkout, capsys):
    checkout, runner = pr_checkout
    assert (checkout / "planguard.toml").read_text(encoding="utf-8") == WEAK  # the PR checkout is weakened
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    proc = run_step(script, checkout, runner)
    assert proc.returncode == 0, proc.stderr
    produced = runner / "planguard.toml"
    assert produced.read_text(encoding="utf-8") == STRICT
    assert (checkout / "planguard.toml").read_text(encoding="utf-8") == WEAK  # checkout left untouched

    # the contract the step produced catches the delete ...
    assert planguard_exit(produced) == 1
    capsys.readouterr()
    # ... while the PR's own weakened contract would let the same plan pass (the threat)
    assert planguard_exit(checkout / "planguard.toml") == 0


@needs_tools
def test_step_fails_closed_when_the_base_ref_cannot_be_fetched(pr_checkout):
    checkout, runner = pr_checkout
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    proc = run_step(script, checkout, runner, base_ref="no-such-branch")
    assert proc.returncode != 0
    assert not (runner / "planguard.toml").exists() or (runner / "planguard.toml").read_text() == ""


@needs_tools
def test_negative_control_reading_the_contract_from_the_checkout_is_the_threat(pr_checkout, capsys):
    """If the step took the contract from the PR checkout, the weakened version would win."""
    checkout, runner = pr_checkout
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    bad = script.replace('git show "origin/$BASE_REF:planguard.toml"', "cat planguard.toml")
    assert bad != script
    assert run_step(bad, checkout, runner).returncode == 0
    produced = runner / "planguard.toml"
    assert produced.read_text(encoding="utf-8") == WEAK
    assert planguard_exit(produced) == 0
    capsys.readouterr()
