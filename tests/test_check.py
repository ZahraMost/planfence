from planguard.config import parse_config
from planguard.parser import parse_plan
from planguard.rules import check
from tests.conftest import load_fixture


def run(name, cfg):
    return check(parse_plan(load_fixture(name)).changes, parse_config(cfg))


def test_only_enabled_rules_run():
    assert run("delete", {"enabled_rules": ["no-replace"]}) == []
    assert [v.rule for v in run("delete", {"enabled_rules": ["no-delete"]})] == ["no-delete"]


def test_replace_trips_several_rules_sorted_deterministically():
    cfg = {
        "enabled_rules": ["no-replace", "no-delete", "stateful-protect", "scope"],
        "stateful": {"types": ["terraform_data"]},
        "scope": {"allow": ["nothing.*"]},
    }
    got = [(v.rule, v.severity) for v in run("replace", cfg)]
    assert got == [("no-replace", "error"), ("scope", "error"), ("stateful-protect", "critical")]


def test_compliant_plan_has_no_violations():
    cfg = {
        "enabled_rules": ["no-delete", "no-replace", "scope", "stateful-protect", "budget"],
        "scope": {"allow": ["terraform_data.*"]},
        "stateful": {"types": ["aws_db_instance"]},
        "budget": {"max_changes": 1, "max_creates_per_type": {"terraform_data": 1}},
    }
    assert run("update", cfg) == []
    assert run("empty", cfg) == []
