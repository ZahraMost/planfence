"""output_changes and resource_drift are deliberately NOT checked in v0.1 (documented in README)."""

import json

import pytest

from planfence import cli
from planfence.parser import parse_plan
from tests.conftest import make_plan

ALL_RULES = """\
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]
[scope]
allow = ["nothing.*"]
[stateful]
types = ["aws_db_instance"]
[budget]
max_changes = 0
[budget.max_creates_per_type]
aws_db_instance = 0
"""

OUTPUT_CHANGES = {
    "db_endpoint": {"actions": ["delete"], "before": "x", "after": None, "after_unknown": False},
    "new_out": {"actions": ["create"], "before": None, "after": "y"},
}
RESOURCE_DRIFT = [
    {"address": "aws_db_instance.db", "type": "aws_db_instance", "change": {"actions": ["delete"]}},
    {"address": "aws_db_instance.db2", "type": "aws_db_instance", "change": {"actions": ["delete", "create"]}},
]


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "planfence.toml"
    p.write_text(ALL_RULES, encoding="utf-8")
    return p


def run(capsys, tmp_path, cfg, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    code = cli.main([str(p), "--config", str(cfg)])
    return code, capsys.readouterr().out


def _variants():
    for absent in (True, False):
        for extra in (
            {"output_changes": OUTPUT_CHANGES},
            {"resource_drift": RESOURCE_DRIFT},
            {"output_changes": OUTPUT_CHANGES, "resource_drift": RESOURCE_DRIFT},
        ):
            plan = make_plan(**extra)
            if absent:
                del plan["resource_changes"]
            yield plan


@pytest.mark.parametrize("plan", list(_variants()))
def test_changes_only_in_output_changes_or_resource_drift_exit_0(capsys, tmp_path, cfg, plan):
    assert parse_plan(plan).changes == ()
    code, out = run(capsys, tmp_path, cfg, plan)
    assert code == 0
    assert "OK - 0 change(s) checked" in out


def test_drift_and_output_changes_do_not_leak_into_resource_changes_checks(capsys, tmp_path, cfg):
    plan = make_plan(("aws_db_instance.db", "aws_db_instance", ["delete"]), output_changes=OUTPUT_CHANGES)
    code, out = run(capsys, tmp_path, cfg, plan)
    assert code == 1  # the real resource_changes delete is still caught
    assert out.count("aws_db_instance.db ") >= 1
