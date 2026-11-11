"""plan_source = "terraform" (strict, default) | "opentofu" (no `complete` / `applyable` in the JSON)."""

import json
import re
from pathlib import Path

import pytest

from planguard import cli
from planguard.config import ConfigError, load_config, parse_config
from planguard.parser import PlanError, load_plan, parse_plan
from tests.test_parser import BAD_PLANS
from tests.conftest import make_plan

FIX = Path(__file__).resolve().parent / "fixtures"
TOFU_DIR, TF_DIR = FIX / "opentofu", FIX / "real"
NAMES = sorted(p.stem for p in TOFU_DIR.glob("*.json"))
IMPORT_NAMES = ["import", "import_noop", "import_replace"]
OK_NAMES = [n for n in NAMES if n not in IMPORT_NAMES]


def tofu(name):
    return json.loads((TOFU_DIR / f"{name}.json").read_text(encoding="utf-8"))


def tf(name):
    return json.loads((TF_DIR / f"{name}.json").read_text(encoding="utf-8"))


def summary(plan):
    return [(c.address, c.actions, c.deposed) for c in plan.changes]


def refusal(data, mode):
    with pytest.raises(PlanError) as ei:
        parse_plan(data, mode)
    return str(ei.value)


# ---- the fixture sets --------------------------------------------------------

def test_there_are_16_opentofu_fixtures_matching_the_terraform_names():
    assert len(NAMES) == 16
    assert NAMES == sorted(p.stem for p in TF_DIR.glob("*.json"))


def test_opentofu_fixtures_really_lack_complete_and_applyable():
    for name in NAMES:
        data = tofu(name)
        assert "complete" not in data and "applyable" not in data, name
        assert data["errored"] is False and data["format_version"] == "1.2"
        assert "complete" in tf(name) and "applyable" in tf(name)


@pytest.mark.parametrize("name", OK_NAMES)
def test_opentofu_fixtures_parse_like_terraform_ones(name):
    assert summary(parse_plan(tofu(name), "opentofu")) == summary(parse_plan(tf(name), "terraform"))


@pytest.mark.parametrize("name", OK_NAMES)
def test_terraform_fixtures_also_accepted_in_opentofu_mode(name):
    assert summary(parse_plan(tf(name), "opentofu")) == summary(parse_plan(tf(name), "terraform"))


@pytest.mark.parametrize("name", IMPORT_NAMES)
def test_import_fixtures_refused_in_both_modes_for_the_same_reason(name):
    tofu_msg = refusal(tofu(name), "opentofu")
    tf_msg = refusal(tf(name), "terraform")
    assert "importing" in tofu_msg
    assert tofu_msg == tf_msg == refusal(tf(name), "opentofu")


@pytest.mark.parametrize("name", NAMES)
def test_opentofu_fixtures_refused_in_default_terraform_mode(name):
    for call in (lambda d: parse_plan(d), lambda d: parse_plan(d, "terraform")):
        with pytest.raises(PlanError, match="'errored' and 'complete' must be present booleans"):
            call(tofu(name))


def test_error_message_hints_at_plan_source():
    assert "plan_source" in refusal(tofu("create"), "terraform")


def test_load_plan_takes_the_mode_explicitly():
    path = TOFU_DIR / "update.json"
    assert load_plan(path, plan_source="opentofu").changes[0].actions == ("update",)
    with pytest.raises(PlanError):
        load_plan(path)  # default mode is strict


# ---- opentofu mode: what stays strict ---------------------------------------

def plan(**changes):
    base = tofu("update")
    for key, value in changes.items():
        if value is ...:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def test_complete_true_present_is_accepted():
    assert len(parse_plan(plan(complete=True), "opentofu").changes) == 1


@pytest.mark.parametrize("value", [False, 0, 1, "true", "false", None, [], {}, 1.0], ids=repr)
def test_complete_present_must_be_boolean_true(value):
    msg = refusal(plan(complete=value), "opentofu")
    assert "complete" in msg
    if value is False:
        assert "not marked complete" in msg
    else:
        assert "must be a boolean" in msg


