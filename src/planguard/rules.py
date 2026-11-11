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
    count: int | None = None  # budget only: observed number
    limit: int | None = None  # budget only: configured maximum

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


# ---- address matching ------------------------------------------------------

def glob_match(pattern: str, text: str) -> bool:
    """Match `text` against `pattern`, case-sensitive.

    Only `*` (any run of characters, including none, dots and quotes) and `?`
    (exactly one character) are wildcards. Everything else, including `[`, is
    literal. Linear-time two-pointer algorithm (no regex, no backtracking blowup).
    """
    p = t = 0
    lp, lt = len(pattern), len(text)
    star = -1
    mark = 0
    while t < lt:
        if p < lp and pattern[p] == "*":
            star, mark = p, t
            p += 1
        elif p < lp and (pattern[p] == "?" or pattern[p] == text[t]):
            p += 1
            t += 1
        elif star != -1:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < lp and pattern[p] == "*":
        p += 1
    return p == lp


def scope(changes: Iterable, allow: Iterable[str]) -> list[Violation]:
    """Every change (not no-op / read) must match at least one allowed pattern."""
    patterns = list(allow)
    return sorted(
        (
            _from_change("scope", SEVERITY_ERROR, c)
            for c in changes
            if c.is_change and not any(glob_match(p, c.address) for p in patterns)
        ),
        key=lambda v: v.sort_key,
    )


def stateful_protect(changes: Iterable, stateful_types: Iterable[str]) -> list[Violation]:
    """delete, replace or forget of a configured stateful type is critical.

    Types are matched exactly (no wildcards, case-sensitive).
    """
    types = frozenset(stateful_types)
    return sorted(
        (
            _from_change("stateful-protect", SEVERITY_CRITICAL, c)
            for c in changes
            if c.type in types and (c.is_delete or c.is_replace or c.is_forget)
        ),
        key=lambda v: v.sort_key,
    )


def budget(
    changes: Iterable,
    max_changes: int | None,
    max_creates_per_type: dict[str, int],
) -> list[Violation]:
    """Total changes and per-type creates must stay within limits.

    A change is any entry that is not no-op / read. A replacement counts as one
    change and one create for its type. Aggregate violations carry no address but do carry count and limit.
    """
    total = 0
    creates: dict[str, int] = {}
    for c in changes:
        if not c.is_change:
            continue
        total += 1
        if c.is_create:
            creates[c.type] = creates.get(c.type, 0) + 1
    out: list[Violation] = []
    if max_changes is not None and total > max_changes:
        out.append(Violation("budget", SEVERITY_ERROR, None, None, None, count=total, limit=max_changes))
    for rtype, limit in max_creates_per_type.items():
        count = creates.get(rtype, 0)
        if count > limit:
            out.append(Violation("budget", SEVERITY_ERROR, None, rtype, "create", count=count, limit=limit))
    return sorted(out, key=lambda v: v.sort_key)


def check(changes: Iterable, config) -> list[Violation]:
    """Run every enabled rule; return all violations in deterministic order."""
    changes = list(changes)
    out: list[Violation] = []
    if "no-delete" in config.enabled:
        out += no_delete(changes)
    if "no-replace" in config.enabled:
        out += no_replace(changes)
    if "scope" in config.enabled:
        out += scope(changes, config.allow)
    if "stateful-protect" in config.enabled:
        out += stateful_protect(changes, config.stateful_types)
    if "budget" in config.enabled:
        out += budget(changes, config.max_changes, config.max_creates_per_type)
    return sorted(out, key=lambda v: v.sort_key)
