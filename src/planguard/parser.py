"""Strict, fail-closed parser for `terraform show -json <planfile>` output.

Anything that is not understood raises PlanError (CLI exit code 2). Error
messages never include values from the plan, only field names and indexes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_BYTES = 128 * 1024 * 1024
MAX_STRING_LENGTH = 4096  # characters; applies to resource_changes address, type and deposed

PLAN_SOURCES = ("terraform", "opentofu")

_FORMAT_VERSION_RE = re.compile(r"1\.[0-9]+")

# Exact action lists seen in real plans (see tests/fixtures/README.md).
KNOWN_ACTIONS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("no-op",),
        ("create",),
        ("read",),
        ("update",),
        ("delete",),
        ("forget",),
        ("delete", "create"),
        ("create", "delete"),
    }
)
_UNJUDGEABLE_TOP_LEVEL = ("action_invocations", "deferred_changes")
_NOT_CHANGES: frozenset[tuple[str, ...]] = frozenset({("no-op",), ("read",)})


class PlanError(Exception):
    """The plan could not be fully understood. Maps to exit code 2."""


@dataclass(frozen=True)
class Change:
    address: str
    type: str
    actions: tuple[str, ...]
    deposed: str | None = None

    @property
    def is_change(self) -> bool:
        return self.actions not in _NOT_CHANGES

    @property
    def is_delete(self) -> bool:
        """Exactly ["delete"]. A forget is NOT a delete."""
        return self.actions == ("delete",)

    @property
    def is_replace(self) -> bool:
        return "delete" in self.actions and "create" in self.actions

    @property
    def is_forget(self) -> bool:
        return self.actions == ("forget",)

    @property
    def is_create(self) -> bool:
        """A plain create or a replacement (a replacement counts as a create)."""
        return "create" in self.actions

    @property
    def action_label(self) -> str:
        return ",".join(self.actions)

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.address, self.deposed or "")


@dataclass(frozen=True)
class Plan:
    changes: tuple[Change, ...]  # every resource_changes entry, sorted


def _reject_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PlanError("duplicate key in JSON object")
        obj[key] = value
    return obj


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _check_length(where: str, name: str, value: str) -> None:
    if len(value) > MAX_STRING_LENGTH:
        raise PlanError(f"{where}: '{name}' longer than {MAX_STRING_LENGTH} characters")


def _parse_change(index: int, item: object) -> Change:
    where = f"resource_changes[{index}]"
    if not isinstance(item, dict):
        raise PlanError(f"{where} is not an object")
    address = item.get("address")
    if not _is_nonempty_str(address):
        raise PlanError(f"{where}: 'address' missing or not a non-empty string")
    _check_length(where, "address", address)
    rtype = item.get("type")
    if not _is_nonempty_str(rtype):
        raise PlanError(f"{where}: 'type' missing or not a non-empty string")
    _check_length(where, "type", rtype)
    change = item.get("change")
    if not isinstance(change, dict):
        raise PlanError(f"{where}: 'change' missing or not an object")
    actions = change.get("actions")
    if not isinstance(actions, list) or not actions:
        raise PlanError(f"{where}: 'change.actions' missing, empty or not a list")
    if not all(isinstance(a, str) for a in actions) or tuple(actions) not in KNOWN_ACTIONS:
        raise PlanError(f"{where}: unknown 'change.actions' list")
    if change.get("importing") is not None:
        # An import operation happens in addition to the actions; planguard cannot judge it.
        raise PlanError(f"{where}: 'change.importing' is present (import operations are not supported)")
    deposed = None
    if "deposed" in item:
        deposed = item["deposed"]
        if not _is_nonempty_str(deposed):
            raise PlanError(f"{where}: 'deposed' present but not a non-empty string")
        _check_length(where, "deposed", deposed)
    return Change(address=address, type=rtype, actions=tuple(actions), deposed=deposed)


def parse_plan(data: object, plan_source: str = "terraform") -> Plan:
    """Validate decoded JSON and return a Plan. Raises PlanError otherwise.

    plan_source "terraform" (default, strict): `errored` and `complete` must be present booleans.
    plan_source "opentofu": OpenTofu's plan JSON has no `complete` (and no `applyable`), so
    `complete` is optional, but if present it must be boolean true. `errored` stays required.
    """
    if plan_source not in PLAN_SOURCES:
        raise PlanError("unknown plan_source")
    if not isinstance(data, dict):
        raise PlanError("top level is not a JSON object")
    version = data.get("format_version")
    if not isinstance(version, str) or _FORMAT_VERSION_RE.fullmatch(version) is None:
        raise PlanError("'format_version' missing or not a supported 1.x version")
    errored = data.get("errored")
    if plan_source == "terraform":
        complete = data.get("complete")
        if not isinstance(errored, bool) or not isinstance(complete, bool):
            raise PlanError(
                "'errored' and 'complete' must be present booleans (is this a plan file? "
                'OpenTofu plans need plan_source = "opentofu" in the config)'
            )
    else:
        if not isinstance(errored, bool):
            raise PlanError("'errored' must be a present boolean (is this a plan file?)")
        if "complete" in data and not isinstance(data["complete"], bool):
            raise PlanError("'complete', if present, must be a boolean")
    if errored:
        raise PlanError("plan is marked errored")
    if "complete" in data and not data["complete"]:
        raise PlanError("plan is not marked complete")
    # Content outside resource_changes that can still act at apply: planguard cannot
    # judge it, so anything but absent / [] is refused (fail closed).
    for key in _UNJUDGEABLE_TOP_LEVEL:
        if key in data and not (isinstance(data[key], list) and not data[key]):
            raise PlanError(f"'{key}' is present and not an empty list (not supported)")
    # Absent resource_changes == nothing planned (only reached once the checks above passed).
    raw = data.get("resource_changes", [])
    if not isinstance(raw, list):
        raise PlanError("'resource_changes' is not a list")
    changes = [_parse_change(i, item) for i, item in enumerate(raw)]
    changes.sort(key=lambda c: c.sort_key)
    return Plan(changes=tuple(changes))


def load_plan(path: str | Path, max_bytes: int = DEFAULT_MAX_BYTES, plan_source: str = "terraform") -> Plan:
    """Read, size-limit, decode and validate a plan JSON file (see parse_plan for plan_source)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        raise PlanError(f"cannot read plan file ({type(exc).__name__})") from None
    if len(raw) > max_bytes:
        raise PlanError(f"plan file larger than {max_bytes} bytes")
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except PlanError:
        raise
    except (ValueError, RecursionError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise PlanError("plan file is not valid JSON") from None
    return parse_plan(data, plan_source)
