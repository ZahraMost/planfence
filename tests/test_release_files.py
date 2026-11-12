"""Repository hygiene files: dependabot, SECURITY.md, CHANGELOG.md and their links from the README."""

import re
from pathlib import Path

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

def test_changelog_has_an_unreleased_0_1_0_entry_and_no_other_release():
    text = read("CHANGELOG.md")
    assert re.search(r"^## \[0\.1\.0\] - Unreleased$", text, re.M)
    assert len(re.findall(r"^## \[", text, re.M)) == 1
    assert "Keep a Changelog" in text


def test_changelog_mentions_the_main_features_and_security_behaviour():
    text = read("CHANGELOG.md")
    for needle in ("no-delete", "no-replace", "scope", "stateful-protect", "budget", "plan_source", "state_only_plans",
                   "previous_address", "--version", "fail-closed", "exit 2", "### Added", "### Security"):
        assert needle in text, needle


def test_version_was_not_bumped_or_tagged_by_the_changelog():
    assert "0.0.1" not in read("CHANGELOG.md") or "Unreleased" in read("CHANGELOG.md")


# ---- README links -----------------------------------------------------------------------------------------

def test_readme_links_both_files():
    text = read("README.md")
    assert "[SECURITY.md](SECURITY.md)" in text
    assert "[CHANGELOG.md](CHANGELOG.md)" in text
    assert (ROOT / "SECURITY.md").is_file() and (ROOT / "CHANGELOG.md").is_file()
