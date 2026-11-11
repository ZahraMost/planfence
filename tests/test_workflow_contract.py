"""Behavioural and structural tests of examples/github-actions/planfence-example.yml.

The workflow is read as plain text (no YAML dependency). Its "Load contract from the base
branch" step is extracted and actually executed with bash against a throwaway git setup.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from planfence import cli
from tests.conftest import fixture_path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "github-actions" / "planfence-example.yml"
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
    (seed / "planfence.toml").write_text(STRICT, encoding="utf-8")
    git(seed, "add", "planfence.toml")
    git(seed, "commit", "-m", "strict contract")
    git(seed, "remote", "add", "origin", url)
    git(seed, "push", "origin", "main")
    git(seed, "checkout", "-b", "pr")
    (seed / "planfence.toml").write_text(WEAK, encoding="utf-8")
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


def planfence_exit(contract: Path) -> int:
    return cli.main([str(fixture_path("delete")), "--config", str(contract)])


# ---- tests ---------------------------------------------------------------------------

def test_extraction_finds_the_expected_commands():
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    assert 'git fetch --no-tags --depth=1 origin "$BASE_REF"' in script
    assert 'git show "origin/$BASE_REF:planfence.toml"' in script and "$RUNNER_TEMP/planfence.toml" in script


@needs_tools
def test_step_produces_the_base_branch_contract_not_the_pr_one(pr_checkout, capsys):
    checkout, runner = pr_checkout
    assert (checkout / "planfence.toml").read_text(encoding="utf-8") == WEAK  # the PR checkout is weakened
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    proc = run_step(script, checkout, runner)
    assert proc.returncode == 0, proc.stderr
    produced = runner / "planfence.toml"
    assert produced.read_text(encoding="utf-8") == STRICT
    assert (checkout / "planfence.toml").read_text(encoding="utf-8") == WEAK  # checkout left untouched

    # the contract the step produced catches the delete ...
    assert planfence_exit(produced) == 1
    capsys.readouterr()
    # ... while the PR's own weakened contract would let the same plan pass (the threat)
    assert planfence_exit(checkout / "planfence.toml") == 0


@needs_tools
def test_step_fails_closed_when_the_base_ref_cannot_be_fetched(pr_checkout):
    checkout, runner = pr_checkout
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    proc = run_step(script, checkout, runner, base_ref="no-such-branch")
    assert proc.returncode != 0
    assert not (runner / "planfence.toml").exists() or (runner / "planfence.toml").read_text() == ""


@needs_tools
def test_negative_control_reading_the_contract_from_the_checkout_is_the_threat(pr_checkout, capsys):
    """If the step took the contract from the PR checkout, the weakened version would win."""
    checkout, runner = pr_checkout
    script = step_script(EXAMPLE.read_text(encoding="utf-8"))
    bad = script.replace('git show "origin/$BASE_REF:planfence.toml"', "cat planfence.toml")
    assert bad != script
    assert run_step(bad, checkout, runner).returncode == 0
    produced = runner / "planfence.toml"
    assert produced.read_text(encoding="utf-8") == WEAK
    assert planfence_exit(produced) == 0
    capsys.readouterr()


# ---- structure: plan identity, order, no re-plan, timeouts ------------------------------------

def code_lines(text):
    """(line number, stripped text) of every non-comment, non-blank line."""
    return [(i, ln.strip()) for i, ln in enumerate(text.splitlines(), 1) if ln.strip() and not ln.strip().startswith("#")]


def comment_lines(text):
    return [(i, ln.strip().lstrip("#").strip()) for i, ln in enumerate(text.splitlines(), 1) if ln.strip().startswith("#")]


def _find(lines, pattern):
    rx = re.compile(pattern)
    return [(i, m) for i, ln in lines if (m := rx.search(ln))]


def workflow_problems(text):
    """Everything wrong with the example workflow's plan identity / ordering (empty list = fine)."""
    problems = []
    code, comments = code_lines(text), comment_lines(text)
    plan = _find(code, r"\b(?:terraform|tofu) plan\b.*-out=(\S+)")
    show = _find(code, r"\b(?:terraform|tofu) show -json (\S+)")
    checksum = _find(code, r"\bsha256sum (\S+) >")
    load = _find(code, r'git show "origin/')
    check = _find(code, r"\bplanfence \S+ --config")
    apply_comment = _find(comments, r"sha256sum -c (\S+) && (?:terraform|tofu) apply (\S+)")
    for name, found in (("plan -out", plan), ("show -json", show), ("sha256sum", checksum),
                        ("contract load", load), ("planfence check", check), ("commented apply", apply_comment)):
        if len(found) != 1:
            problems.append(f"expected exactly one {name} line, found {len(found)}")
    if problems:
        return problems

    names = {plan[0][1].group(1), show[0][1].group(1), checksum[0][1].group(1),
             apply_comment[0][1].group(2)}
    if names != {"tfplan"}:
        problems.append(f"plan file names differ or are not 'tfplan': {sorted(names)}")
    if apply_comment[0][1].group(1) != "tfplan.sha256":
        problems.append("apply line does not verify tfplan.sha256")
    order = [plan[0][0], show[0][0], checksum[0][0], load[0][0], check[0][0]]
    if order != sorted(order):
        problems.append(f"wrong order (plan, show, checksum, contract load, check): lines {order}")
    check_line = check[0][0]
    if [i for i, _ in _find(code, r"\b(?:terraform|tofu) plan\b") if i != plan[0][0]]:
        problems.append("a second plan command exists")
    if [i for i, _ in _find(code, r"\b(?:terraform|tofu) plan\b") if i > check_line]:
        problems.append("a plan command runs after the check")
    if _find(code, r"\b(?:terraform|tofu) (?:apply|refresh|import|destroy)\b"):
        problems.append("the example must only CHECK, not change anything")
    stray = {t for t in re.findall(r"tfplan[\w.\-]*", text)} - {"tfplan", "tfplan.sha256"}
    if stray:
        problems.append(f"unexpected plan file names: {sorted(stray)}")
    if text.count("runs-on:") != text.count("timeout-minutes:") or "timeout-minutes:" not in text:
        problems.append("every job needs timeout-minutes")
    return problems