@pytest.mark.parametrize("value", [None, "x", 0, [], {"a": 1}, True, False], ids=repr)
def test_applyable_is_ignored(value):
    assert len(parse_plan(plan(applyable=value), "opentofu").changes) == 1


def test_errored_missing_refused():
    assert "'errored' must be a present boolean" in refusal(plan(errored=...), "opentofu")


@pytest.mark.parametrize("value", ["false", 0, 1, None, [], {}], ids=repr)
def test_errored_must_be_boolean(value):
    assert "'errored' must be a present boolean" in refusal(plan(errored=value), "opentofu")


def test_errored_true_refused():
    assert "marked errored" in refusal(plan(errored=True), "opentofu")


def test_errored_true_refused_even_when_complete_absent():
    data = plan(errored=True, complete=...)
    assert "marked errored" in refusal(data, "opentofu")


def test_state_like_json_refused_in_both_modes():
    state = {"format_version": "1.0", "terraform_version": "1.13.9", "values": {"root_module": {}}}
    assert "'errored'" in refusal(state, "opentofu")
    assert "'errored'" in refusal(state, "terraform")


@pytest.mark.parametrize("name", sorted(set(BAD_PLANS) - {"no complete"}))
def test_every_other_bad_plan_is_still_refused_in_opentofu_mode(name):
    with pytest.raises(PlanError):
        parse_plan(BAD_PLANS[name], "opentofu")


def test_only_missing_complete_differs_between_modes():
    data = make_plan(("a.b", "a", ["create"]))
    del data["complete"]
    assert len(parse_plan(data, "opentofu").changes) == 1
    assert "'errored' and 'complete'" in refusal(data, "terraform")


@pytest.mark.parametrize("mode", ["", "Terraform", "OpenTofu", "tofu", "TERRAFORM", " opentofu", None, 1, True, ["terraform"]], ids=repr)
def test_unknown_mode_refused(mode):
    with pytest.raises(PlanError, match="unknown plan_source"):
        parse_plan(make_plan(), mode)
    with pytest.raises(PlanError, match="unknown plan_source"):
        load_plan(TF_DIR / "update.json", plan_source=mode)


def test_other_checks_still_apply_in_opentofu_mode():
    data = plan(action_invocations=[{"x": 1}])
    assert "action_invocations" in refusal(data, "opentofu")
    data = plan(deferred_changes=[{"x": 1}])
    assert "deferred_changes" in refusal(data, "opentofu")
    assert "format_version" in refusal(plan(format_version="2.0"), "opentofu")


# ---- documented limitation: OpenTofu partial plans are NOT recognisable -----

def test_opentofu_partial_plans_have_no_signal_and_are_accepted():
    """-target / -exclude plans from OpenTofu look exactly like complete plans (see
    tests/fixtures/README.md). planguard cannot refuse them; documented limitation."""
    for name in ("two_target", "two_exclude"):
        data = json.loads((FIX / "partial" / "opentofu" / f"{name}.json").read_text(encoding="utf-8"))
        assert "complete" not in data and "applyable" not in data
        assert len(parse_plan(data, "opentofu").changes) == 1  # only the targeted resource is visible
    full = json.loads((FIX / "partial" / "opentofu" / "two_full.json").read_text(encoding="utf-8"))
    assert len(parse_plan(full, "opentofu").changes) == 2


def test_terraform_partial_plan_is_refused_via_complete_false():
    data = json.loads((FIX / "partial" / "terraform" / "two_target.json").read_text(encoding="utf-8"))
    assert data["complete"] is False
    assert "not marked complete" in refusal(data, "terraform")
    assert "not marked complete" in refusal(data, "opentofu")  # a present complete=false is always refused
    full = json.loads((FIX / "partial" / "terraform" / "two_full.json").read_text(encoding="utf-8"))
    assert len(parse_plan(full, "terraform").changes) == 2


