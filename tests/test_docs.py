"""Guard the safety guidance against accidental deletion (README and CLAUDE.md must stay in sync)."""

from pathlib import Path

import re

import pytest

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    ("contract from a protected place", ["base branch", "CODEOWNERS", "branch protection"]),
    ("apply exactly the plan you checked", ["terraform apply tfplan", "never re-plan"]),
    ("what planfence cannot see", ["provisioner", "local-exec", "data source"]),
    ("a pass is not an approval", ["not an approval"]),
]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_ci_safety_guidance_present(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "Using planfence safely in CI" in text
    for topic, needles in REQUIRED:
        for needle in needles:
            assert needle in text, f"{name}: missing {needle!r} ({topic})"


def test_example_workflow_loads_contract_from_base_branch():
    text = (ROOT / "examples" / "github-actions" / "planfence-example.yml").read_text(encoding="utf-8")
    assert "base_ref" in text and "git show" in text
    assert "--config" in text and "--config planfence.toml" not in text


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
    for rel in ("examples/planfence.toml", "examples/github-actions/planfence-example.yml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "plan_source" in text and "opentofu" in text, rel
    wf = (ROOT / "examples/github-actions/planfence-example.yml").read_text(encoding="utf-8")
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


# ---- round-2 review: plan modes, what planfence reads, versions, CODEOWNERS ----------------------

ROUND2_NEEDLES = [
    # what planfence reads vs what creating the plan does
    "planfence itself reads only the plan file",
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
    "/planfence.toml",
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
    assert {"/planfence.toml", "/.github/", "/CODEOWNERS"} <= protected
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
    "BOTH its address and its `previous_address`",
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
    assert "`moved` blocks (planfence judges only" not in text
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
    text = (ROOT / "examples" / "planfence.toml").read_text(encoding="utf-8")
    assert 'state_only_plans = "refuse"' in text and "CI" in text


def test_fixture_readme_lists_outputs_and_the_handmade_drift_file():
    text = (ROOT / "tests" / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "outputs/" in text and "handmade/resource_drift.json" in text and "output_added_only" in text


def test_claude_md_hard_constraints_name_the_only_downloadable_providers():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Never use cloud providers" in text
    for provider in ("hashicorp/random", "hashicorp/local", "hashicorp/null"):
        assert provider in text
    fixtures = (ROOT / "tests" / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "Providers downloaded" in fixtures


def test_quality_gates_documented():
    for name in ("README.md", "CLAUDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "ruff check src" in text and "mypy" in text, name
        assert "dev" in text.lower()


# ---- no wall-clock assertions ------------------------------------------------------------------------------------

TIMING_NEEDLES = ["no wall-clock", "operation count", "scaling ratio", "CPU time", "hang_guard", "tests/timing.py"]


def test_timing_policy_documented():
    for name in ("README.md", "CLAUDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for needle in TIMING_NEEDLES:
            assert needle in text, f"{name}: missing {needle!r}"


def test_no_test_asserts_on_measured_wall_clock_time():
    """Slow CI runners must not fail tests. Measured times are only allowed in tests/timing.py (CPU-time scaling
    ratios with retries, and the hang watchdog)."""
    import re

    clocks = re.compile(r"perf_counter|time\.time\(|monotonic|process_time|thread_time")
    offenders = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        if path.name in ("timing.py", "test_docs.py"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if clocks.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert offenders == []


# ---- README: Related tools --------------------------------------------------------------------------------------

RELATED_TOOLS_URL_ALLOWLIST = {
    "https://github.com/bridgecrewio/checkov",
    "https://trivy.dev/latest/docs/coverage/iac/terraform/",
    "https://github.com/open-policy-agent/conftest",
    "https://github.com/terraform-compliance/cli",
    "https://developer.hashicorp.com/terraform/cloud-docs/policy-enforcement/sentinel/import/tfplan-v2",
    "https://github.com/jamian/terraguard",
    "https://github.com/Dicklesworthstone/destructive_command_guard",
    "https://pypi.org/project/tfrev",
}
RELATED_TOOLS_NEEDLES = [
    "November 2026", "not tested against planfence", "complementary",
    "When planfence is a good fit", "When it is not enough",
    "Checkov", "Trivy", "Conftest", "terraform-compliance", "Sentinel", "Terraguard", "dcg", "tfrev",
]
# the comparison must stay factual: no superlatives, no "better than", no claim that nothing else exists
HONESTY_TERMS = ["best", "better than", "only tool", "unique", "no other", "superior", "unlike", "outperform", "no tool"]


def readme_text():
    return (ROOT / "README.md").read_text(encoding="utf-8")


def related_tools_section():
    text = readme_text()
    start = text.index("\n## Related tools\n")
    end = text.find("\n## ", start + 1)  # "### " subsections do not match "\n## "
    return text[start:end if end != -1 else None]


def test_related_tools_heading_exists_once_between_the_two_neighbouring_sections():
    import re

    lines = readme_text().splitlines()
    assert lines.count("## Related tools") == 1
    headings = [ln[3:] for ln in lines if ln.startswith("## ")]
    i = headings.index("Related tools")
    assert headings[i - 1] == "What planfence does NOT claim"
    assert headings[i + 1] == "Verified against"
    assert re.search(r"(?m)^## Related tools$", readme_text())


def test_related_tools_phrases_are_each_on_a_single_line():
    lines = related_tools_section().splitlines()
    for needle in RELATED_TOOLS_NEEDLES:
        assert any(needle in line for line in lines), f"{needle!r} is missing or wrapped across lines"


def test_related_tools_has_a_dated_disclaimer():
    import re

    section = related_tools_section()
    assert re.search(r"as of [A-Z][a-z]+ \d{4}", section)
    assert "may be out of date" in section and "check the linked pages" in section


def test_related_tools_links_are_exactly_the_allowlisted_https_urls():
    import re

    section = related_tools_section()
    assert "http://" not in section
    found = set(re.findall(r"https?://[^\s)>\]]+", section))
    assert found - RELATED_TOOLS_URL_ALLOWLIST == set(), f"URL(s) not in the allowlist: {sorted(found - RELATED_TOOLS_URL_ALLOWLIST)}"
    assert RELATED_TOOLS_URL_ALLOWLIST - found == set(), "an allowlisted tool is no longer linked"
    assert section.count("](https://") == len(RELATED_TOOLS_URL_ALLOWLIST)


def test_related_tools_makes_no_superlative_or_exclusivity_claims():
    import re

    section = related_tools_section()
    hits = [term for term in HONESTY_TERMS if re.search(rf"\b{re.escape(term)}\b", section, re.IGNORECASE)]
    assert hits == [], f"comparison must stay factual, found: {hits}"


def test_honesty_guard_really_detects_the_forbidden_terms():
    import re

    for term in HONESTY_TERMS:
        sample = f"This is the {term.upper()} option."
        assert re.search(rf"\b{re.escape(term)}\b", sample, re.IGNORECASE), term
    assert not re.search(r"\bbest\b", "the bestowed gift", re.IGNORECASE)  # whole words only


def test_related_tools_has_no_at_sign_and_no_email_address():
    import re

    section = related_tools_section()
    assert "@" not in section
    assert not re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", section)


def test_related_tools_section_content_is_structured():
    section = related_tools_section()
    assert "\n### When planfence is a good fit\n" in section and "\n### When it is not enough\n" in section
    rows = [ln for ln in section.splitlines() if ln.startswith("| ") and not ln.startswith("| Tool ")]
    assert len(rows) == 7  # Checkov, Trivy, Conftest, terraform-compliance, Sentinel, Terraguard, AI-agent guardrails
    assert all(row.count(" | ") == 2 and row.rstrip().endswith("|") for row in rows)
    assert '(see "Using planfence safely in CI")' in section
    assert "## Using planfence safely in CI" in readme_text()  # the cross-reference points at a real section


def test_claude_md_states_the_related_tools_policy():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Related tools policy" in text
    for needle in ("public documentation", "date-stamped", "superlatives", "better than", "tests/test_docs.py", "update the date"):
        assert needle in text, needle


def test_changelog_lists_the_related_tools_comparison_under_added():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    added = text[text.index("### Added"):text.index("### Security")]
    assert "README: Related tools comparison" in added


# ---- README accuracy round: Quick start sample ---------------------------------------------------------------------

def readme_section_between(start_heading, level="##"):
    text = readme_text()
    start = text.index(f"\n{start_heading}\n")
    end = text.find(f"\n{level} ", start + 1)
    return text[start:end if end != -1 else None]


def markdown_fences(section):
    return re.findall(r"```(\w*)\n(.*?)```", section, re.S)


def test_quick_start_sample_is_real_cli_output_on_a_named_fixture_and_config(capsys, tmp_path):
    section = readme_section_between("## Quick start")
    assert "real output" in section and "tests/fixtures/real/replace.json" in section
    blocks = markdown_fences(section)
    toml = [body for lang, body in blocks if lang == "toml"]
    consoles = [body for lang, body in blocks if lang == "console"]
    assert len(toml) == 1 and len(consoles) == 1, "one demo config and one sample output"
    command, *output_lines = consoles[0].rstrip("\n").splitlines()
    match = re.fullmatch(r"\$ planfence (\S+) --config (\S+)", command)
    assert match, command
    fixture, config_name = match.groups()
    assert fixture == "tests/fixtures/real/replace.json"
    assert config_name in section and config_name.endswith(".toml") and config_name != "planfence.toml"
    config = tmp_path / config_name
    config.write_text(toml[0], encoding="utf-8")
    from planfence import cli

    code = cli.main([str(ROOT / fixture), "--config", str(config)])
    out = capsys.readouterr().out
    assert out.splitlines() == output_lines, "the README sample must be exactly what the CLI prints"
    assert code == 1


def test_quick_start_sample_is_not_the_main_example():
    section = readme_section_between("## Quick start")
    assert "Real plan example" in section  # points to the main example (local_file, real providers)
    assert "local_file" not in section


# ---- README accuracy round: output_changes / resource_drift are DEFAULT behaviour ------------------------------------

def doc_units(text):
    """Split Markdown source into units: paragraphs, table rows, headings and list items. A list whose items are
    lowercase FRAGMENTS of the sentence before it (that sentence ends in ':') belongs to that sentence; lists of
    independent claims (capitalised, bold or code-first items) are split per item. Fenced code is skipped."""
    units, in_fence, current, attached, blank_seen = [], False, None, False, False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence, current, attached = not in_fence, None, False
            continue
        if in_fence:
            continue
        if not stripped:
            blank_seen = True
            if not attached:
                current = None
            continue
        is_item = re.match(r"\s*([-*]|\d+\.)\s", line) is not None
        if is_item:
            last = units[-1] if units else None
            body = re.sub(r"^([-*]|\d+\.)\s+", "", stripped)
            fragment = body[:1].islower()
            if attached and fragment:
                last.append(stripped)
            elif fragment and last and last[-1].endswith(":") and (blank_seen or current is last):
                attached = True
                last.append(stripped)
            else:
                attached = False
                current = [stripped]
                units.append(current)
            blank_seen = False
            continue
        if stripped.startswith(("|", "#")):
            units.append([stripped])
            current, attached = None, False
        elif attached and line.startswith((" ", "\t")):
            units[-1].append(stripped)
        else:
            attached = False
            if current is None:
                current = [stripped]
                units.append(current)
            else:
                current.append(stripped)
        blank_seen = False
    return [" ".join(unit) for unit in units]


def test_doc_units_splits_list_items_rows_and_keeps_intro_lists_together():
    sample = "\n".join([
        "Intro sentence about x:",
        "",
        "- first fragment mentioning output_changes;",
        "- second fragment;",
        "",
        "Another paragraph",
        "that wraps.",
        "",
        "- item one",
        "  continues here",
        "- item two",
        "  - nested item",
        "| a | b |",
        "|---|---|",
        "| row | cell |",
        "```",
        "code inside a fence",
        "```",
        "## Heading",
    ])
    units = doc_units(sample)
    assert units[0].startswith("Intro sentence about x: - first fragment") and "second fragment" in units[0]
    assert "Another paragraph that wraps." in units
    assert "- item one continues here" in units and "- item two" in units and "- nested item" in units
    assert "| row | cell |" in units and "## Heading" in units
    assert not any("code inside a fence" in unit for unit in units)


def test_doc_units_splits_lists_of_independent_claims_after_a_label():
    sample = "How changes are counted:\n\n- A **change** is something.\n- `read` is not a change.\n- **Bold** claim."
    units = doc_units(sample)
    assert units == ["How changes are counted:", "- A **change** is something.", "- `read` is not a change.",
                     "- **Bold** claim."]


def default_or_key_missing(text):
    """Units that mention output_changes / resource_drift without saying 'default' or naming state_only_plans."""
    return [unit for unit in doc_units(text)
            if re.search(r"output_changes|resource_drift", unit) and not re.search(r"default|state_only_plans", unit, re.I)]


def test_the_default_guard_detects_a_missing_qualifier():
    assert default_or_key_missing("- `output_changes` is ignored.\n- another item mentions nothing") != []
    assert default_or_key_missing("- `output_changes` is ignored by default.") == []
    assert default_or_key_missing("Some text:\n\n- `resource_drift` entry;\n- more;\n\n(state_only_plans)") != []
    assert default_or_key_missing("Refuse with state_only_plans, when there is:\n\n- a non-empty `resource_drift`;") == []
    assert default_or_key_missing("Counted:\n\n- `resource_drift` is not refused.") != []  # an independent claim


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md", "SECURITY.md"])
def test_every_mention_of_output_changes_or_resource_drift_says_default_or_names_the_key(name):
    offenders = default_or_key_missing((ROOT / name).read_text(encoding="utf-8"))
    assert offenders == [], f"{name}: no 'default' / 'state_only_plans' next to: {[o[:90] for o in offenders]}"


STATE_ONLY_ANCHOR = "(#refusing-state-only-plans-state_only_plans)"


def test_the_not_claimed_bullet_names_the_default_and_points_to_the_state_only_section():
    section = readme_section_between("## What planfence does NOT claim")
    bullets = [u for u in doc_units(section) if "output_changes" in u]
    assert len(bullets) == 1
    bullet = bullets[0]
    assert 'default `state_only_plans = "allow"`' in bullet
    assert 'state_only_plans = "refuse"' in bullet and "exit 2" in bullet
    assert STATE_ONLY_ANCHOR in bullet
    assert "v0.1 inspects" not in section
    assert "passes with exit 0" in bullet  # the example stays, now qualified by the default
    assert "### Refusing state-only plans: `state_only_plans`" in readme_text()  # the anchor target exists


def test_claude_md_and_security_say_default_where_they_mention_the_sections():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    units = doc_units(claude)
    count_only = [u for u in units if u.startswith("- Count only `resource_changes`")]
    assert len(count_only) == 1 and "by default" in count_only[0] and "state_only_plans" in count_only[0]
    nor = [u for u in units if "README must state what planfence does NOT claim" in u]
    assert len(nor) == 1 and "by default" in nor[0] and "v0.1, `output_changes`" not in nor[0]
    security = [u for u in doc_units((ROOT / "SECURITY.md").read_text(encoding="utf-8")) if "output_changes" in u]
    assert len(security) == 1 and "by default" in security[0] and 'state_only_plans = "allow"' in security[0]


# ---- README accuracy round: scope and moved resources say the same thing everywhere --------------------------------

BOTH_ADDRESSES = "for a moved resource BOTH its address and its `previous_address` must match the allowlist"


def test_scope_row_says_both_addresses_must_match():
    rows = [ln for ln in readme_text().splitlines() if ln.startswith("| `scope` |")]
    assert len(rows) == 1
    row = rows[0]
    assert "both" in row.lower() and "previous_address" in row
    assert BOTH_ADDRESSES in row
    assert "its address or its" not in row  # the old, differently worded (equivalent) formulation


def test_moved_resources_section_and_claude_md_use_the_same_sentence():
    moved = readme_section_between("### Moved resources")
    assert any(BOTH_ADDRESSES in line for line in moved.splitlines()), "wrapped or missing in 'Moved resources'"
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    holders = [line for line in claude.splitlines() if BOTH_ADDRESSES in line]
    assert len(holders) == 2, "CLAUDE.md: rule 3 and the 'Moves' bullet must both use the sentence"
    rule3 = [u for u in doc_units(claude) if u.startswith("3. scope")]
    assert len(rule3) == 1 and BOTH_ADDRESSES in rule3[0]
    moves = [u for u in doc_units(claude) if u.startswith("- Moves:")]
    assert len(moves) == 1 and BOTH_ADDRESSES in moves[0]


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md"])
def test_no_differently_worded_description_of_the_move_rule_remains(name):
    for unit in doc_units((ROOT / name).read_text(encoding="utf-8")):
        if "previous_address" in unit and "match the allowlist" in unit:
            assert BOTH_ADDRESSES in unit, f"{name}: {unit[:120]}"


# ---- README accuracy round: the exact list of what causes exit 2 -----------------------------------------------------

def exit_2_bullet():
    section = readme_section_between("## Rules (v0.1)")
    bullets = [u for u in doc_units(section) if u.startswith("- **Exit 2")]
    assert len(bullets) == 1, "one bullet must list what causes exit 2"
    return bullets[0]


def test_old_vague_sentence_is_gone():
    assert "Plans containing things planfence cannot judge" not in readme_text()


def test_exit_2_bullet_lists_every_cause():
    bullet = exit_2_bullet()
    for cause in ("change.importing", "action_invocations", "deferred_changes", "unknown or empty `actions` list",
                  "`errored: true`", "`complete` is not true", "unknown `format_version`"):
        assert cause in bullet, cause
    assert "invalid JSON" in bullet and "also exits 2" in bullet  # unparseable input is not silently skipped
    assert "Absent or empty `action_invocations` / `deferred_changes` are fine" in bullet


def test_opentofu_partial_plan_limitation_is_mentioned_where_it_is_true_only():
    bullet = exit_2_bullet()
    assert "`-target`" in bullet and "`complete: false`" in bullet  # Terraform: refused
    assert "OpenTofu" in bullet and "cannot be detected" in bullet and "not refused" in bullet
    # the other bullets of the rules section do not repeat the limitation
    section = readme_section_between("## Rules (v0.1)")
    others = [u for u in doc_units(section) if u.startswith("- ") and u != bullet and "partial" in u]
    assert others == []


def test_output_changes_and_resource_drift_are_ignored_by_default_is_said_separately():
    section = readme_section_between("## Rules (v0.1)")
    separate = [u for u in doc_units(section) if "output_changes" in u and "resource_drift" in u]
    assert len(separate) == 1
    text = separate[0]
    assert text.startswith("- ") and text != exit_2_bullet()
    assert "by default" in text and 'state_only_plans = "allow"' in text and "ignored" in text and "not refused" in text
    assert "(#refusing-state-only-plans-state_only_plans)" in text


def plan_with(**changes):
    doc = {"format_version": "1.2", "errored": False, "complete": True,
           "resource_changes": [{"address": "a.b", "type": "a", "change": {"actions": ["update"]}}]}
    for key, value in changes.items():
        doc[key] = value
    return doc


def run_cli_on(tmp_path, doc):
    import json

    from planfence import cli

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(doc), encoding="utf-8")
    cfg = tmp_path / "planfence.toml"
    cfg.write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    return cli.main([str(plan), "--config", str(cfg)])


def with_change(**change):
    doc = plan_with()
    doc["resource_changes"][0]["change"].update(change)
    return doc


LISTED_CAUSES = {
    "change.importing": with_change(importing={"id": "x"}),
    "non-empty action_invocations": plan_with(action_invocations=[{"x": 1}]),
    "non-empty deferred_changes": plan_with(deferred_changes=[{"x": 1}]),
    "unknown actions list": with_change(actions=["destroy"]),
    "empty actions list": with_change(actions=[]),
    "errored plan": plan_with(errored=True),
    "incomplete plan": plan_with(complete=False),
    "unknown format_version": plan_with(format_version="2.0"),
}


@pytest.mark.parametrize("cause", sorted(LISTED_CAUSES))
def test_every_listed_cause_really_exits_2(tmp_path, capsys, cause):
    assert run_cli_on(tmp_path, LISTED_CAUSES[cause]) == 2
    assert "OK" not in capsys.readouterr().out


def test_what_the_readme_says_is_fine_or_ignored_really_is(tmp_path, capsys):
    assert run_cli_on(tmp_path, plan_with(action_invocations=[], deferred_changes=[])) == 0  # absent or empty: fine
    drift = plan_with(output_changes={"o": {"actions": ["delete"]}}, resource_drift=[{"address": "x"}])
    drift["resource_changes"] = []
    assert run_cli_on(tmp_path, drift) == 0  # ignored by default
    capsys.readouterr()
