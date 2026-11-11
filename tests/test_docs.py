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
