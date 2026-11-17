"""A for_each key rename a -> b, REAL plans from Terraform 1.17.2 and OpenTofu 1.13.9 (tests/fixtures/for_each_rename).

Without a moved block the plan is a delete of x["a"] plus a create of x["b"] (two different addresses); with a
moved block it is a pure move (no-op with previous_address). These tests pin what planfence actually does,
rule by rule, for both tools. Both tools give identical results.
"""

import json

import pytest

from planfence import cli
from tests.conftest import REAL

TOOLS = {"terraform": "", "opentofu": 'plan_source = "opentofu"\n'}
A = r'terraform_data.x["a"]'
B = r'terraform_data.x["b"]'
TOML_A, TOML_B = r'"terraform_data.x[\"a\"]"', r'"terraform_data.x[\"b\"]"'

DELETE_A = f"[error] no-delete {A} (terraform_data) delete\n"
OK2 = "planfence: OK - 2 change(s) checked, all enabled rules passed\n"
OK0 = "planfence: OK - 0 change(s) checked, all enabled rules passed\n"
FAIL1 = "planfence: FAIL - 1 violation(s)\n"
MOVE_SCOPE = f"[error] scope {B} (terraform_data) no-op moved-from={A}\n"

CONFIGS = {
    "no-delete": 'enabled_rules = ["no-delete"]\n',
    "no-replace": 'enabled_rules = ["no-replace"]\n',
    "scope a only": f'enabled_rules = ["scope"]\n[scope]\nallow = [{TOML_A}]\n',
    "scope b only": f'enabled_rules = ["scope"]\n[scope]\nallow = [{TOML_B}]\n',
    "scope a and b": f'enabled_rules = ["scope"]\n[scope]\nallow = [{TOML_A}, {TOML_B}]\n',
    "stateful-protect": 'enabled_rules = ["stateful-protect"]\n[stateful]\ntypes = ["terraform_data"]\n',
    "budget max 1": 'enabled_rules = ["budget"]\n[budget]\nmax_changes = 1\n',
    "budget max 2": 'enabled_rules = ["budget"]\n[budget]\nmax_changes = 2\n',
    "budget creates 0": 'enabled_rules = ["budget"]\n[budget.max_creates_per_type]\nterraform_data = 0\n',
    "state_only_plans allow": 'enabled_rules = ["no-delete"]\nstate_only_plans = "allow"\n',
    "state_only_plans refuse": 'enabled_rules = ["no-delete"]\nstate_only_plans = "refuse"\n',
}

# (exit code, stdout) per config; stdout is the whole text output
RENAME_WITHOUT_MOVED = {
    "no-delete": (1, DELETE_A + FAIL1),
    "no-replace": (0, OK2),  # delete and create are under DIFFERENT addresses: not a replacement
    "scope a only": (1, f"[error] scope {B} (terraform_data) create\n" + FAIL1),
    "scope b only": (1, f"[error] scope {A} (terraform_data) delete\n" + FAIL1),
    "scope a and b": (0, OK2),
    "stateful-protect": (1, f"[critical] stateful-protect {A} (terraform_data) delete\n" + FAIL1),
    "budget max 1": (1, "[error] budget total changes 2 > max_changes 1\n" + FAIL1),
    "budget max 2": (0, OK2),
    "budget creates 0": (1, "[error] budget creates of terraform_data 1 > limit 0\n" + FAIL1),
    "state_only_plans allow": (1, DELETE_A + FAIL1),
    "state_only_plans refuse": (1, DELETE_A + FAIL1),  # it has resource changes: never refused by this key
}
RENAME_WITH_MOVED = {
    "no-delete": (0, OK0),
    "no-replace": (0, OK0),
    "scope a only": (1, MOVE_SCOPE + FAIL1),  # the new address is not allowed
    "scope b only": (1, MOVE_SCOPE + FAIL1),  # the previous address is not allowed
    "scope a and b": (0, OK0),  # both addresses are allowed
    "stateful-protect": (0, OK0),
    "budget max 1": (0, OK0),  # a pure move is not counted as a change
    "budget max 2": (0, OK0),
    "budget creates 0": (0, OK0),
    "state_only_plans allow": (0, OK0),
    "state_only_plans refuse": (2, ""),  # no resource changes but a move
}

