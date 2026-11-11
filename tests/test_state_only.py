"""state_only_plans = "allow" (default) | "refuse".

In "refuse" mode a plan is refused (exit 2) when it has ZERO changes in resource_changes (no entry other
than no-op / read; moves are not counted as changes) AND at least one of:
  * an output_changes entry whose actions are not exactly ["no-op"]
  * a non-empty resource_drift
  * at least one moved entry
Plans that also change resources are never refused by this key.

Real plans (Terraform 1.17.2 / OpenTofu 1.13.9): tests/fixtures/{outputs,modes,moved}. Real
resource_drift cannot be produced offline: tests/fixtures/handmade/resource_drift.json is HAND-MADE.
"""

import json
import re
from pathlib import Path

import pytest

from planfence import cli
from planfence.config import ConfigError, load_config, parse_config
from planfence.parser import PlanError, load_plan, parse_plan
from tests.conftest import make_plan

FIX = Path(__file__).resolve().parent / "fixtures"
TOOLS = ["terraform", "opentofu"]  # directory names, also the matching plan_source
SECRET = "S3CR3T-NAME"


def write_cfg(tmp_path, plan_source, state_only, rules='["no-delete"]'):
    lines = [f'plan_source = "{plan_source}"', f"enabled_rules = {rules}"]
    if state_only is not None:
        lines.insert(1, f'state_only_plans = "{state_only}"')
    p = tmp_path / "planfence.toml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def exit_code(capsys, path, cfg):
    code = cli.main([str(path), "--config", str(cfg)])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- real fixtures -------------------------------------------------------------------------------

REAL_EXPECTATIONS = [
    # (directory, scenario, exit in allow mode, exit in refuse mode)
    ("outputs", "output_unchanged", 0, 0),                # output_changes {o: [no-op]}
    ("outputs", "output_changes_with_resource", 0, 0),    # the resource changes too
    ("outputs", "output_added_only", 0, 2),               # resource no-op, output create
    ("modes", "refresh_only", 0, 0),                      # nothing at all
    ("modes", "refresh_only_output_drift", 0, 2),         # real drift: only output_changes
    ("moved", "moved_only", 0, 2),                        # pure move
    ("moved", "moved_into_module", 0, 2),                 # pure move
    ("moved", "moved_update", 0, 0),                      # move + update: a real change
    ("modes", "replace_flag", 1, 1),                      # unaffected (no-delete... replace is not a delete)
]


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("directory, name, allow_exit, refuse_exit", REAL_EXPECTATIONS)
def test_real_plans(capsys, tmp_path, tool, directory, name, allow_exit, refuse_exit):
    if name == "replace_flag":
        rules, allow_exit, refuse_exit = '["no-replace"]', 1, 1
    else:
        rules = '["no-delete"]'
    path = FIX / directory / tool / f"{name}.json"
    assert exit_code(capsys, path, write_cfg(tmp_path, tool, "allow", rules))[0] == allow_exit
    assert exit_code(capsys, path, write_cfg(tmp_path, tool, None, rules))[0] == allow_exit  # default == allow
    assert exit_code(capsys, path, write_cfg(tmp_path, tool, "refuse", rules))[0] == refuse_exit


def test_terraform_fixtures_behave_the_same_under_plan_source_opentofu(capsys, tmp_path):
    for directory, name, allow_exit, refuse_exit in REAL_EXPECTATIONS:
        if name == "replace_flag":
            continue
        path = FIX / directory / "terraform" / f"{name}.json"
        assert exit_code(capsys, path, write_cfg(tmp_path, "opentofu", "allow"))[0] == allow_exit, name
        assert exit_code(capsys, path, write_cfg(tmp_path, "opentofu", "refuse"))[0] == refuse_exit, name


def test_the_verified_refresh_only_gap_is_closed_in_refuse_mode(capsys, tmp_path):
    path = FIX / "modes" / "terraform" / "refresh_only_output_drift.json"
    code, out, err = exit_code(capsys, path, write_cfg(tmp_path, "terraform", "refuse"))
    assert code == 2 and "OK" not in out
    assert "output_changes" in err and "state_only_plans" in err
    assert exit_code(capsys, path, write_cfg(tmp_path, "terraform", "allow"))[0] == 0


@pytest.mark.parametrize("tool", TOOLS)
def test_refuse_mode_never_changes_the_result_for_plans_with_changes(capsys, tmp_path, tool):
    """Every real fixture that has a change (or is refused anyway) gives the same exit code in both modes."""
    state_only = {"output_added_only", "refresh_only_output_drift", "moved_only", "moved_into_module"}
    checked = 0
    for directory in ("real", "opentofu", "outputs", "modes", "moved", "partial"):
        d = FIX / directory / tool if directory in ("outputs", "modes", "moved", "partial") else FIX / directory
        if directory == "real" and tool != "terraform" or directory == "opentofu" and tool != "opentofu":
            continue
        for path in sorted(d.glob("*.json")):
            if path.stem in state_only:
                continue
            a = exit_code(capsys, path, write_cfg(tmp_path, tool, "allow"))[0]
            r = exit_code(capsys, path, write_cfg(tmp_path, tool, "refuse"))[0]
            assert a == r, path
            checked += 1
    assert checked > 20


