"""Plan modes verified with REAL Terraform 1.17.2 and OpenTofu 1.13.9 output (tests/fixtures/modes).

  replace_flag               plan -replace=terraform_data.example on an unchanged config
  refresh_only               plan -refresh-only on an unchanged config
  refresh_only_output_drift  plan -refresh-only after the remote state a data source reads changed
                             (real drift, offline: terraform_remote_state + local state files)

Real drift of a MANAGED resource (`resource_drift`) cannot be produced offline with built-in
resources, so there is no fixture for it (and none was faked).
"""

import json
from pathlib import Path

import pytest

from planfence import cli
from planfence.config import parse_config
from planfence.parser import parse_plan
from planfence.rules import check

MODES = Path(__file__).resolve().parent / "fixtures" / "modes"
TOOLS = {"terraform": "terraform", "opentofu": "opentofu"}  # directory -> plan_source


def raw(tool, name):
    return json.loads((MODES / tool / f"{name}.json").read_text(encoding="utf-8"))


def plan(tool, name):
    return parse_plan(raw(tool, name), TOOLS[tool])


def contract(tool, rules=("no-delete", "no-replace", "scope", "stateful-protect", "budget")):
    cfg = {"enabled_rules": list(rules), "plan_source": TOOLS[tool]}
    if "scope" in rules:
        cfg["scope"] = {"allow": ["nothing.*"]}
    if "stateful-protect" in rules:
        cfg["stateful"] = {"types": ["terraform_data"]}
    if "budget" in rules:
        cfg["budget"] = {"max_changes": 0, "max_creates_per_type": {"terraform_data": 0}}
    return parse_config(cfg)


def strictest_toml(tool):
    prefix = 'plan_source = "opentofu"\n' if tool == "opentofu" else ""
    return (prefix + 'enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]\n'
            '[scope]\nallow = ["nothing.*"]\n[stateful]\ntypes = ["terraform_data"]\n'
            '[budget]\nmax_changes = 0\n[budget.max_creates_per_type]\nterraform_data = 0\n')


@pytest.fixture(params=sorted(TOOLS))
def tool(request):
    return request.param


# ---- -replace ---------------------------------------------------------------------------------

def test_replace_flag_is_a_delete_and_create_with_reason_replace_by_request(tool):
    doc = raw(tool, "replace_flag")
    (rc,) = doc["resource_changes"]
    assert rc["address"] == "terraform_data.example"
    assert rc["change"]["actions"] == ["delete", "create"]
    assert rc["action_reason"] == "replace_by_request"
    # a config-driven replacement (real/replace.json) has replace_paths + replace_because_cannot_update;
    # one forced by the flag has neither
    assert "replace_paths" not in rc["change"]
    (change,) = plan(tool, "replace_flag").changes
    assert change.is_replace and change.actions == ("delete", "create")


def test_replace_flag_trips_no_replace(tool):
    vs = check(plan(tool, "replace_flag").changes, contract(tool, ("no-replace",)))
    assert [(v.rule, v.address, v.action) for v in vs] == [("no-replace", "terraform_data.example", "delete,create")]


def test_replace_flag_is_invisible_when_no_replace_is_not_enabled(tool):
    """planfence cannot know about the flag: without no-replace (and with a permissive contract) it passes."""
    changes = plan(tool, "replace_flag").changes
    assert check(changes, contract(tool, ("no-delete",))) == []  # a replacement is not a plain delete
    assert [v.rule for v in check(changes, contract(tool, ("no-replace", "no-delete")))] == ["no-replace"]


def test_replace_flag_counts_as_one_change_and_one_create(tool):
    vs = check(plan(tool, "replace_flag").changes, contract(tool, ("budget",)))
    assert [(v.type, v.count, v.limit) for v in vs] == [(None, 1, 0), ("terraform_data", 1, 0)]


# ---- -refresh-only ----------------------------------------------------------------------------

def test_refresh_only_unchanged_config_has_no_resource_changes_key(tool):
    doc = raw(tool, "refresh_only")
    assert "resource_changes" not in doc and "resource_drift" not in doc and "output_changes" not in doc
    assert doc["errored"] is False
    if tool == "terraform":
        assert doc["complete"] is True and doc["applyable"] is False
    else:
        assert "complete" not in doc and "applyable" not in doc
    assert plan(tool, "refresh_only").changes == ()


def test_refresh_only_passes_even_the_strictest_contract(tool, tmp_path, capsys):
    cfg = tmp_path / "planfence.toml"
    cfg.write_text(strictest_toml(tool), encoding="utf-8")
    code = cli.main([str(MODES / tool / "refresh_only.json"), "--config", str(cfg)])
    assert code == 0 and "OK - 0 change(s) checked" in capsys.readouterr().out


# ---- -refresh-only with real output drift --------------------------------------------------------

def test_real_output_drift_is_only_in_output_changes(tool):
    doc = raw(tool, "refresh_only_output_drift")
    assert "resource_changes" not in doc and "resource_drift" not in doc
    out = doc["output_changes"]["greeting"]
    assert (out["actions"], out["before"], out["after"]) == (["update"], "hello", "changed")
    assert doc["errored"] is False
    assert plan(tool, "refresh_only_output_drift").changes == ()


def test_terraform_marks_the_output_drift_plan_applyable(tool):
    """Applying this plan would change the state (the output), yet there is nothing for planfence to judge."""
    doc = raw(tool, "refresh_only_output_drift")
    if tool == "terraform":
        assert doc["applyable"] is True and doc["complete"] is True
    else:
        assert "applyable" not in doc


def test_output_drift_plan_exits_0_with_the_strictest_contract(tool, tmp_path, capsys):
    """Documented limitation: state changes found by -refresh-only (output_changes) are not checked."""
    cfg = tmp_path / "planfence.toml"
    cfg.write_text(strictest_toml(tool), encoding="utf-8")
    code = cli.main([str(MODES / tool / "refresh_only_output_drift.json"), "--config", str(cfg)])
    assert code == 0 and "OK - 0 change(s) checked" in capsys.readouterr().out


# ---- both tools agree --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["replace_flag", "refresh_only", "refresh_only_output_drift"])
def test_terraform_and_opentofu_agree(name):
    a, b = raw("terraform", name), raw("opentofu", name)
    assert a["format_version"] == b["format_version"] == "1.2"
    assert ("resource_changes" in a) == ("resource_changes" in b)
    assert a.get("output_changes") == b.get("output_changes")
    ra = [(r["address"], r["change"]["actions"], r.get("action_reason")) for r in a.get("resource_changes", [])]
    rb = [(r["address"], r["change"]["actions"], r.get("action_reason")) for r in b.get("resource_changes", [])]
    assert ra == rb
