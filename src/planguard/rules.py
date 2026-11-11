"""Rule implementations. Each rule takes parsed Changes and returns Violations.

Violations carry only identifiers (rule id, severity, address, type, action),
never attribute values.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planguard.config import Config
    from planguard.parser import Change

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
    previous_address: str | None = None  # set when the entry is a move (`moved` block)

    @property
    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (self.address or "", self.deposed or "", self.rule, self.type or "", self.previous_address or "")


def _from_change(rule: str, severity: str, change: Change) -> Violation:
    return Violation(
        rule, severity, change.address, change.type, change.action_label, change.deposed,
        previous_address=change.previous_address,
    )


def no_delete(changes: Iterable[Change]) -> list[Violation]:
    """actions is exactly ["delete"] (a replacement or forget is not a delete)."""
    return sorted(
        (_from_change("no-delete", SEVERITY_ERROR, c) for c in changes if c.is_delete),
        key=lambda v: v.sort_key,
    )


def no_replace(changes: Iterable[Change]) -> list[Violation]:
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


class AddressMatcher:
    """Match an address against many patterns, with identical semantics to
    glob_match but cheaper for the common pattern shapes:

    - no wildcard            -> set lookup
    - `prefix*` (one `*` at the end, no `?`) -> str.startswith (C loop over all prefixes)
    - everything else        -> glob_match
    """

    def __init__(self, patterns: Iterable[str]):
        exact: set[str] = set()
        prefixes: set[str] = set()
        generic: dict[str, None] = {}  # ordered, de-duplicated
        for p in patterns:
            if "*" not in p and "?" not in p:
                exact.add(p)
            elif p.endswith("*") and "*" not in p[:-1] and "?" not in p:
                prefixes.add(p[:-1])
            else:
                generic[p] = None
        self._exact = frozenset(exact)
        self._prefixes = tuple(sorted(prefixes))
        self._generic = tuple(generic)

    def matches(self, text: str) -> bool:
        if text in self._exact:
            return True
        if self._prefixes and text.startswith(self._prefixes):
            return True
        return any(glob_match(p, text) for p in self._generic)


def _scope_ok(matcher: AddressMatcher, change: Change) -> bool:
    """A moved entry must be allowlisted at BOTH its new and its previous address."""
    return matcher.matches(change.address) and (
        change.previous_address is None or matcher.matches(change.previous_address)
    )


def scope(changes: Iterable[Change], allow: Iterable[str]) -> list[Violation]:
    """Every change must match an allowed pattern; so must every move (even a no-op one).

    no-op and read entries that are not moves are ignored. A moved entry passes only if both
    its address and its previous_address match.
    """
    matcher = AddressMatcher(allow)
    return sorted(
        (
            _from_change("scope", SEVERITY_ERROR, c)
            for c in changes
            if (c.is_change or c.is_move) and not _scope_ok(matcher, c)
        ),
        key=lambda v: v.sort_key,
    )


def stateful_protect(changes: Iterable[Change], stateful_types: Iterable[str]) -> list[Violation]:
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
    changes: Iterable[Change],
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


def check(changes: Iterable[Change], config: Config) -> list[Violation]:
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
