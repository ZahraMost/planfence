import copy
import json

import pytest

from planguard.parser import PlanError, load_plan, parse_plan
from tests.conftest import fixture_path, load_fixture, make_plan

REAL_EXPECTED = {
    "create": [("terraform_data.example", ("create",))],
    "update": [("terraform_data.example", ("update",))],
    "delete": [("terraform_data.example", ("delete",))],
    "replace": [("terraform_data.example", ("delete", "create"))],
    "replace_cbd": [("terraform_data.example", ("create", "delete"))],
    "noop": [("terraform_data.example", ("no-op",))],
    "forget": [("terraform_data.example", ("forget",))],
    "read": [
        ("data.terraform_remote_state.src", ("read",)),
        ("terraform_data.gate", ("create",)),
    ],
    "empty": [],
    "read_immediate": [],
    "module": [("module.child.terraform_data.inner", ("update",))],
}


@pytest.mark.parametrize("name", sorted(REAL_EXPECTED))
def test_real_fixtures_parse(name):
    plan = parse_plan(load_fixture(name))
    assert [(c.address, c.actions) for c in plan.changes] == REAL_EXPECTED[name]


def test_real_fixtures_via_file():
    assert load_plan(fixture_path("update")).changes[0].actions == ("update",)


def test_count_and_for_each_addresses_preserved():
    addrs = [c.address for c in parse_plan(load_fixture("count")).changes]
    assert addrs == [f"terraform_data.counted[{i}]" for i in range(3)]
    addrs = [c.address for c in parse_plan(load_fixture("for_each")).changes]
    assert addrs == ['terraform_data.keyed["a"]', 'terraform_data.keyed["b"]', 'terraform_data.keyed["c"]']


def test_absent_resource_changes_is_zero_changes():
    plan = make_plan()
    del plan["resource_changes"]
    assert parse_plan(plan).changes == ()


def test_empty_resource_changes_list_ok():
    assert parse_plan(make_plan()).changes == ()


def test_unknown_properties_ignored():
    plan = make_plan(("a.b", "a", ["create"]), brand_new_key={"x": 1})
    plan["resource_changes"][0]["future_field"] = 1
    plan["resource_changes"][0]["change"]["future"] = [1]
    assert len(parse_plan(plan).changes) == 1


def test_format_version_minor_accepted():
    assert parse_plan(make_plan(format_version="1.99")).changes == ()


def test_applyable_not_required():
    plan = make_plan()
    assert "applyable" not in plan
    parse_plan(plan)


def test_changes_sorted_by_address_then_deposed():
    plan = make_plan(
        ("b.x", "b", ["delete"]),
        ("a.x", "a", ["delete"], "0002"),
        ("a.x", "a", ["create"]),
        ("a.x", "a", ["delete"], "0001"),
    )
    got = [(c.address, c.deposed) for c in parse_plan(plan).changes]
    assert got == [("a.x", None), ("a.x", "0001"), ("a.x", "0002"), ("b.x", None)]


@pytest.mark.parametrize(
    "actions, is_change, is_delete, is_replace, is_create, is_forget",
    [
        (["no-op"], False, False, False, False, False),
        (["read"], False, False, False, False, False),
        (["create"], True, False, False, True, False),
        (["update"], True, False, False, False, False),
        (["delete"], True, True, False, False, False),
        (["forget"], True, False, False, False, True),
        (["delete", "create"], True, False, True, True, False),
        (["create", "delete"], True, False, True, True, False),
    ],
)
def test_change_semantics(actions, is_change, is_delete, is_replace, is_create, is_forget):
    (c,) = parse_plan(make_plan(("a.b", "a", actions))).changes
    assert (c.is_change, c.is_delete, c.is_replace, c.is_create, c.is_forget) == (
        is_change, is_delete, is_replace, is_create, is_forget
    )


def _mut(fn):
    plan = copy.deepcopy(make_plan(("a.b", "a", ["create"])))
    fn(plan)
    return plan


