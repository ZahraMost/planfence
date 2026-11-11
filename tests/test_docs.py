"""Guard the safety guidance against accidental deletion (README and CLAUDE.md must stay in sync)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    ("contract from a protected place", ["base branch", "CODEOWNERS", "branch protection"]),
    ("apply exactly the plan you checked", ["terraform apply tfplan", "never re-plan"]),
    ("what planguard cannot see", ["provisioner", "local-exec", "data source"]),
    ("a pass is not an approval", ["not an approval"]),
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_ci_safety_guidance_present(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "Using planguard safely in CI" in text
    for topic, needles in REQUIRED:
        for needle in needles:
            assert needle in text, f"{name}: missing {needle!r} ({topic})"


def test_example_workflow_loads_contract_from_base_branch():
    text = (ROOT / "examples" / "github-actions" / "planguard-example.yml").read_text(encoding="utf-8")
    assert "base_ref" in text and "git show" in text
    assert "--config" in text and "--config planguard.toml" not in text


# ---- OpenTofu / plan_source documentation ------------------------------------

OPENTOFU_NEEDLES = [
    "plan_source",
    "OpenTofu 1.13.9",
    "Terraform 1.17.2",
    "16 scenarios",
    "no `complete`",
    "`applyable`",
    "-target",
    "-exclude",
    "tofu plan",
    "cannot be recognised",
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_opentofu_documented(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for needle in OPENTOFU_NEEDLES:
        assert needle in text, f"{name}: missing {needle!r}"


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_not_verified_list_no_longer_claims_opentofu_is_unverified(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "OpenTofu: `format_version` and action lists are NOT verified" not in text
    assert "**OpenTofu has not been verified.**" not in text
    assert "OpenTofu's format_version is NOT verified yet" not in text
    # what OpenTofu could not produce stays listed
    for needle in ("action_invocations", "deferred_changes", "deposed", "moved"):
        assert needle in text


def test_examples_mention_plan_source():
    for rel in ("examples/planguard.toml", "examples/github-actions/planguard-example.yml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "plan_source" in text and "opentofu" in text, rel
    wf = (ROOT / "examples/github-actions/planguard-example.yml").read_text(encoding="utf-8")
    assert "-target" in wf and "-exclude" in wf


# ---- the example workflow only CHECKS ---------------------------------------------------

WORKFLOW_NEEDLES = [
    "only CHECKS",
    "protected environment",
    "required reviewers",
    "accidental change",
    "uploading an artifact",
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_example_workflow_scope_documented(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for needle in WORKFLOW_NEEDLES:
        assert needle in text, f"{name}: missing {needle!r}"


# ---- round-2 review: plan modes, what planguard reads, versions, CODEOWNERS ----------------------

ROUND2_NEEDLES = [
    # what planguard reads vs what creating the plan does
    "planguard itself reads only the plan file",
    "may contact providers",
    "run data sources",
    "`external`",
    # plan modes and flags
    "Plan modes and flags",
    "-refresh-only",
    "-replace",
    "replace_by_request",
    "keep `no-replace` enabled",
    "output_changes",
    "complete: false",
    # versions and providers
    "`terraform_version`",
    "provider versions",
    ".terraform.lock.hcl",
    "unverified",
    # trust boundary
    "CODEOWNERS",
    "/planguard.toml",
    "/.github/",
    "/CODEOWNERS",
    "trust boundary",
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_round2_documentation_present(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for needle in ROUND2_NEEDLES:
        assert needle in text, f"{name}: missing {needle!r}"


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_misleading_claim_removed(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "never your cloud, state, or provider" not in text
    assert "it only reads the plan file" not in text


def test_codeowners_example_protects_the_trust_boundary():
    lines = [ln.split() for ln in (ROOT / "examples" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    protected = {parts[0] for parts in lines}
    assert {"/planguard.toml", "/.github/", "/CODEOWNERS"} <= protected
    assert all(len(parts) >= 2 and parts[1].startswith("@") for parts in lines)


def test_documented_mutant_count_matches_the_script():
    import importlib.util
    import re
    import sys

    spec = importlib.util.spec_from_file_location("mutation_check", ROOT / "scripts" / "mutation_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["mutation_check"] = module  # @dataclass needs the module to be registered
    try:
        spec.loader.exec_module(module)
        actual = len(module.MUTATIONS)
    finally:
        sys.modules.pop("mutation_check", None)
    for name in ("README.md", "CLAUDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        counts = [int(n) for n in re.findall(r"(\d+) (?:small )?source mutations", text)]
        assert counts == [actual], f"{name}: documented {counts}, script has {actual}"


# ---- moves (previous_address) -------------------------------------------------------------------

MOVE_NEEDLES = [
    "`previous_address`",
    "moved_update",
    "bypass",
    "moved-from=",
    "treats every move as a change",
    "both its address and its `previous_address`",
    "a pure move is not counted",
    "judged on its own",
    "4096",
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_moves_documented(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for needle in MOVE_NEEDLES:
        assert needle in text, f"{name}: missing {needle!r}"


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_moved_blocks_no_longer_listed_as_unverified(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "`moved` blocks (planguard judges only" not in text
    assert "`moved` blocks: not verified" not in text
    assert "ignores `previous_address`" not in text


# ---- state_only_plans -----------------------------------------------------------------------------

STATE_ONLY_NEEDLES = [
    'state_only_plans = "refuse"',
    "no resource changes",
    "pure move",
    "`resource_drift`",
    "output_added_only",
    "hand-made",
    "CI gate",
    "never refused by this key",
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_state_only_plans_documented(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for needle in STATE_ONLY_NEEDLES:
        assert needle in text, f"{name}: missing {needle!r}"


def test_example_config_recommends_refuse_for_ci_gates():
    text = (ROOT / "examples" / "planguard.toml").read_text(encoding="utf-8")
    assert 'state_only_plans = "refuse"' in text and "CI" in text


def test_fixture_readme_lists_outputs_and_the_handmade_drift_file():
    text = (ROOT / "tests" / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "outputs/" in text and "handmade/resource_drift.json" in text and "output_added_only" in text
