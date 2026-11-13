"""Issue templates and the README Support section (repo-only files, not part of the package)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"


def read(path):
    return path.read_text(encoding="utf-8")


def test_issue_chooser_config_disables_blank_issues_and_links_private_security_reporting():
    text = read(TEMPLATES / "config.yml")
    assert re.search(r"^blank_issues_enabled: false$", text, re.M)
    assert "name: Report a security vulnerability privately" in text
    assert "url: https://github.com/ZahraMost/planfence/security/advisories/new" in text
    assert text.count("- name:") == 1


def test_bug_report_form_has_the_required_fields_and_the_secrets_warning():
    text = read(TEMPLATES / "bug_report.yml")
    assert re.search(r'^labels: \["bug"\]$', text, re.M)
    blocks = re.split(r"^  - type: ", text.split("\nbody:\n", 1)[1], flags=re.M)[1:]
    by_id = {re.search(r"^    id: (\S+)", b, re.M).group(1): b for b in blocks if re.search(r"^    id: ", b, re.M)}
    assert set(by_id) == {"planfence-version", "tool-version", "what-happened", "exit-code", "config", "plan", "secrets"}
    for field, block in by_id.items():
        assert "required: true" in block, f"{field} must be required"
    assert "planfence --version" in by_id["planfence-version"]
    assert "terraform version" in by_id["tool-version"] and "tofu version" in by_id["tool-version"]
    assert "passed something it should have refused" in by_id["what-happened"]
    assert "planfence.toml" in by_id["config"] and "plan JSON" in by_id["plan"]
    assert "WARNING" in by_id["plan"] and "remove ALL" in by_id["plan"] and "secrets" in by_id["plan"]
    assert "@" not in text


def test_readme_support_section_exists_and_states_best_effort():
    readme = read(ROOT / "README.md")
    assert "\n## Support\n" in readme
    start = readme.index("\n## Support\n")
    section = readme[start:readme.find("\n## ", start + 1)]
    flat = " ".join(section.split())
    assert "one-person project" in flat and "best-effort basis" in flat and "no response-time guarantee" in flat
    assert "minimal plan JSON" in flat and "remove secrets and attribute values" in flat
    assert "(https://github.com/ZahraMost/planfence/blob/main/SECURITY.md)" in flat
    assert "private vulnerability reporting" in flat
    assert [t for t in re.findall(r"\]\(([^)\s]+)\)", section) if not t.startswith("https://")] == []
