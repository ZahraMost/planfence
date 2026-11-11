"""Hard limit on address / type / deposed length (4096 characters)."""

import pytest

from planfence.parser import MAX_STRING_LENGTH, PlanError, parse_plan
from tests.conftest import make_plan

FIELDS = ["address", "type", "deposed"]
SECRET = "S3CR3T"


def plan_with(field, value):
    plan = make_plan(("a.b", "a", ["delete"]), ("c.d", "c", ["update"]))
    plan["resource_changes"][1][field] = value  # index 1
    return plan


def test_limit_value():
    assert MAX_STRING_LENGTH == 4096


@pytest.mark.parametrize("field", FIELDS)
def test_exactly_4096_accepted(field):
    value = "x" * 4096
    plan = parse_plan(plan_with(field, value))
    assert any(getattr(ch, field) == value for ch in plan.changes)


@pytest.mark.parametrize("field", FIELDS)
def test_4097_rejected(field):
    with pytest.raises(PlanError) as ei:
        parse_plan(plan_with(field, "x" * 4097))
    msg = str(ei.value)
    assert field in msg and "[1]" in msg


@pytest.mark.parametrize("field", FIELDS)
def test_value_never_echoed(field):
    with pytest.raises(PlanError) as ei:
        parse_plan(plan_with(field, SECRET + "x" * 5000))
    assert SECRET not in str(ei.value)


def test_length_counts_characters_not_bytes():
    parse_plan(plan_with("address", "é" * 4096))
    with pytest.raises(PlanError):
        parse_plan(plan_with("address", "é" * 4097))


def test_huge_string_rejected_without_issue():
    with pytest.raises(PlanError):
        parse_plan(plan_with("address", "x" * 5_000_000))


def test_cli_exit_2_and_no_value(capsys, tmp_path):
    import json

    from planfence import cli

    cfg = tmp_path / "c.toml"
    cfg.write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    p = tmp_path / "p.json"
    p.write_text(json.dumps(plan_with("address", SECRET + "x" * 5000)), encoding="utf-8")
    code = cli.main([str(p), "--config", str(cfg)])
    out = capsys.readouterr()
    assert code == 2 and "OK" not in out.out and SECRET not in out.out + out.err
