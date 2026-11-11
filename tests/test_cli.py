import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from planfence import cli
from tests.conftest import SRC, fixture_path, make_plan


FULL_TOML = """\
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]
[scope]
allow = ["terraform_data.*", "module.child.*"]
[stateful]
types = ["terraform_data"]
[budget]
max_changes = 5
[budget.max_creates_per_type]
terraform_data = 3
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "planfence.toml"
    p.write_text(FULL_TOML, encoding="utf-8")
    return p


def write_cfg(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text, encoding="utf-8")
    return p


def write_plan(tmp_path, plan, name="p.json"):
    p = tmp_path / name
    p.write_text(json.dumps(plan), encoding="utf-8")
    return p


def run(capsys, *args):
    code = cli.main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- exit codes on real fixtures (full config) -------------------------------

EXPECTED = {
    "create": 0, "update": 0, "noop": 0, "read": 0, "empty": 0, "read_immediate": 0, "module": 0,
    "delete": 1, "replace": 1, "replace_cbd": 1, "forget": 1,
    "count": 1, "for_each": 1,
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_real_fixture_exit_codes(capsys, cfg, name):
    code, out, err = run(capsys, fixture_path(name), "--config", cfg)
    assert code == EXPECTED[name], (out, err)
    assert ("OK" in out) == (code == 0)


# ---- text output -------------------------------------------------------------

def test_text_output_for_violations(capsys, cfg):
    code, out, _ = run(capsys, fixture_path("replace"), "--config", cfg)
    assert code == 1
    lines = out.strip().splitlines()
    assert lines == [
        "[error] no-replace terraform_data.example (terraform_data) delete,create",
        "[critical] stateful-protect terraform_data.example (terraform_data) delete,create",
        "planfence: FAIL - 2 violation(s)",
    ]


def test_text_pass_line(capsys, cfg):
    code, out, _ = run(capsys, fixture_path("update"), "--config", cfg)
    assert code == 0
    assert out.strip() == "planfence: OK - 1 change(s) checked, all enabled rules passed"


def test_budget_violations_show_numbers_in_text(capsys, tmp_path):
    c = write_cfg(tmp_path, 'enabled_rules = ["budget"]\n[budget]\nmax_changes = 5\n[budget.max_creates_per_type]\naws_instance = 2\n')
    plan = make_plan(
        *[("aws_instance.i%d" % i, "aws_instance", ["create"]) for i in range(3)],
        *[("aws_s3_bucket.b%d" % i, "aws_s3_bucket", ["update"]) for i in range(3)],
    )
    code, out, _ = run(capsys, write_plan(tmp_path, plan), "--config", c)
    assert code == 1
    assert out.splitlines() == [
        "[error] budget total changes 6 > max_changes 5",
        "[error] budget creates of aws_instance 3 > limit 2",
        "planfence: FAIL - 2 violation(s)",
    ]


def test_budget_json_has_count_and_limit_only_for_budget(capsys, tmp_path):
    c = write_cfg(
        tmp_path,
        'enabled_rules = ["budget", "no-delete"]\n[budget]\nmax_changes = 0\n[budget.max_creates_per_type]\nterraform_data = 0\n',
    )
    plan = make_plan(("terraform_data.a", "terraform_data", ["create"]), ("terraform_data.b", "terraform_data", ["delete"]))
    code, out, _ = run(capsys, write_plan(tmp_path, plan), "--config", c, "--format", "json")
    assert code == 1
    vs = json.loads(out)["violations"]
    by_rule = {}
    for v in vs:
        by_rule.setdefault(v["rule"], []).append(v)
    assert by_rule["budget"] == [
        {"rule": "budget", "severity": "error", "address": None, "type": None, "action": None,
         "deposed": None, "previous_address": None, "count": 2, "limit": 0},
        {"rule": "budget", "severity": "error", "address": None, "type": "terraform_data", "action": "create",
         "deposed": None, "previous_address": None, "count": 1, "limit": 0},
    ]
    assert all(type(v["count"]) is int and type(v["limit"]) is int for v in by_rule["budget"])
    assert "count" not in by_rule["no-delete"][0] and "limit" not in by_rule["no-delete"][0]


def test_budget_type_name_is_escaped_in_text(capsys, tmp_path):
    c = write_cfg(tmp_path, 'enabled_rules = ["budget"]\n[budget.max_creates_per_type]\n"a\\u001b[31mb" = 0\n')
    plan = make_plan(("a.b", "a\x1b[31mb", ["create"]))
    code, out, _ = run(capsys, write_plan(tmp_path, plan), "--config", c)
    assert code == 1 and "\x1b" not in out
    assert out.splitlines()[0] == "[error] budget creates of a\\x1b[31mb 1 > limit 0"


# ---- json output -------------------------------------------------------------

def test_json_output(capsys, cfg):
    code, out, _ = run(capsys, fixture_path("replace"), "--config", cfg, "--format", "json")
    assert code == 1
    assert json.loads(out) == {
        "result": "fail",
        "changes_checked": 1,
        "violations": [
            {"rule": "no-replace", "severity": "error", "address": "terraform_data.example",
             "type": "terraform_data", "action": "delete,create", "deposed": None, "previous_address": None},
            {"rule": "stateful-protect", "severity": "critical", "address": "terraform_data.example",
             "type": "terraform_data", "action": "delete,create", "deposed": None, "previous_address": None},
        ],
    }


def test_json_pass(capsys, cfg):
    code, out, _ = run(capsys, fixture_path("noop"), "--config", cfg, "--format", "json")
    assert code == 0
    assert json.loads(out) == {"result": "pass", "changes_checked": 0, "violations": []}


def test_json_error_object_and_exit_2(capsys, cfg, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    code, out, err = run(capsys, bad, "--config", cfg, "--format", "json")
    assert code == 2
    assert json.loads(out)["result"] == "error"
    assert "error" in err


# ---- fail-closed: exit 2 -----------------------------------------------------

def test_missing_plan_file(capsys, cfg, tmp_path):
    code, out, err = run(capsys, tmp_path / "nope.json", "--config", cfg)
    assert (code, out) == (2, "")
    assert "OK" not in out + err


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(format_version="2.0"),
        lambda p: p.update(errored=True),
        lambda p: p.update(complete=False),
        lambda p: p.pop("errored"),
        lambda p: p["resource_changes"][0]["change"].update(actions=["explode"]),
    ],
)
def test_bad_plans_exit_2_never_ok(capsys, cfg, tmp_path, mutate):
    plan = make_plan(("terraform_data.x", "terraform_data", ["create"]))
    mutate(plan)
    code, out, err = run(capsys, write_plan(tmp_path, plan), "--config", cfg)
    assert code == 2
    assert "OK" not in out


def test_state_json_is_not_a_plan(capsys, cfg, tmp_path):
    code, out, _ = run(capsys, write_plan(tmp_path, {"format_version": "1.0", "values": {}}), "--config", cfg)
    assert (code, out) == (2, "")


def test_missing_config_file_exit_2(capsys, tmp_path):
    code, out, err = run(capsys, fixture_path("update"), "--config", tmp_path / "nope.toml")
    assert (code, out) == (2, "")


def test_default_config_is_planfence_toml_in_cwd(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, fixture_path("update"))
    assert (code, out) == (2, "")  # no planfence.toml here: fail closed, do not pass silently
    (tmp_path / "planfence.toml").write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    code, out, _ = run(capsys, fixture_path("update"))
    assert code == 0


def test_config_unknown_key_exit_2(capsys, tmp_path):
    c = write_cfg(tmp_path, 'enabled_rules = ["no-delete"]\noops = 1\n')
    code, out, _ = run(capsys, fixture_path("update"), "--config", c)
    assert (code, out) == (2, "")


def test_argument_errors_exit_2(capsys, cfg):
    for argv in ([], [str(fixture_path("update")), "--format", "xml"], [str(fixture_path("update")), "--bogus"]):
        assert cli.main(argv) == 2
        assert "usage" in capsys.readouterr().err


def test_unexpected_crash_exit_2_never_ok(capsys, cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(cli, "check", boom)
    code, out, err = run(capsys, fixture_path("update"), "--config", cfg)
    assert code == 2
    assert "OK" not in out
    assert "RuntimeError" in err and "secret-internal-detail" not in err


# ---- no attribute values ever printed ---------------------------------------

SECRET = "hunter2-SUPER-SECRET"


def secret_plan():
    plan = make_plan(
        ("terraform_data.db", "terraform_data", ["delete", "create"]),
        ("terraform_data.other", "terraform_data", ["delete"]),
    )
    for rc in plan["resource_changes"]:
        rc["change"].update(
            before={"password": SECRET}, after={"password": SECRET + "2"},
            after_sensitive={"password": True}, after_unknown={},
        )
        rc["name"] = SECRET + "-name"
    plan["planned_values"] = {"root_module": {"x": SECRET}}
    plan["output_changes"] = {"o": {"actions": ["update"], "after": SECRET}}
    return plan


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_secrets_never_printed(capsys, cfg, tmp_path, fmt):
    code, out, err = run(capsys, write_plan(tmp_path, secret_plan()), "--config", cfg, "--format", fmt)
    assert code == 1
    assert SECRET not in out + err


def test_secrets_not_in_error_output_either(capsys, cfg, tmp_path):
    plan = secret_plan()
    plan["resource_changes"][0]["change"]["actions"] = [SECRET]
    code, out, err = run(capsys, write_plan(tmp_path, plan), "--config", cfg)
    assert code == 2
    assert SECRET not in out + err


# ---- determinism and safe printing ------------------------------------------

def test_output_independent_of_input_order(capsys, cfg, tmp_path):
    changes = [(f"terraform_data.r{i}", "terraform_data", ["delete"]) for i in range(6)]
    plan = make_plan(*changes)
    rev = make_plan(*reversed(changes))
    a = run(capsys, write_plan(tmp_path, plan, "a.json"), "--config", cfg, "--format", "json")
    b = run(capsys, write_plan(tmp_path, rev, "b.json"), "--config", cfg, "--format", "json")
    assert a == b
    addrs = [v["address"] for v in json.loads(a[1])["violations"] if v["address"] is not None]
    assert addrs and addrs == sorted(addrs)


def test_control_characters_escaped_in_text(capsys, cfg, tmp_path):
    evil = "terraform_data.x\x1b[31m\nFAKE-LINE"
    plan = make_plan((evil, "terraform_data", ["delete"]))
    code, out, _ = run(capsys, write_plan(tmp_path, plan), "--config", cfg)
    assert code == 1
    assert "\x1b" not in out
    assert "FAKE-LINE" in out and not any(line.startswith("FAKE-LINE") for line in out.splitlines())


def test_deposed_shown(capsys, cfg, tmp_path):
    plan = make_plan(("terraform_data.x", "terraform_data", ["delete"], "0abc"))
    code, out, _ = run(capsys, write_plan(tmp_path, plan), "--config", cfg)
    assert "deposed=0abc" in out


# ---- real process: python -m planfence --------------------------------------

def sub(*args, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run(
        [sys.executable, "-m", "planfence", *map(str, args)], capture_output=True, text=True, env=env, cwd=cwd
    )


def test_subprocess_exit_codes(cfg, tmp_path):
    assert sub(fixture_path("update"), "--config", cfg).returncode == 0
    assert sub(fixture_path("delete"), "--config", cfg).returncode == 1
    assert sub(tmp_path / "missing.json", "--config", cfg).returncode == 2
    assert sub().returncode == 2
