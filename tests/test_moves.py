"""`moved` blocks: a plan entry with previous_address is a move.

Real fixtures (tests/fixtures/moved, Terraform 1.17.2 and OpenTofu 1.13.9):
  moved_only         terraform_data.old -> terraform_data.new            ["no-op"]
  moved_update       terraform_data.old -> terraform_data.new + input    ["update"]
  moved_into_module  terraform_data.thing -> module.m.terraform_data.thing ["no-op"]

The scope rule treats every move as a change and requires BOTH addresses to be allowlisted.
Before this rule, `moved_update` with only the NEW address allowed passed (the bypass).
"""

import json
from pathlib import Path

import pytest

from planfence import cli
from planfence.parser import Change, PlanError, parse_plan
from planfence.rules import Violation, scope
from tests.conftest import make_plan

MOVED = Path(__file__).resolve().parent / "fixtures" / "moved"
TOOLS = ["terraform", "opentofu"]
SCENARIOS = {
    "moved_only": ("terraform_data.old", "terraform_data.new", "no-op"),
    "moved_update": ("terraform_data.old", "terraform_data.new", "update"),
    "moved_into_module": ("terraform_data.thing", "module.m.terraform_data.thing", "no-op"),
}
PURE = ["moved_only", "moved_into_module"]


def fixture(tool, name):
    return MOVED / tool / f"{name}.json"


def write_cfg(tmp_path, tool, rules, allow=None, extra=""):
    lines = []
    if tool == "opentofu":
        lines.append('plan_source = "opentofu"')
    lines.append(f"enabled_rules = {json.dumps(rules)}")
    lines.append(extra)
    if allow is not None:
        lines += ["[scope]", f"allow = {json.dumps(allow)}"]
    p = tmp_path / "planfence.toml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run(capsys, path, cfg, fmt="text"):
    code = cli.main([str(path), "--config", str(cfg), "--format", fmt])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- the real fixtures, parsed ----------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_real_fixtures_parse_as_moves(tool, name):
    old, new, action = SCENARIOS[name]
    (change,) = parse_plan(json.loads(fixture(tool, name).read_text(encoding="utf-8")), tool).changes
    assert (change.address, change.previous_address, change.actions) == (new, old, (action,))
    assert change.is_move is True
    assert change.is_change is (action != "no-op")


# ---- scope on the real fixtures ----------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("allow_key", ["new", "old"])
def test_only_one_of_the_two_addresses_allowed_is_a_violation(capsys, tmp_path, tool, name, allow_key):
    old, new, action = SCENARIOS[name]
    cfg = write_cfg(tmp_path, tool, ["scope"], [new if allow_key == "new" else old])
    code, out, _ = run(capsys, fixture(tool, name), cfg)
    assert code == 1
    assert out.splitlines() == [
        f"[error] scope {new} (terraform_data) {action} moved-from={old}",
        "planfence: FAIL - 1 violation(s)",
    ]


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_both_addresses_allowed_passes(capsys, tmp_path, tool, name):
    old, new, _ = SCENARIOS[name]
    cfg = write_cfg(tmp_path, tool, ["scope"], [old, new])
    code, out, _ = run(capsys, fixture(tool, name), cfg)
    assert code == 0 and "OK" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_the_bypass_moved_update_with_only_the_new_address_allowed_now_fails(capsys, tmp_path, tool):
    """Before: exit 0 ("1 change checked"), although the resource being updated lived at terraform_data.old."""
    cfg = write_cfg(tmp_path, tool, ["scope"], ["terraform_data.new"])
    code, out, _ = run(capsys, fixture(tool, "moved_update"), cfg)
    assert code == 1 and "moved-from=terraform_data.old" in out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("name", PURE)
def test_pure_move_is_a_scope_violation_even_though_the_action_is_noop(capsys, tmp_path, tool, name):
    _, new, _ = SCENARIOS[name]
    cfg = write_cfg(tmp_path, tool, ["scope"], ["nothing.*"])
    code, out, _ = run(capsys, fixture(tool, name), cfg)
    assert code == 1 and f"[error] scope {new} " in out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scope_disabled_pure_moves_exit_0(capsys, tmp_path, tool, name):
    cfg = write_cfg(tmp_path, tool, ["no-delete", "no-replace"])
    code, out, _ = run(capsys, fixture(tool, name), cfg)
    assert code == 0 and "OK" in out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("name", PURE)
