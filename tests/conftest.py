"""Shared test helpers."""

import copy
import json
from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def load_fixture(name: str) -> dict:
    return json.loads((REAL / f"{name}.json").read_text(encoding="utf-8"))


def fixture_path(name: str) -> Path:
    return REAL / f"{name}.json"


def make_plan(*changes, **top) -> dict:
    """Minimal valid plan dict. Each change is (address, type, actions[, deposed])."""
    items = []
    for ch in changes:
        address, rtype, actions, *rest = ch
        item = {"address": address, "type": rtype, "change": {"actions": list(actions)}}
        if rest:
            item["deposed"] = rest[0]
        items.append(item)
    plan = {"format_version": "1.2", "errored": False, "complete": True, "resource_changes": items}
    plan.update(top)
    return plan


@pytest.fixture
def valid_plan():
    return copy.deepcopy(make_plan(("a.b", "a", ["create"])))
