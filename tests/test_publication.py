"""Pre-publication checks: placeholders, action versions, README status and real example, privacy."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planfence import cli

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
PROVIDERS = FIX / "providers" / "terraform"
CI = ROOT / ".github" / "workflows" / "ci.yml"
EXAMPLE = ROOT / "examples" / "github-actions" / "planfence-example.yml"

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
    assert {parts[0] for parts in lines} == {"/planfence.toml", "/.github/", "/CODEOWNERS"}
    assert all(parts[1:] == ["@ZahraMost"] for parts in lines), lines


def test_readme_codeowners_block_matches_the_example_file():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = [ln for ln in (ROOT / "examples" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.startswith("#")]
    for line in example:
        assert " ".join(line.split()) in " ".join(readme.split()), line


def test_example_workflow_install_url_and_the_pinning_comment():
    text = EXAMPLE.read_text(encoding="utf-8")
    url = "git+https://github.com/ZahraMost/planfence.git@v0.1.0"
    assert f'pip install "{url}"' in text  # the install URL keeps pointing at the release tag
    comment = " ".join(ln.strip().lstrip("#").strip() for ln in text[:text.index(url)].splitlines()[-9:]
                       if ln.strip().startswith("#"))
    assert "release tag or a commit" in comment
    assert "trust boundary" in comment
    assert "A v0.1.0 tag is created when the release is published" in comment
    # the pre-release wording is gone
    assert "does not exist yet" not in text and "nothing has been released" not in text


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
    assert "Status: early release (0.1.x), experimental." in block
    assert "pre-release" not in block and "0.0.x" not in block
    assert "narrow guardrail, not a security audit" in block
    assert "'this plan stays inside the contract you wrote', nothing more" in block
    assert "(see [What planfence does NOT claim](#what-planfence-does-not-claim))" in block
    assert "## What planfence does NOT claim" in "\n".join(lines)


# ---- 4. the real plan example (Terraform 1.17.2, hashicorp/local + hashicorp/random) ----------------------------

EXPECTED_OUTPUT = (
    "[error] no-replace local_file.hello (local_file) delete,create\n"
    "planfence: FAIL - 1 violation(s)\n"
)


def load_make_fixtures():
    spec = importlib.util.spec_from_file_location("make_fixtures", ROOT / "scripts" / "make_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_fixtures"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("make_fixtures", None)
    return module


def test_the_real_provider_fixture_is_a_content_replacement():
    doc = json.loads((PROVIDERS / "local_file_replace.json").read_text(encoding="utf-8"))
    entries = {r["address"]: r for r in doc["resource_changes"]}
    assert set(entries) == {"local_file.hello", "random_pet.name"}
    hello = entries["local_file.hello"]
    assert hello["change"]["actions"] == ["delete", "create"]
    assert hello["action_reason"] == "replace_because_cannot_update"
    assert hello["change"]["replace_paths"] == [["content"]]
    assert entries["random_pet.name"]["change"]["actions"] == ["no-op"]
    assert doc["terraform_version"] == "1.17.2" and doc["format_version"] == "1.2"
    assert {r["provider_name"] for r in entries.values()} == {
        "registry.terraform.io/hashicorp/local", "registry.terraform.io/hashicorp/random"}


def test_exact_planfence_output_on_the_real_plan(capsys, tmp_path):
    cfg = tmp_path / "planfence.toml"
    cfg.write_text('enabled_rules = ["no-replace"]\n', encoding="utf-8")
    code = cli.main([str(PROVIDERS / "local_file_replace.json"), "--config", str(cfg)])
    assert code == 1
    assert capsys.readouterr().out == EXPECTED_OUTPUT


def test_a_replacement_is_not_a_plain_delete(capsys, tmp_path):
    cfg = tmp_path / "planfence.toml"
    cfg.write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    assert cli.main([str(PROVIDERS / "local_file_replace.json"), "--config", str(cfg)]) == 0
    capsys.readouterr()


def test_provider_versions_are_recorded_next_to_the_fixture():
    text = (PROVIDERS / "PROVIDERS.txt").read_text(encoding="utf-8")
    assert "terraform_version: 1.17.2" in text
    assert re.search(r"registry\.terraform\.io/hashicorp/local v\d+\.\d+\.\d+", text)
    assert re.search(r"registry\.terraform\.io/hashicorp/random v\d+\.\d+\.\d+", text)


def readme_section(title):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index(f"\n## {title}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def fences(section):
    return re.findall(r"```(\w*)\n(.*?)```", section, re.S)


def test_readme_real_plan_example_text():
    section = readme_section("Real plan example")
    for needle in ("Terraform 1.17.2", "hashicorp/local", "hashicorp/random", "no cloud account",
                   "-/+", "delete,create", "no-replace"):
        assert needle in section, needle
    assert EXPECTED_OUTPUT in section
    recorded = (PROVIDERS / "PROVIDERS.txt").read_text(encoding="utf-8")
    for version in re.findall(r"hashicorp/\w+ (v\d+\.\d+\.\d+)", recorded):
        assert version in section, f"provider version {version} is not stated in the README"


def test_readme_config_is_the_config_that_produced_the_plan():
    module = load_make_fixtures()
    scenario = module.SCENARIOS["local_file_replace"]
    blocks = dict(fences(readme_section("Real plan example")))
    hcl = [body for lang, body in fences(readme_section("Real plan example")) if lang == "hcl"]
    assert hcl and hcl[0].strip() == scenario.planned["main.tf"].strip()
    assert 'content  = "hello"' in scenario.baseline["main.tf"]  # what the earlier apply created
    assert 'enabled_rules = ["no-replace"]' in blocks["toml"]


def test_readme_plan_excerpt_is_real_terraform_output():
    excerpt = [body for lang, body in fences(readme_section("Real plan example")) if lang == "text"][0]
    real_lines = {ln.rstrip() for ln in (PROVIDERS / "local_file_replace.txt").read_text(encoding="utf-8").splitlines()}
    for line in excerpt.splitlines():
        if line.strip():
            assert line.rstrip() in real_lines, f"not in the real terraform output: {line!r}"
    assert "-/+ resource \"local_file\" \"hello\"" in excerpt


# ---- 5. privacy ----------------------------------------------------------------------------------------------

# built from pieces so that this file does not contain the strings it forbids
FORBIDDEN = [
    "mostat" + "abi", "yah" + "oo", "/Us" + "ers/", "/ho" + "me/", "fritz" + ".box", "zah" + "ras", "Startup" + " Founder",
    "var/fol" + "ders", "private/" + "tmp", "claude-" + "501",
]
ALLOWED_EMAIL_DOMAINS = {"example.invalid", "example.com"}
EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})\b")  # a real TLD: not git@v0.1.0
DOC_SUFFIXES = {".md", ".yml", ".yaml", ".toml", ".txt"}
MENTION = re.compile(r"(?<![\w./@+-])@([A-Za-z0-9][\w-]*)")


@needs_git
def test_no_personal_data_in_tracked_files():
    found = []
    for path in tracked_files():
        text = read_text_or_none(path)
        if text is None:
            continue
        rel = path.relative_to(ROOT)
        for needle in FORBIDDEN:
            if needle.lower() in text.lower():
                found.append(f"{rel}: contains {needle!r}")
        for m in EMAIL.finditer(text):
            if m.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
                found.append(f"{rel}: e-mail address with domain {m.group(1)}")
    assert found == []


@needs_git
def test_the_only_user_mentioned_in_tracked_files_is_zahramost():
    found = []
    for path in tracked_files():
        text = read_text_or_none(path)
        if text is None or not (path.suffix in DOC_SUFFIXES or path.name == "CODEOWNERS"):
            continue  # docs and config only: Python decorators such as @property are not mentions
        for m in MENTION.finditer(text):
            if m.group(1) != "ZahraMost" and path.relative_to(ROOT).parts[0] != "tests":
                found.append(f"{path.relative_to(ROOT)}: @{m.group(1)}")
    assert found == []