def test_example_workflow_structure_is_sound():
    assert workflow_problems(EXAMPLE.read_text(encoding="utf-8")) == []


def test_both_workflow_files_set_timeout_minutes_on_every_job():
    for path in (EXAMPLE, CI):
        text = path.read_text(encoding="utf-8")
        assert "timeout-minutes:" in text, path
        assert text.count("runs-on:") == text.count("timeout-minutes:"), path


def _mutate(old, new, count=1):
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text, old
    return text.replace(old, new, count)


NEGATIVE_CONTROLS = {
    "different plan file in -out": _mutate("-out=tfplan", "-out=other.plan"),
    "different file in show": _mutate("show -json tfplan >", "show -json other.plan >"),
    "checksum of another file": _mutate("sha256sum tfplan >", "sha256sum plan.json >"),
    "apply line uses another file": _mutate("&& terraform apply tfplan", "&& terraform apply other.plan"),
    "apply line without verification": _mutate("sha256sum -c tfplan.sha256 && ", ""),
    "second plan after the check": _mutate(
        "      - name: Remove plan files",
        "      - name: Re-plan\n        run: terraform plan -input=false -out=tfplan\n      - name: Remove plan files"),
    "real apply step": _mutate(
        "      - name: Remove plan files",
        "      - name: Apply\n        run: terraform apply tfplan\n      - name: Remove plan files"),
    "contract loaded after the check": _mutate(
        'run: planfence plan.json --config "$RUNNER_TEMP/planfence.toml"',
        'run: planfence plan.json --config "$RUNNER_TEMP/planfence.toml"\n'
        '      - name: Late load\n        run: git show "origin/main:planfence.toml" > x'),
    "no timeout": _mutate("    timeout-minutes: 5\n", ""),
    "no checksum step": _mutate("          sha256sum tfplan > tfplan.sha256\n", ""),
}


@pytest.mark.parametrize("name", sorted(NEGATIVE_CONTROLS))
def test_structure_check_detects_broken_workflows(name):
    assert workflow_problems(NEGATIVE_CONTROLS[name]), f"not detected: {name}"
