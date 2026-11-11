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
