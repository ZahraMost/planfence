import pytest

from planguard.parser import parse_plan
from planguard.rules import Violation, no_delete, no_replace
from tests.conftest import load_fixture, make_plan


def changes(name):
    return parse_plan(load_fixture(name)).changes


# ---- no-delete -------------------------------------------------------------

def test_no_delete_fails_on_real_delete():
    vs = no_delete(changes("delete"))
    assert vs == [Violation("no-delete", "error", "terraform_data.example", "terraform_data", "delete")]


@pytest.mark.parametrize("name", ["create", "update", "noop", "replace", "replace_cbd", "forget", "read", "empty"])
def test_no_delete_passes(name):
    # replacements and forgets are NOT plain deletes
    assert no_delete(changes(name)) == []


def test_no_delete_count_and_for_each_report_each_instance():
    assert [v.address for v in no_delete(changes("count"))] == ["terraform_data.counted[2]"]
    assert [v.address for v in no_delete(changes("for_each"))] == ['terraform_data.keyed["b"]']


def test_no_delete_flags_deposed_delete():
    plan = parse_plan(make_plan(("a.b", "a", ["delete"], "0x1")))
    (v,) = no_delete(plan.changes)
    assert v.deposed == "0x1"


# ---- no-replace ------------------------------------------------------------

@pytest.mark.parametrize("name, label", [("replace", "delete,create"), ("replace_cbd", "create,delete")])
def test_no_replace_fails_on_real_replace(name, label):
    vs = no_replace(changes(name))
    assert vs == [Violation("no-replace", "error", "terraform_data.example", "terraform_data", label)]


@pytest.mark.parametrize("name", ["create", "update", "delete", "noop", "forget", "read", "empty", "count"])
def test_no_replace_passes(name):
    assert no_replace(changes(name)) == []


def test_results_are_sorted_by_address():
    plan = parse_plan(make_plan(("z.z", "z", ["delete"]), ("a.a", "a", ["delete"])))
    assert [v.address for v in no_delete(plan.changes)] == ["a.a", "z.z"]


def test_violation_has_no_attribute_fields():
    assert set(Violation.__dataclass_fields__) == {"rule", "severity", "address", "type", "action", "deposed"}
