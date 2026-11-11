import pytest

from planguard.parser import parse_plan
from planguard.rules import Violation, budget
from tests.conftest import load_fixture, make_plan


def changes(name):
    return parse_plan(load_fixture(name)).changes


TOTAL = Violation("budget", "error", None, None, None)


def per_type(t):
    return Violation("budget", "error", None, t, "create")


def test_total_over_limit_fails():
    # for_each fixture: update + delete + create = 3 changes
    assert budget(changes("for_each"), max_changes=2, max_creates_per_type={}) == [TOTAL]


def test_total_at_limit_passes():
    assert budget(changes("for_each"), max_changes=3, max_creates_per_type={}) == []


def test_noop_instances_not_counted():
    # count fixture: two no-ops + one delete = 1 change
    assert budget(changes("count"), max_changes=1, max_creates_per_type={}) == []
    assert budget(changes("count"), max_changes=0, max_creates_per_type={}) == [TOTAL]


def test_read_not_counted():
    plan = parse_plan(make_plan(("data.x.y", "x", ["read"]), ("data.x.z", "x", ["no-op"])))
    assert budget(plan.changes, max_changes=0, max_creates_per_type={"x": 0}) == []


def test_read_fixture_counts_only_the_create():
    assert budget(changes("read"), max_changes=1, max_creates_per_type={}) == []
    assert budget(changes("read"), max_changes=0, max_creates_per_type={}) == [TOTAL]


def test_forget_counts_as_a_change():
    assert budget(changes("forget"), max_changes=0, max_creates_per_type={}) == [TOTAL]


def test_replacement_is_one_change_and_one_create():
    assert budget(changes("replace"), max_changes=1, max_creates_per_type={"terraform_data": 1}) == []
    assert budget(changes("replace_cbd"), max_changes=0, max_creates_per_type={}) == [TOTAL]
    assert budget(changes("replace"), max_changes=None, max_creates_per_type={"terraform_data": 0}) == [
        per_type("terraform_data")
    ]


def test_creates_per_type_over_limit():
    plan = parse_plan(
        make_plan(
            ("aws_i.a", "aws_i", ["create"]),
            ("aws_i.b", "aws_i", ["create"]),
            ("aws_i.c", "aws_i", ["update"]),
            ("aws_s.a", "aws_s", ["create"]),
        )
    )
    assert budget(plan.changes, None, {"aws_i": 1}) == [per_type("aws_i")]
    assert budget(plan.changes, None, {"aws_i": 2, "aws_s": 1}) == []
    assert budget(plan.changes, None, {"aws_i": 0, "aws_s": 0}) == [per_type("aws_i"), per_type("aws_s")]


def test_unlisted_types_have_no_create_limit():
    plan = parse_plan(make_plan(("a.a", "a", ["create"]), ("a.b", "a", ["create"])))
    assert budget(plan.changes, None, {"other": 0}) == []


def test_both_limits_can_fail_together_sorted():
    vs = budget(changes("for_each"), max_changes=0, max_creates_per_type={"terraform_data": 0})
    assert vs == [TOTAL, per_type("terraform_data")]


@pytest.mark.parametrize("name", ["empty", "read_immediate", "noop"])
def test_zero_changes_passes_zero_budget(name):
    assert budget(changes(name), max_changes=0, max_creates_per_type={"terraform_data": 0}) == []


def test_no_limits_configured_means_no_violations():
    assert budget(changes("for_each"), None, {}) == []