CASES = [(tool, scenario, name, expected)
         for tool in TOOLS
         for scenario, table in (("for_each_rename", RENAME_WITHOUT_MOVED), ("for_each_rename_moved", RENAME_WITH_MOVED))
         for name, expected in table.items()]


def fixture(tool, scenario):
    return REAL.parent / "for_each_rename" / tool / f"{scenario}.json"


@pytest.mark.parametrize("tool, scenario, name, expected", CASES,
                         ids=[f"{t}-{s}-{n.replace(' ', '_')}" for t, s, n, _ in CASES])
def test_planfence_result_on_the_real_plan(tool, scenario, name, expected, tmp_path, capsys):
    cfg = tmp_path / "planfence.toml"
    cfg.write_text(TOOLS[tool] + CONFIGS[name], encoding="utf-8")
    code = cli.main([str(fixture(tool, scenario)), "--config", str(cfg)])
    captured = capsys.readouterr()
    assert (code, captured.out) == expected
    if code == 2:
        assert 'state_only_plans = "refuse"' in captured.err and "moves resources" in captured.err
        assert "OK" not in captured.out


def test_the_tables_cover_every_config_for_both_scenarios():
    assert set(RENAME_WITHOUT_MOVED) == set(RENAME_WITH_MOVED) == set(CONFIGS)


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_what_the_tool_really_planned(tool):
    plain = json.loads(fixture(tool, "for_each_rename").read_text(encoding="utf-8"))
    entries = {r["address"]: r for r in plain["resource_changes"]}
    assert set(entries) == {A, B}
    assert entries[A]["change"]["actions"] == ["delete"] and entries[B]["change"]["actions"] == ["create"]
    assert entries[A]["action_reason"] == "delete_because_each_key"  # planfence never reads action_reason
    assert "previous_address" not in entries[A] and "previous_address" not in entries[B]
    moved = json.loads(fixture(tool, "for_each_rename_moved").read_text(encoding="utf-8"))
    (entry,) = moved["resource_changes"]
    assert entry["address"] == B and entry["previous_address"] == A and entry["change"]["actions"] == ["no-op"]
    expected_version = {"terraform": "1.17.2", "opentofu": "1.13.9"}[tool]
    assert plain["terraform_version"] == moved["terraform_version"] == expected_version


def test_both_tools_produce_identical_planned_changes():
    def shape(tool, scenario):
        doc = json.loads(fixture(tool, scenario).read_text(encoding="utf-8"))
        return [(r["address"], r.get("previous_address"), r["change"]["actions"]) for r in doc["resource_changes"]]
    for scenario in ("for_each_rename", "for_each_rename_moved"):
        assert shape("terraform", scenario) == shape("opentofu", scenario)


def test_readme_sentences_match_what_the_tests_above_prove():
    from tests.conftest import REAL
    readme = (REAL.parent.parent.parent / "README.md").read_text(encoding="utf-8")
    start = readme.index("\n### Renaming a `for_each` key\n")
    section = readme[start:readme.index("\n## ", start)]
    flat = " ".join(section.split())
    assert "Terraform 1.17.2 and OpenTofu 1.13.9" in flat
    assert "`no-delete` catches the delete" in flat and "`no-replace` does **not** fire" in flat
    assert RENAME_WITHOUT_MOVED["no-delete"][0] == 1 and RENAME_WITHOUT_MOVED["no-replace"][0] == 0
    assert "single `[\"no-op\"]` entry" in flat and "`budget` counts no change" in flat
    assert RENAME_WITH_MOVED["budget max 1"][0] == 0 and RENAME_WITH_MOVED["scope a only"][0] == 1
    assert 'With `state_only_plans = "refuse"` such a pure move exits 2' in flat
    assert RENAME_WITH_MOVED["state_only_plans refuse"][0] == 2
    assert "`budget` counts two changes and one create" in flat
    assert [t for t in __import__("re").findall(r"\]\(([^)\s]+)\)", section) if not t.startswith("https://")] == []