# ---- hand-made resource_drift ---------------------------------------------------------------------

def test_handmade_resource_drift_fixture(capsys, tmp_path):
    path = FIX / "handmade" / "resource_drift.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["resource_changes"][0]["change"]["actions"] == ["no-op"] and doc["resource_drift"]
    assert exit_code(capsys, path, write_cfg(tmp_path, "terraform", "allow"))[0] == 0
    code, _, err = exit_code(capsys, path, write_cfg(tmp_path, "terraform", "refuse"))
    assert code == 2 and "resource_drift" in err


def test_drift_together_with_a_real_change_is_not_refused():
    doc = json.loads((FIX / "handmade" / "resource_drift.json").read_text(encoding="utf-8"))
    doc["resource_changes"][0]["change"]["actions"] = ["update"]
    assert len(parse_plan(doc, "terraform", "refuse").changes) == 1


# ---- the rule, hand-built ---------------------------------------------------------------------------

def plan(*changes, **top):
    doc = make_plan(*changes)
    doc.update(top)
    return doc


def refused(doc, plan_source="terraform"):
    try:
        parse_plan(doc, plan_source, "refuse")
    except PlanError as exc:
        return str(exc)
    return None


NOOP_ENTRY = ("a.b", "t", ["no-op"])
READ_ENTRY = ("data.a.b", "t", ["read"])


@pytest.mark.parametrize("entries", [[], [NOOP_ENTRY], [READ_ENTRY], [NOOP_ENTRY, READ_ENTRY]], ids=repr)
@pytest.mark.parametrize("absent", [False, True])
class TestZeroChangePlans:
    def build(self, entries, absent, **top):
        doc = plan(*entries, **top)
        if absent and not entries:
            del doc["resource_changes"]
        return doc

    def test_nothing_else_is_fine(self, entries, absent):
        assert refused(self.build(entries, absent)) is None

    @pytest.mark.parametrize("oc", [{}, {"o": {"actions": ["no-op"]}}, {"o": {"actions": ["no-op"]}, "p": {"actions": ["no-op"]}}], ids=repr)
    def test_only_noop_outputs_are_fine(self, entries, absent, oc):
        assert refused(self.build(entries, absent, output_changes=oc)) is None

    @pytest.mark.parametrize("actions", [["create"], ["update"], ["delete"], ["read"], ["no-op", "update"], [], "no-op", None],
                             ids=repr)
    def test_any_other_output_action_is_refused(self, entries, absent, actions):
        msg = refused(self.build(entries, absent, output_changes={"o": {"actions": ["no-op"]}, "q": {"actions": actions}}))
        assert msg and "output_changes" in msg

    def test_missing_actions_and_non_object_entries_are_refused(self, entries, absent):
        assert refused(self.build(entries, absent, output_changes={"o": {}}))
        assert refused(self.build(entries, absent, output_changes={"o": "x"}))
        assert refused(self.build(entries, absent, output_changes={"o": None}))

    @pytest.mark.parametrize("oc", [None, [], "x", 0, [{"actions": ["update"]}]], ids=repr)
    def test_malformed_output_changes_is_refused(self, entries, absent, oc):
        assert "output_changes" in refused(self.build(entries, absent, output_changes=oc))

    def test_non_empty_resource_drift_is_refused(self, entries, absent):
        msg = refused(self.build(entries, absent, resource_drift=[{"address": "x.y"}]))
        assert msg and "resource_drift" in msg

    def test_empty_resource_drift_is_fine(self, entries, absent):
        assert refused(self.build(entries, absent, resource_drift=[])) is None

    @pytest.mark.parametrize("rd", [None, {}, "x", 0, {"a": 1}], ids=repr)
    def test_malformed_resource_drift_is_refused(self, entries, absent, rd):
        assert "resource_drift" in refused(self.build(entries, absent, resource_drift=rd))


@pytest.mark.parametrize("actions", [["create"], ["update"], ["delete"], ["forget"], ["delete", "create"], ["create", "delete"]])
def test_any_real_change_means_never_refused_by_this_key(actions):
    doc = plan(("a.b", "t", actions), output_changes={"o": {"actions": ["update"]}},
               resource_drift=[{"address": "x"}])
    assert refused(doc) is None
    # even malformed state sections are ignored once there is a change (today's behaviour)
    doc = plan(("a.b", "t", actions), output_changes=None, resource_drift="x")
    assert refused(doc) is None


