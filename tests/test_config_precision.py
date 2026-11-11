"""Each invalid config is rejected for exactly the intended reason (not by accident)."""

import re

import pytest

from planguard.config import ConfigError, load_config, parse_config

CASES = [
    ({"enabled_rules": ["no-delete", "no-delete"]}, "duplicate rule id"),
    ({"enabled_rules": ["no-deletes"]}, "unknown rule id"),
    ({"enabled_rules": ["scope"]}, "section [scope] is missing"),
    ({"enabled_rules": ["stateful-protect"]}, "section [stateful] is missing"),
    ({"enabled_rules": ["budget"]}, "section [budget] is missing"),
    ({"enabled_rules": ["no-delete"], "scope": {"allow": ["a"]}}, "present but rule 'scope' is not enabled"),
    ({"enabled_rules": ["no-delete"], "stateful": {"types": ["a"]}}, "present but rule 'stateful-protect'"),
    ({"enabled_rules": ["no-delete"], "budget": {"max_changes": 1}}, "present but rule 'budget'"),
    ({"enabled_rules": []}, "non-empty list"),
    ({"enabled_rules": ["no-delete"], "extra": 1}, "unknown key"),
    ({"enabled_rules": ["scope"], "scope": {"allow": []}}, "non-empty list"),
    ({"enabled_rules": ["scope"], "scope": {"allow": [""]}}, "non-empty strings"),
    ({"enabled_rules": ["scope"], "scope": {"allow": ["a"], "x": 1}}, "unknown key"),
    ({"enabled_rules": ["stateful-protect"], "stateful": {"types": []}}, "non-empty list"),
    ({"enabled_rules": ["budget"], "budget": {}}, "needs 'max_changes'"),
    ({"enabled_rules": ["budget"], "budget": {"max_creates_per_type": {}}}, "non-empty table"),
    ({"enabled_rules": ["budget"], "budget": {"max_creates_per_type": []}}, "non-empty table"),
    ({"enabled_rules": ["budget"], "budget": {"max_creates_per_type": {"": 1}}}, "empty type name"),
    ({"enabled_rules": ["budget"], "budget": {"max_changes": True}}, "integer >= 0"),
    ({"enabled_rules": ["budget"], "budget": {"max_changes": -1}}, "integer >= 0"),
    ({"enabled_rules": ["budget"], "budget": {"max_creates_per_type": {"t": True}}}, "integer >= 0"),
    ({"enabled_rules": ["no-delete"], "plan_source": "tofu"}, 'plan_source must be "terraform" or "opentofu"'),
    ({"enabled_rules": ["no-delete"], "plan_source": 1}, 'plan_source must be "terraform" or "opentofu"'),
]


@pytest.mark.parametrize("cfg, fragment", CASES, ids=lambda x: str(x)[:60])
def test_rejected_for_the_right_reason(cfg, fragment):
    with pytest.raises(ConfigError, match=re.escape(fragment)):
        parse_config(cfg)


def test_config_size_limit_exact_boundary(tmp_path):
    text = 'enabled_rules = ["no-delete"]\n'
    p = tmp_path / "c.toml"
    p.write_text(text, encoding="utf-8")
    n = len(text.encode())
    assert load_config(p, max_bytes=n).enabled == ("no-delete",)
    with pytest.raises(ConfigError, match="larger than"):
        load_config(p, max_bytes=n - 1)