def test_pure_move_is_not_counted_by_budget_nor_flagged_by_other_rules(capsys, tmp_path, tool, name):
    cfg = write_cfg(
        tmp_path, tool, ["no-delete", "no-replace", "stateful-protect", "budget"],
        extra='[stateful]\ntypes = ["terraform_data"]\n[budget]\nmax_changes = 0\n[budget.max_creates_per_type]\nterraform_data = 0',
    )
    code, out, _ = run(capsys, fixture(tool, name), cfg)
    assert code == 0 and "OK - 0 change(s) checked" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_moved_update_counts_as_one_change_in_budget(capsys, tmp_path, tool):
    cfg = write_cfg(tmp_path, tool, ["budget"], extra="[budget]\nmax_changes = 0")
    code, out, _ = run(capsys, fixture(tool, "moved_update"), cfg)
    assert code == 1 and "budget total changes 1 > max_changes 0" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_json_output_has_previous_address(capsys, tmp_path, tool):
    cfg = write_cfg(tmp_path, tool, ["scope"], ["terraform_data.new"])
    code, out, _ = run(capsys, fixture(tool, "moved_only"), cfg, "json")
    assert code == 1
    (v,) = json.loads(out)["violations"]
    assert v == {"rule": "scope", "severity": "error", "address": "terraform_data.new", "type": "terraform_data",
                 "action": "no-op", "deposed": None, "previous_address": "terraform_data.old"}


def test_json_violations_always_contain_previous_address_null_when_none(capsys, tmp_path):
    cfg = write_cfg(tmp_path, "terraform", ["no-delete", "budget"], extra="[budget]\nmax_changes = 0")
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps(make_plan(("a.b", "a", ["delete"]))), encoding="utf-8")
    code, out, _ = run(capsys, plan, cfg, "json")
    assert code == 1
    for v in json.loads(out)["violations"]:
        assert "previous_address" in v and v["previous_address"] is None


# ---- hand-built plans ----------------------------------------------------------------------------

def moved_plan(*entries):
    """entries: (address, previous_address, actions[, deposed])"""
    items = []
    for address, previous, actions, *rest in entries:
        items.append((address, "t", actions) + tuple(rest))
    plan = make_plan(*items)
    for rc, (_, previous, *_r) in zip(plan["resource_changes"], entries):
        if previous is not ...:
            rc["previous_address"] = previous
    return plan


def changes_of(*entries):
    return parse_plan(moved_plan(*entries)).changes


@pytest.mark.parametrize("bad", [None, 5, True, False, [], {}, "", 1.5, ["a"], {"a": 1}], ids=repr)
def test_malformed_previous_address_exits_2(bad):
    with pytest.raises(PlanError) as ei:
        parse_plan(moved_plan(("a.b", bad, ["no-op"])))
    assert "previous_address" in str(ei.value) and "resource_changes[0]" in str(ei.value)


def test_too_long_previous_address_is_rejected_and_not_echoed():
    parse_plan(moved_plan(("a.b", "x" * 4096, ["no-op"])))  # exactly 4096 is fine
    with pytest.raises(PlanError) as ei:
        parse_plan(moved_plan(("a.b", "SECRET" + "x" * 4091, ["no-op"])))
    assert "previous_address" in str(ei.value) and "SECRET" not in str(ei.value)
    assert "resource_changes[0]" in str(ei.value)


def test_index_in_the_message_is_the_bad_entry():
    with pytest.raises(PlanError, match=r"resource_changes\[1\]: 'previous_address'"):
        parse_plan(moved_plan(("a.a", ..., ["update"]), ("a.b", "", ["no-op"])))


def test_previous_address_absent_means_not_a_move():
    (c,) = changes_of(("a.b", ..., ["update"]))
    assert c.previous_address is None and c.is_move is False


def test_previous_address_equal_to_address_is_not_a_move(capsys, tmp_path):
    (c,) = changes_of(("a.b", "a.b", ["no-op"]))
    assert c.previous_address is None and c.is_move is False
    assert scope([c], []) == []  # a plain no-op outside the allowlist is still ignored


