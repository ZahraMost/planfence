from pathlib import Path

from planfence.config import load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_example_config_is_valid():
    c = load_config(EXAMPLES / "planfence.toml")
    assert set(c.enabled) == {"no-delete", "no-replace", "scope", "stateful-protect", "budget"}
    assert 'aws_instance.web["blue"]' in c.allow
