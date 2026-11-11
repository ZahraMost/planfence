"""Plan content outside resource_changes that can still act at apply: fail closed (exit 2)."""

import copy
import json
from pathlib import Path

import pytest

from planguard import cli
from planguard.parser import PlanError, load_plan, parse_plan
from tests.conftest import fixture_path, load_fixture, make_plan

HANDMADE = Path(__file__).resolve().parent / "fixtures" / "handmade"
SECRET = "S3CR3T-IMPORT-ID"

NONEMPTY_OR_WRONG = [[{"address": "x"}], [[]], [None], {}, {"a": 1}, None, "x", "", 0, 1, False, True]


def plan_with(**top):
    return make_plan(("a.b", "a", ["create"]), **top)


# ---- top-level action_invocations / deferred_changes -------------------------

@pytest.mark.parametrize("key", ["action_invocations", "deferred_changes"])
class TestTopLevelKeys:
    def test_absent_ok(self, key):
        assert key not in plan_with()
        parse_plan(plan_with())

    def test_empty_list_ok(self, key):
        assert len(parse_plan(plan_with(**{key: []})).changes) == 1

    @pytest.mark.parametrize("value", NONEMPTY_OR_WRONG, ids=repr)
    def test_anything_else_rejected(self, key, value):
        with pytest.raises(PlanError) as ei:
            parse_plan(plan_with(**{key: value}))
        assert key in str(ei.value)

    def test_rejected_even_with_no_resource_changes(self, key):
        plan = plan_with(**{key: [{"x": 1}]})
        del plan["resource_changes"]
        with pytest.raises(PlanError):
            parse_plan(plan)

    def test_message_names_field_only(self, key):
        with pytest.raises(PlanError) as ei:
            parse_plan(plan_with(**{key: [{"password": SECRET}]}))
        assert SECRET not in str(ei.value) and "password" not in str(ei.value)


# ---- change.importing ---------------------------------------------------------

def with_importing(value, present=True):
    plan = plan_with()
    if present:
        plan["resource_changes"][0]["change"]["importing"] = value
    return plan


def test_importing_absent_ok():
    parse_plan(with_importing(None, present=False))


def test_importing_null_ok():
    assert len(parse_plan(with_importing(None)).changes) == 1


@pytest.mark.parametrize("value", [{"id": "x"}, {}, "x", "", 0, False, True, [], [{"id": "x"}]], ids=repr)
def test_non_null_importing_rejected(value):
    with pytest.raises(PlanError) as ei:
        parse_plan(with_importing(value))
    assert "importing" in str(ei.value)


def test_importing_on_noop_entry_still_rejected():
    plan = make_plan(("a.b", "a", ["no-op"]))
    plan["resource_changes"][0]["change"]["importing"] = {"id": "x"}
    with pytest.raises(PlanError):
        parse_plan(plan)


def test_importing_on_read_entry_rejected_too():
    plan = make_plan(("data.a.b", "a", ["read"]))
    plan["resource_changes"][0]["change"]["importing"] = {"id": "x"}
    with pytest.raises(PlanError):
        parse_plan(plan)


def test_one_importing_entry_among_many_rejects_whole_plan():
    plan = make_plan(("a.a", "a", ["update"]), ("a.b", "a", ["update"]), ("a.c", "a", ["update"]))
    plan["resource_changes"][2]["change"]["importing"] = {"id": SECRET}
    with pytest.raises(PlanError) as ei:
        parse_plan(plan)
    assert SECRET not in str(ei.value) and "[2]" in str(ei.value)


def test_importing_id_value_never_in_message():
    with pytest.raises(PlanError) as ei:
        parse_plan(with_importing({"id": SECRET}))
    assert SECRET not in str(ei.value)


# ---- real fixtures (import) and handmade fixtures ----------------------------

@pytest.mark.parametrize("name, actions", [("import", ["update"]), ("import_replace", ["delete", "create"]), ("import_noop", ["no-op"])])
def test_real_import_fixtures_are_rejected(name, actions):
    data = load_fixture(name)
    (rc,) = data["resource_changes"]
    assert rc["change"]["actions"] == actions
    assert rc["change"]["importing"]["id"]  # fixture really contains an import
    with pytest.raises(PlanError):
        parse_plan(data)


@pytest.mark.parametrize("name", ["action_invocations", "deferred_changes"])
def test_handmade_fixtures_are_rejected(name):
    with pytest.raises(PlanError):
        load_plan(HANDMADE / f"{name}.json")


@pytest.mark.parametrize("name", ["import", "import_replace", "import_noop"])
def test_existing_real_fixtures_without_import_unaffected(name):
    # sanity: the same scenario minus `importing` parses fine
    data = copy.deepcopy(load_fixture(name))
    del data["resource_changes"][0]["change"]["importing"]
    assert len(parse_plan(data).changes) == 1


# ---- CLI: exit 2, never OK, no values -----------------------------------------

CFG = 'enabled_rules = ["no-delete"]\n'


@pytest.mark.parametrize("fmt", ["text", "json"])
@pytest.mark.parametrize(
    "path",
    [fixture_path("import"), fixture_path("import_replace"), fixture_path("import_noop"),
     HANDMADE / "action_invocations.json", HANDMADE / "deferred_changes.json"],
    ids=lambda p: p.stem,
)
def test_cli_exit_2(capsys, tmp_path, path, fmt):
    cfg = tmp_path / "planguard.toml"
    cfg.write_text(CFG, encoding="utf-8")
    code = cli.main([str(path), "--config", str(cfg), "--format", fmt])
    out = capsys.readouterr()
    assert code == 2
    assert "OK" not in out.out
    assert "11111111-2222-3333-4444-555555555555" not in out.out + out.err
    if fmt == "json":
        assert json.loads(out.out)["result"] == "error"