def test_plain_noop_and_read_entries_are_still_ignored_by_scope():
    changes = changes_of(("x.noop", ..., ["no-op"]), ("data.x.read", ..., ["read"]))
    assert scope(changes, []) == []


def test_move_plus_plain_noop_only_the_move_is_reported():
    changes = changes_of(("x.noop", ..., ["no-op"]), ("a.new", "a.old", ["no-op"]))
    assert [(v.address, v.previous_address) for v in scope(changes, ["a.new"])] == [("a.new", "a.old")]


def test_module_moves():
    changes = changes_of(("module.app.aws_s3_bucket.b", "aws_s3_bucket.b", ["no-op"]))
    assert scope(changes, ["module.app.*", "aws_s3_bucket.b"]) == []
    (v,) = scope(changes, ["module.app.*"])
    assert (v.address, v.previous_address) == ("module.app.aws_s3_bucket.b", "aws_s3_bucket.b")
    (v,) = scope(changes, ["aws_s3_bucket.b"])
    assert v.previous_address == "aws_s3_bucket.b"


def test_count_and_for_each_bracket_addresses():
    changes = changes_of(('aws_x.y["k"]', "aws_x.y[0]", ["no-op"]))
    assert scope(changes, ["aws_x.y[*]"]) == []  # [ is literal, * spans both
    assert scope(changes, ['aws_x.y["k"]', "aws_x.y[0]"]) == []
    assert len(scope(changes, ['aws_x.y["k"]'])) == 1
    assert len(scope(changes, ["aws_x.y[0]"])) == 1
    assert len(scope(changes, ["aws_x.y0", 'aws_x.y"k"']))  # brackets are not optional


def test_move_combined_with_deposed(capsys, tmp_path):
    cfg = write_cfg(tmp_path, "terraform", ["scope"], ["a.new"])
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps(moved_plan(("a.new", "a.old", ["delete"], "0001"))), encoding="utf-8")
    code, out, _ = run(capsys, plan, cfg)
    assert code == 1
    assert out.splitlines()[0] == "[error] scope a.new (t) delete deposed=0001 moved-from=a.old"
    code, out, _ = run(capsys, plan, cfg, "json")
    (v,) = json.loads(out)["violations"]
    assert (v["deposed"], v["previous_address"]) == ("0001", "a.old")


def test_previous_address_is_escaped_in_text_output(capsys, tmp_path):
    cfg = write_cfg(tmp_path, "terraform", ["scope"], ["a.new"])
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps(moved_plan(("a.new", "a.old\x1b[31mé", ["no-op"]))), encoding="utf-8")
    code, out, _ = run(capsys, plan, cfg)
    assert code == 1
    out.encode("ascii")
    assert "\x1b" not in out and "moved-from=a.old\\x1b[31m\\xe9" in out


def test_output_order_is_deterministic_and_includes_previous_address():
    forward = changes_of(("a.new", "a.old1", ["no-op"]), ("a.new", "a.old2", ["no-op"]), ("b.x", "b.y", ["no-op"]))
    for changes in (forward, tuple(reversed(forward)), forward[1:] + forward[:1]):
        vs = scope(changes, [])
        assert [(v.address, v.previous_address) for v in vs] == [
            ("a.new", "a.old1"), ("a.new", "a.old2"), ("b.x", "b.y")]


def test_violation_sort_key_uses_previous_address_as_a_tiebreaker():
    """Direct call with unsorted Change objects: the key (not input order) decides."""
    b = Change("a.new", "t", ("no-op",), None, "a.old2")
    a = Change("a.new", "t", ("no-op",), None, "a.old1")
    assert [v.previous_address for v in scope([b, a], [])] == ["a.old1", "a.old2"]
    assert Violation("scope", "error", "a.new", "t", "no-op", previous_address="a.old1").sort_key < \
        Violation("scope", "error", "a.new", "t", "no-op", previous_address="a.old2").sort_key


def test_parser_orders_moves_by_previous_address_when_addresses_tie():
    plan = parse_plan(moved_plan(("a.new", "a.old2", ["no-op"]), ("a.new", "a.old1", ["no-op"])))
    assert [c.previous_address for c in plan.changes] == ["a.old1", "a.old2"]
