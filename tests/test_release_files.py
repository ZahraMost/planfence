"""Repository hygiene files: dependabot, SECURITY.md, CHANGELOG.md and their links from the README."""

import re
import tomllib
from datetime import date
from pathlib import Path

import pytest

import planfence

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ---- dependabot ------------------------------------------------------------------------------------

def test_dependabot_covers_github_actions_weekly():
    text = read(".github/dependabot.yml")
    assert re.search(r"^version: 2$", text, re.M)
    assert re.search(r'^\s*-\s*package-ecosystem:\s*"?github-actions"?\s*$', text, re.M)
    assert re.search(r'^\s*directory:\s*"/"\s*$', text, re.M)
    assert re.search(r'^\s*interval:\s*"?weekly"?\s*$', text, re.M)
    assert "\t" not in text


def test_dependabot_only_touches_github_actions():
    ecosystems = re.findall(r"package-ecosystem:\s*\"?([\w-]+)", read(".github/dependabot.yml"))
    assert ecosystems == ["github-actions"]


# ---- SECURITY.md ---------------------------------------------------------------------------------------

SECURITY_NEEDLES = [
    "Supported versions",
    "latest `0.1.x` release",
    "current `main` branch",
    "Older versions are not supported",
    "Reporting a vulnerability",
    "privately",
    "security advisories",
    "Report a vulnerability",
    "planfence --version",
    "In scope",
    "Out of scope",
    "What planfence does not guarantee",
    "not an approval",
    "exit 0",
    "attribute values",
    "remove any secrets",
]


def test_supported_versions_section_states_the_release_policy():
    text = read("SECURITY.md")
    section = text[text.index("## Supported versions"):text.index("## Reporting a vulnerability")]
    assert "latest `0.1.x` release" in section and "current `main` branch" in section
    assert "Older versions are not supported" in section
    assert "pre-release" not in section and "0.0.x" not in section


def test_security_policy_content():
    text = read("SECURITY.md")
    for needle in SECURITY_NEEDLES:
        assert needle in text, f"SECURITY.md: missing {needle!r}"


def test_security_policy_asks_for_private_reporting_and_has_no_personal_contact_details():
    text = read("SECURITY.md")
    assert "public issue" in text  # tells people NOT to open one
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "no e-mail addresses in the policy"
    assert "http://" not in text


def test_security_scope_matches_the_documented_non_goals():
    text = read("SECURITY.md")
    for non_goal in ("provisioners", "data source", "cost", "output_changes", "-target", "lone `*`"):
        assert non_goal in text, non_goal


# ---- CHANGELOG.md --------------------------------------------------------------------------------------

CHANGELOG_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\S+)$", re.M)


def changelog_top_entry():
    match = CHANGELOG_HEADING.search(read("CHANGELOG.md"))
    assert match, "the CHANGELOG must start with a '## [X.Y.Z] - YYYY-MM-DD' entry"
    return match.group(1), match.group(2)


def release_date_problem(when, today):
    """None if `when` is a real ISO date that is not in the future, else a message."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        return f"{when!r} is not an ISO date (YYYY-MM-DD)"
    try:
        released = date.fromisoformat(when)
    except ValueError:
        return f"{when!r} is not a valid calendar date"
    if released > today:
        return f"{when} is in the future"
    return None


@pytest.mark.parametrize("when, problem", [
    ("2026-11-12", None),
    ("2026-11-11", None),
    ("Unreleased", "not an ISO date"),
    ("2026-13-40", "not a valid calendar date"),
    ("2026-11-13", "in the future"),
    ("12.11.2026", "not an ISO date"),
    ("", "not an ISO date"),
])
def test_the_release_date_guard_detects_bad_dates(when, problem):
    result = release_date_problem(when, today=date(2026, 11, 12))
    assert (result is None) if problem is None else (problem in result)


def test_versions_agree_and_the_top_changelog_entry_is_a_dated_release():
    pyproject = tomllib.loads(read("pyproject.toml"))["project"]["version"]
    version, when = changelog_top_entry()
    assert planfence.__version__ == pyproject == version, (planfence.__version__, pyproject, version)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert release_date_problem(when, today=date.today()) is None, release_date_problem(when, today=date.today())
    assert "Keep a Changelog" in read("CHANGELOG.md")


def test_readme_and_security_policy_do_not_claim_a_pre_release():
    for name in ("README.md", "SECURITY.md"):
        text = read(name).lower()
        assert "0.0.x" not in text and "pre-release" not in text, name


def test_changelog_mentions_the_main_features_and_security_behaviour():
    text = read("CHANGELOG.md")
    for needle in ("no-delete", "no-replace", "scope", "stateful-protect", "budget", "plan_source", "state_only_plans",
                   "previous_address", "--version", "fail-closed", "exit 2", "### Added", "### Security"):
        assert needle in text, needle


def test_changelog_announces_a_first_release_and_keeps_the_rename_note():
    text = read("CHANGELOG.md")
    assert "\nFirst release.\n" in text
    for stale in ("release candidate", "Not tagged", "Unreleased", "still `0.0.1`"):
        assert stale not in text, stale
    assert text.lower().count("formerly named") == 1  # the rename guard (tests/test_rename.py) needs exactly one


# ---- README links -----------------------------------------------------------------------------------------

def test_readme_links_both_files():
    text = read("README.md")
    assert "[SECURITY.md](https://github.com/ZahraMost/planfence/blob/main/SECURITY.md)" in text
    assert "[CHANGELOG.md](https://github.com/ZahraMost/planfence/blob/main/CHANGELOG.md)" in text
    assert (ROOT / "SECURITY.md").is_file() and (ROOT / "CHANGELOG.md").is_file()