BAD_PLANS = {
    "top-level list": [],
    "top-level string": "x",
    "no format_version": _mut(lambda p: p.pop("format_version")),
    "format 2.0": _mut(lambda p: p.update(format_version="2.0")),
    "format 0.2": _mut(lambda p: p.update(format_version="0.2")),
    "format '1'": _mut(lambda p: p.update(format_version="1")),
    "format '1.2.3'": _mut(lambda p: p.update(format_version="1.2.3")),
    "format '1.'": _mut(lambda p: p.update(format_version="1.")),
    "format '1.x'": _mut(lambda p: p.update(format_version="1.x")),
    "format ' 1.2'": _mut(lambda p: p.update(format_version=" 1.2")),
    "format '1.2\\n'": _mut(lambda p: p.update(format_version="1.2\n")),
    "format unicode digit": _mut(lambda p: p.update(format_version="1.\u0663")),
    "format float": _mut(lambda p: p.update(format_version=1.2)),
    "no errored": _mut(lambda p: p.pop("errored")),
    "errored string": _mut(lambda p: p.update(errored="false")),
    "errored int 0": _mut(lambda p: p.update(errored=0)),
    "errored true": _mut(lambda p: p.update(errored=True)),
    "no complete": _mut(lambda p: p.pop("complete")),
    "complete string": _mut(lambda p: p.update(complete="true")),
    "complete int 1": _mut(lambda p: p.update(complete=1)),
    "complete false": _mut(lambda p: p.update(complete=False)),
    "resource_changes null": _mut(lambda p: p.update(resource_changes=None)),
    "resource_changes dict": _mut(lambda p: p.update(resource_changes={})),
    "item not dict": _mut(lambda p: p.update(resource_changes=["x"])),
    "no address": _mut(lambda p: p["resource_changes"][0].pop("address")),
    "empty address": _mut(lambda p: p["resource_changes"][0].update(address="")),
    "address int": _mut(lambda p: p["resource_changes"][0].update(address=1)),
    "no type": _mut(lambda p: p["resource_changes"][0].pop("type")),
    "empty type": _mut(lambda p: p["resource_changes"][0].update(type="")),
    "no change": _mut(lambda p: p["resource_changes"][0].pop("change")),
    "change list": _mut(lambda p: p["resource_changes"][0].update(change=[])),
    "no actions": _mut(lambda p: p["resource_changes"][0]["change"].pop("actions")),
    "actions empty": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=[])),
    "actions string": _mut(lambda p: p["resource_changes"][0]["change"].update(actions="create")),
    "actions null": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=None)),
    "actions unknown": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["destroy"])),
    "actions case": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["Create"])),
    "actions non-str": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=[1])),
    "actions dup": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["create", "create"])),
    "actions update+delete": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["update", "delete"])),
    "actions noop+create": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["no-op", "create"])),
    "actions forget+create": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["forget", "create"])),
    "actions triple": _mut(lambda p: p["resource_changes"][0]["change"].update(actions=["delete", "create", "delete"])),
    "deposed int": _mut(lambda p: p["resource_changes"][0].update(deposed=5)),
    "deposed null": _mut(lambda p: p["resource_changes"][0].update(deposed=None)),
    "deposed empty": _mut(lambda p: p["resource_changes"][0].update(deposed="")),
    # a `terraform show -json` of STATE: has format_version but no errored/complete
    "state-like json": {"format_version": "1.0", "terraform_version": "1.17.2", "values": {}},
}


@pytest.mark.parametrize("name", sorted(BAD_PLANS))
def test_bad_plans_fail_closed(name):
    with pytest.raises(PlanError):
        parse_plan(BAD_PLANS[name])


def test_one_bad_item_among_good_fails_whole_plan():
    plan = make_plan(("a.b", "a", ["create"]), ("c.d", "c", ["bogus"]))
    with pytest.raises(PlanError):
        parse_plan(plan)


def test_error_messages_do_not_contain_attribute_values():
    secret = "S3CR3T-VALUE"
    plan = make_plan(("a.b", "a", ["create"]))
    plan["resource_changes"][0]["change"]["after"] = {"password": secret}
    plan["resource_changes"][0]["change"]["actions"] = [secret]
    with pytest.raises(PlanError) as ei:
        parse_plan(plan)
    assert secret not in str(ei.value)


# ---- file loading ----------------------------------------------------------

def _write(tmp_path, text, name="plan.json", mode="w"):
    p = tmp_path / name
    if mode == "w":
        p.write_text(text, encoding="utf-8")
    else:
        p.write_bytes(text)
    return p


def test_missing_file(tmp_path):
    with pytest.raises(PlanError):
        load_plan(tmp_path / "nope.json")


def test_directory_is_not_a_plan(tmp_path):
    with pytest.raises(PlanError):
        load_plan(tmp_path)


@pytest.mark.parametrize("text", ["", "   ", "{", "not json", "[1,2", "null", "{'a': 1}"])
def test_invalid_json(tmp_path, text):
    with pytest.raises(PlanError):
        load_plan(_write(tmp_path, text))


def test_invalid_utf8(tmp_path):
    with pytest.raises(PlanError):
        load_plan(_write(tmp_path, b"\xff\xfe\xfa\xfb", mode="wb"))


def test_size_limit(tmp_path):
    p = _write(tmp_path, json.dumps(make_plan()))
    assert load_plan(p).changes == ()
    with pytest.raises(PlanError):
        load_plan(p, max_bytes=10)


def test_size_limit_exact_boundary(tmp_path):
    text = json.dumps(make_plan())
    p = _write(tmp_path, text)
    n = len(text.encode())
    assert load_plan(p, max_bytes=n).changes == ()
    with pytest.raises(PlanError):
        load_plan(p, max_bytes=n - 1)


def test_deeply_nested_json_fails_closed(tmp_path):
    with pytest.raises(PlanError):
        load_plan(_write(tmp_path, "[" * 200000))


def test_duplicate_keys_rejected(tmp_path):
    text = '{"format_version":"1.2","errored":false,"errored":true,"complete":true}'
    with pytest.raises(PlanError):
        load_plan(_write(tmp_path, text))


@pytest.mark.parametrize(
    "text",
    [
        # the LAST value is harmless, so only an explicit duplicate check can refuse these
        '{"format_version":"1.2","errored":true,"errored":false,"complete":true}',
        '{"format_version":"1.2","errored":false,"complete":false,"complete":true}',
        '{"format_version":"1.2","errored":false,"complete":true,"resource_changes":[],"resource_changes":[]}',
        '{"format_version":"1.2","errored":false,"complete":true,"resource_changes":[{"address":"a.b","address":"a.b",'
        '"type":"a","change":{"actions":["create"]}}]}',
        '{"format_version":"1.2","errored":false,"complete":true,"resource_changes":[{"address":"a.b","type":"a",'
        '"change":{"actions":["delete"],"actions":["no-op"]}}]}',
    ],
)
def test_duplicate_keys_are_refused_even_when_the_last_value_would_be_fine(tmp_path, text):
    with pytest.raises(PlanError, match="duplicate key"):
        load_plan(_write(tmp_path, text))