# ---- config key ---------------------------------------------------------------

def test_default_plan_source_is_terraform():
    assert parse_config({"enabled_rules": ["no-delete"]}).plan_source == "terraform"


@pytest.mark.parametrize("value", ["terraform", "opentofu"])
def test_valid_plan_sources(value):
    assert parse_config({"enabled_rules": ["no-delete"], "plan_source": value}).plan_source == value


@pytest.mark.parametrize("value", ["tofu", "OpenTofu", "Terraform", "", " terraform", 1, 0, True, False, ["opentofu"], {"a": 1}, 1.5, None], ids=repr)
def test_invalid_plan_source_rejected_with_reason(value):
    with pytest.raises(ConfigError, match=re.escape('plan_source must be "terraform" or "opentofu"')):
        parse_config({"enabled_rules": ["no-delete"], "plan_source": value})


def test_plan_source_must_be_top_level():
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({"enabled_rules": ["scope"], "scope": {"allow": ["a"], "plan_source": "opentofu"}})


def test_plan_source_from_toml_file(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('enabled_rules = ["no-delete"]\nplan_source = "opentofu"\n', encoding="utf-8")
    assert load_config(p).plan_source == "opentofu"
    p.write_text('enabled_rules = ["no-delete"]\nplan_source = 5\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="plan_source"):
        load_config(p)


# ---- CLI end to end -----------------------------------------------------------

CONTRACT = """\
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]
{extra}
[scope]
allow = ["terraform_data.*", "module.child.*"]
[stateful]
types = ["terraform_data"]
[budget]
max_changes = 5
[budget.max_creates_per_type]
terraform_data = 3
"""


def write_cfg(tmp_path, name, extra=""):
    p = tmp_path / name
    p.write_text(CONTRACT.format(extra=extra), encoding="utf-8")
    return p


@pytest.mark.parametrize("fmt", ["text", "json"])
@pytest.mark.parametrize("name", NAMES)
def test_cli_opentofu_matches_terraform_with_same_contract(capsys, tmp_path, name, fmt):
    tofu_cfg = write_cfg(tmp_path, "tofu.toml", 'plan_source = "opentofu"')
    tf_cfg = write_cfg(tmp_path, "tf.toml")
    a = cli.main([str(TOFU_DIR / f"{name}.json"), "--config", str(tofu_cfg), "--format", fmt])
    out_a = capsys.readouterr()
    b = cli.main([str(TF_DIR / f"{name}.json"), "--config", str(tf_cfg), "--format", fmt])
    out_b = capsys.readouterr()
    assert a == b
    assert out_a.out == out_b.out and out_a.err == out_b.err
    assert a == (2 if name in IMPORT_NAMES else {"delete": 1, "replace": 1, "replace_cbd": 1, "forget": 1,
                                                  "count": 1, "for_each": 1}.get(name, 0))


@pytest.mark.parametrize("name", NAMES)
def test_cli_opentofu_file_in_default_mode_is_exit_2(capsys, tmp_path, name):
    cfg = write_cfg(tmp_path, "tf.toml")
    code = cli.main([str(TOFU_DIR / f"{name}.json"), "--config", str(cfg)])
    out = capsys.readouterr()
    assert code == 2 and "OK" not in out.out
    assert "plan_source" in out.err or name in IMPORT_NAMES


def test_cli_terraform_file_accepted_with_opentofu_config(capsys, tmp_path):
    cfg = write_cfg(tmp_path, "tofu.toml", 'plan_source = "opentofu"')
    assert cli.main([str(TF_DIR / "update.json"), "--config", str(cfg)]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_invalid_plan_source_exit_2(capsys, tmp_path):
    cfg = write_cfg(tmp_path, "bad.toml", 'plan_source = "tofu"')
    assert cli.main([str(TOFU_DIR / "update.json"), "--config", str(cfg)]) == 2
    assert "plan_source" in capsys.readouterr().err
