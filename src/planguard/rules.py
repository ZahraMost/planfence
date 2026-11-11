"""Rule implementations. Each rule takes parsed Changes and returns Violations.

Violations carry only identifiers (rule id, severity, address, type, action),
never attribute values.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str
    address: str | None  # None for aggregate rules (budget)
    type: str | None
    action: str | None
    deposed: str | None = None

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.address or "", self.deposed or "", self.rule, self.type or "")


def _from_change(rule: str, severity: str, change) -> Violation:
    return Violation(rule, severity, change.address, change.type, change.action_label, change.deposed)


def no_delete(changes: Iterable) -> list[Violation]:
    """actions is exactly ["delete"] (a replacement or forget is not a delete)."""
    return sorted(
        (_from_change("no-delete", SEVERITY_ERROR, c) for c in changes if c.is_delete),
        key=lambda v: v.sort_key,
    )


def no_replace(changes: Iterable) -> list[Violation]:
    """actions contains both "delete" and "create"."""
    return sorted(
        (_from_change("no-replace", SEVERITY_ERROR, c) for c in changes if c.is_replace),
        key=lambda v: v.sort_key,
    )