def test_a_pure_move_is_state_only_but_a_move_with_a_change_is_not():
    pure = plan(("a.new", "t", ["no-op"]))
    pure["resource_changes"][0]["previous_address"] = "a.old"
    assert "moves" in refused(pure)
    moved_update = plan(("a.new", "t", ["update"]))
    moved_update["resource_changes"][0]["previous_address"] = "a.old"
    assert refused(moved_update) is None


def test_previous_address_equal_to_address_is_not_a_move_here_either():
    doc = plan(("a.b", "t", ["no-op"]))
    doc["resource_changes"][0]["previous_address"] = "a.b"
    assert refused(doc) is None


def test_a_move_next_to_other_noop_entries_is_still_refused():
    doc = plan(("x.y", "t", ["no-op"]), ("a.new", "t", ["no-op"]), ("data.q.r", "t", ["read"]))
    doc["resource_changes"][1]["previous_address"] = "a.old"
    assert refused(doc)


def test_allow_mode_ignores_everything_state_only():
    doc = plan(output_changes={"o": {"actions": ["delete"]}}, resource_drift=[{"x": 1}])
    assert len(parse_plan(doc, "terraform", "allow").changes) == 0
    assert len(parse_plan(doc).changes) == 0  # default is allow
    malformed = plan(output_changes=None, resource_drift="x")
    assert parse_plan(malformed).changes == ()


def test_messages_name_fields_only_never_values():
    doc = plan(output_changes={SECRET: {"actions": ["update"], "after": SECRET}})
    assert SECRET not in refused(doc)
    doc = plan(resource_drift=[{"address": SECRET, "change": {"after": SECRET}}])
    assert SECRET not in refused(doc)
    doc = plan(output_changes={SECRET: {"actions": [SECRET]}})
    assert SECRET not in refused(doc)


def test_refuse_mode_still_requires_everything_else():
    for bad in (plan(errored=True), plan(format_version="2.0"), plan(action_invocations=[{"x": 1}])):
        with pytest.raises(PlanError):
            parse_plan(bad, "terraform", "refuse")


def test_works_in_opentofu_mode_too():
    doc = plan(output_changes={"o": {"actions": ["create"]}})
    del doc["complete"]
    assert "output_changes" in refused(doc, "opentofu")
    assert parse_plan(doc, "opentofu", "allow").changes == ()


@pytest.mark.parametrize("mode", ["", "Refuse", "REFUSE", "deny", "allowed", " refuse", None, 1, True, ["refuse"]], ids=repr)
def test_unknown_mode_refused_by_parser_and_load_plan(mode):
    with pytest.raises(PlanError, match="unknown state_only_plans"):
        parse_plan(plan(), "terraform", mode)
    with pytest.raises(PlanError, match="unknown state_only_plans"):
        load_plan(FIX / "real" / "update.json", state_only_plans=mode)


# ---- config key -------------------------------------------------------------------------------------

def test_default_is_allow():
    assert parse_config({"enabled_rules": ["no-delete"]}).state_only_plans == "allow"


@pytest.mark.parametrize("value", ["allow", "refuse"])
def test_valid_values(value):
    assert parse_config({"enabled_rules": ["no-delete"], "state_only_plans": value}).state_only_plans == value


@pytest.mark.parametrize("value", ["deny", "Refuse", "REFUSE", "Allow", "", " refuse", "true", 1, 0, True, False,
                                   ["refuse"], {"a": 1}, 1.5, None], ids=repr)
def test_invalid_values_rejected_with_reason(value):
    with pytest.raises(ConfigError, match=re.escape('state_only_plans must be "allow" or "refuse"')):
        parse_config({"enabled_rules": ["no-delete"], "state_only_plans": value})


def test_key_must_be_top_level():
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({"enabled_rules": ["budget"], "budget": {"max_changes": 1, "state_only_plans": "refuse"}})


def test_independent_of_plan_source():
    cfg = parse_config({"enabled_rules": ["no-delete"], "plan_source": "opentofu", "state_only_plans": "refuse"})
    assert (cfg.plan_source, cfg.state_only_plans) == ("opentofu", "refuse")


def test_from_toml_file_and_bad_toml_value(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('enabled_rules = ["no-delete"]\nstate_only_plans = "refuse"\n', encoding="utf-8")
    assert load_config(p).state_only_plans == "refuse"
    p.write_text('enabled_rules = ["no-delete"]\nstate_only_plans = true\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="state_only_plans"):
        load_config(p)


def test_cli_invalid_value_exit_2(capsys, tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('enabled_rules = ["no-delete"]\nstate_only_plans = "deny"\n', encoding="utf-8")
    code, out, err = exit_code(capsys, FIX / "real" / "update.json", p)
    assert code == 2 and "state_only_plans" in err and "OK" not in out
