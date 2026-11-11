import copy

import pytest

from planguard.config import Config, ConfigError, load_config, parse_config

FULL = {
    "enabled_rules": ["no-delete", "no-replace", "scope", "stateful-protect", "budget"],
    "scope": {"allow": ["aws_s3_bucket.*", 'module.app.x["k"]']},
    "stateful": {"types": ["aws_db_instance", "aws_s3_bucket"]},
    "budget": {"max_changes": 5, "max_creates_per_type": {"aws_instance": 2}},
}


def test_full_config_parses():
    c = parse_config(copy.deepcopy(FULL))
    assert c == Config(
        enabled=("no-delete", "no-replace", "scope", "stateful-protect", "budget"),
        allow=("aws_s3_bucket.*", 'module.app.x["k"]'),
        stateful_types=("aws_db_instance", "aws_s3_bucket"),
        max_changes=5,
        max_creates_per_type={"aws_instance": 2},
    )


def test_minimal_config():
    c = parse_config({"enabled_rules": ["no-delete"]})
    assert c.enabled == ("no-delete",)
    assert c.max_changes is None and c.max_creates_per_type == {}


def test_budget_single_limit_ok():
    assert parse_config({"enabled_rules": ["budget"], "budget": {"max_changes": 0}}).max_changes == 0
    c = parse_config({"enabled_rules": ["budget"], "budget": {"max_creates_per_type": {"t": 0}}})
    assert c.max_creates_per_type == {"t": 0}


def _bad(**changes):
    cfg = copy.deepcopy(FULL)
    for key, value in changes.items():
        if value is ...:
            cfg.pop(key, None)
        else:
            cfg[key] = value
    return cfg


BAD = {
    "not a table": None,
    "unknown top-level key": _bad(extra=1),
    "typo in key": _bad(enabled_rule=["no-delete"]),
    "missing enabled_rules": _bad(enabled_rules=...),
    "enabled_rules empty": _bad(enabled_rules=[]),
    "enabled_rules string": _bad(enabled_rules="no-delete"),
    "enabled_rules unknown id": _bad(enabled_rules=["no-delete", "no-deletes"]),
    "enabled_rules non-str": _bad(enabled_rules=[1]),
    "enabled_rules duplicate": _bad(enabled_rules=["no-delete", "no-delete"]),
    "scope enabled, section missing": _bad(scope=...),
    "stateful-protect enabled, section missing": _bad(stateful=...),
    "budget enabled, section missing": _bad(budget=...),
    "scope not a table": _bad(scope=["a"]),
    "scope unknown key": _bad(scope={"allow": ["a"], "allows": ["b"]}),
    "scope allow missing": _bad(scope={}),
    "scope allow empty": _bad(scope={"allow": []}),
    "scope allow string": _bad(scope={"allow": "a.*"}),
    "scope allow empty pattern": _bad(scope={"allow": [""]}),
    "scope allow non-str": _bad(scope={"allow": [1]}),
    "stateful unknown key": _bad(stateful={"types": ["a"], "type": ["b"]}),
    "stateful types missing": _bad(stateful={}),
    "stateful types empty": _bad(stateful={"types": []}),
    "stateful types non-str": _bad(stateful={"types": [True]}),
    "stateful types empty string": _bad(stateful={"types": [""]}),
    "budget unknown key": _bad(budget={"max_changes": 1, "max_change": 2}),
    "budget empty": _bad(budget={}),
    "budget max_changes negative": _bad(budget={"max_changes": -1}),
    "budget max_changes float": _bad(budget={"max_changes": 1.5}),
    "budget max_changes bool": _bad(budget={"max_changes": True}),
    "budget max_changes string": _bad(budget={"max_changes": "3"}),
    "budget creates not table": _bad(budget={"max_creates_per_type": [1]}),
    "budget creates negative": _bad(budget={"max_creates_per_type": {"t": -1}}),
    "budget creates bool": _bad(budget={"max_creates_per_type": {"t": False}}),
    "budget creates float": _bad(budget={"max_creates_per_type": {"t": 1.0}}),
    "budget creates empty key": _bad(budget={"max_creates_per_type": {"": 1}}),
    "section present but rule disabled (scope)": _bad(enabled_rules=["no-delete", "stateful-protect", "budget"]),
    "section present but rule disabled (stateful)": _bad(enabled_rules=["no-delete", "scope", "budget"]),
    "section present but rule disabled (budget)": _bad(enabled_rules=["no-delete", "scope", "stateful-protect"]),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_bad_config_rejected(name):
    with pytest.raises(ConfigError):
        parse_config(BAD[name])


def test_load_config_from_file(tmp_path):
    p = tmp_path / "planguard.toml"
    p.write_text(
        'enabled_rules = ["no-delete", "budget"]\n'
        "[budget]\nmax_changes = 3\n[budget.max_creates_per_type]\naws_instance = 1\n",
        encoding="utf-8",
    )
    c = load_config(p)
    assert c.enabled == ("no-delete", "budget")
    assert c.max_changes == 3 and c.max_creates_per_type == {"aws_instance": 1}


def test_load_config_unknown_key_in_file(tmp_path):
    p = tmp_path / "planguard.toml"
    p.write_text('enabled_rules = ["no-delete"]\nsurprise = 1\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


@pytest.mark.parametrize("text", ["", "enabled_rules = [", "enabled_rules = ['no-delete']\nenabled_rules = ['scope']"])
def test_load_config_bad_toml(tmp_path, text):
    p = tmp_path / "planguard.toml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_missing_file_and_directory(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_load_config_invalid_utf8_and_size_limit(tmp_path):
    p = tmp_path / "planguard.toml"
    p.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, max_bytes=5)
