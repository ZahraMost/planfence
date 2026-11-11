"""planguard.toml loading. Strict: any unknown key, wrong type or inconsistency
is a ConfigError (CLI exit code 2).

Format:

    enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]

    [scope]                       # required iff "scope" is enabled
    allow = ["aws_s3_bucket.*", "module.app.*"]   # at most 500 patterns, each <= 512 chars

    [stateful]                    # required iff "stateful-protect" is enabled
    types = ["aws_db_instance"]

    [budget]                      # required iff "budget" is enabled
    max_changes = 10              # optional
    [budget.max_creates_per_type] # optional
    aws_instance = 2
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MAX_CONFIG_BYTES = 1024 * 1024
MAX_ALLOW_PATTERNS = 500
MAX_PATTERN_LENGTH = 512  # characters

RULE_IDS = ("no-delete", "no-replace", "scope", "stateful-protect", "budget")

# rule id -> config section it requires
_SECTION_FOR_RULE = {"scope": "scope", "stateful-protect": "stateful", "budget": "budget"}
_TOP_KEYS = {"enabled_rules", "scope", "stateful", "budget"}


class ConfigError(Exception):
    """The configuration is invalid. Maps to exit code 2."""


@dataclass(frozen=True)
class Config:
    enabled: tuple[str, ...]
    allow: tuple[str, ...] = ()
    stateful_types: tuple[str, ...] = ()
    max_changes: int | None = None
    max_creates_per_type: dict[str, int] = field(default_factory=dict)


def _table(value: object, name: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in [{name}]: {', '.join(unknown)}")
    return value


def _str_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty list of strings")
    if not all(isinstance(v, str) and v != "" for v in value):
        raise ConfigError(f"{name} must contain only non-empty strings")
    return tuple(value)


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{name} must be an integer >= 0")
    return value


def parse_config(data: object) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("configuration must be a table")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(unknown)}")

    if "enabled_rules" not in data:
        raise ConfigError("'enabled_rules' is required")
    enabled = _str_list(data["enabled_rules"], "enabled_rules")
    bad = [r for r in enabled if r not in RULE_IDS]
    if bad:
        raise ConfigError(f"unknown rule id(s): {', '.join(bad)}")
    if len(set(enabled)) != len(enabled):
        raise ConfigError("duplicate rule id in enabled_rules")

    for rule, section in _SECTION_FOR_RULE.items():
        if (rule in enabled) != (section in data):
            if rule in enabled:
                raise ConfigError(f"rule '{rule}' is enabled but section [{section}] is missing")
            raise ConfigError(f"section [{section}] is present but rule '{rule}' is not enabled")

    allow: tuple[str, ...] = ()
    if "scope" in data:
        scope = _table(data["scope"], "scope", {"allow"})
        if "allow" not in scope:
            raise ConfigError("[scope] requires 'allow'")
        allow = _str_list(scope["allow"], "scope.allow")
        if len(allow) > MAX_ALLOW_PATTERNS:
            raise ConfigError(f"scope.allow has more than {MAX_ALLOW_PATTERNS} patterns")
        if any(len(p) > MAX_PATTERN_LENGTH for p in allow):
            raise ConfigError(f"scope.allow has a pattern longer than {MAX_PATTERN_LENGTH} characters")

    stateful_types: tuple[str, ...] = ()
    if "stateful" in data:
        stateful = _table(data["stateful"], "stateful", {"types"})
        if "types" not in stateful:
            raise ConfigError("[stateful] requires 'types'")
        stateful_types = _str_list(stateful["types"], "stateful.types")

    max_changes = None
    max_creates: dict[str, int] = {}
    if "budget" in data:
        budget = _table(data["budget"], "budget", {"max_changes", "max_creates_per_type"})
        if not budget:
            raise ConfigError("[budget] needs 'max_changes' and/or 'max_creates_per_type'")
        if "max_changes" in budget:
            max_changes = _count(budget["max_changes"], "budget.max_changes")
        if "max_creates_per_type" in budget:
            table = budget["max_creates_per_type"]
            if not isinstance(table, dict) or not table:
                raise ConfigError("budget.max_creates_per_type must be a non-empty table")
            for rtype, limit in table.items():
                if rtype == "":
                    raise ConfigError("budget.max_creates_per_type has an empty type name")
                max_creates[rtype] = _count(limit, f"budget.max_creates_per_type.{rtype}")

    return Config(
        enabled=enabled,
        allow=allow,
        stateful_types=stateful_types,
        max_changes=max_changes,
        max_creates_per_type=max_creates,
    )


def load_config(path: str | Path, max_bytes: int = MAX_CONFIG_BYTES) -> Config:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        raise ConfigError(f"cannot read config file ({type(exc).__name__})") from None
    if len(raw) > max_bytes:
        raise ConfigError(f"config file larger than {max_bytes} bytes")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ConfigError("config file is not valid UTF-8 TOML") from None
    return parse_config(data)
