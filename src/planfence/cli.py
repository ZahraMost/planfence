"""Command-line interface.

Exit codes (fail-closed):
  0  plan parsed and every enabled rule passed
  1  at least one rule violated
  2  anything else (bad plan, bad config, bad arguments, crash)

Output contains only rule id, severity, address, type and action (plus the
deposed key when present). Attribute values are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import NoReturn, TextIO

from planfence.config import ConfigError, load_config
from planfence.parser import PlanError, load_plan
from planfence.rules import Violation, check

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


def _safe(text: str | None) -> str:
    """Return pure printable ASCII: escape control and non-ASCII characters so
    untrusted addresses cannot inject terminal escape sequences or fake output
    lines, and so printing can never raise UnicodeEncodeError."""
    if text is None:
        return "-"
    return "".join(
        ch if (ch.isascii() and ch.isprintable()) else ch.encode("unicode_escape").decode("ascii")
        for ch in text
    )


def _text_line(v: Violation) -> str:
    if v.rule == "budget":
        if v.type is None:
            what = f"total changes {v.count} > max_changes {v.limit}"
        else:
            what = f"creates of {_safe(v.type)} {v.count} > limit {v.limit}"
        return f"[{_safe(v.severity)}] budget {what}"
    line = f"[{_safe(v.severity)}] {_safe(v.rule)} {_safe(v.address)} ({_safe(v.type)}) {_safe(v.action)}"
    if v.deposed is not None:
        line += f" deposed={_safe(v.deposed)}"
    if v.previous_address is not None:
        line += f" moved-from={_safe(v.previous_address)}"
    return line


def _json_obj(v: Violation) -> dict[str, str | int | None]:
    obj: dict[str, str | int | None] = {
        "rule": v.rule,
        "severity": v.severity,
        "address": v.address,
        "type": v.type,
        "action": v.action,
        "deposed": v.deposed,
        "previous_address": v.previous_address,
    }
    if v.rule == "budget":
        obj["count"] = v.count
        obj["limit"] = v.limit
    return obj


class _ArgExit(Exception):
    """argparse wanted to exit (help: 0, usage error: 2)."""

    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


class _Parser(argparse.ArgumentParser):
    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:  # never raise SystemExit in main()
        if message:
            _write(sys.stderr, message)
        raise _ArgExit(status)


def _version() -> str:
    """Installed package version; falls back to the module constant when not installed
    (running from a checkout) or when the metadata cannot be read."""
    try:
        from importlib.metadata import version

        return version("planfence")
    except Exception:  # PackageNotFoundError or any broken-metadata problem
        from planfence import __version__

        return __version__


def _build_parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="planfence",
        description="Check a Terraform/OpenTofu plan (terraform show -json output) against a contract.",
        allow_abbrev=False,
    )
    ap.add_argument("plan", help="path to the JSON produced by `terraform show -json <planfile>`")
    ap.add_argument("--config", default="planfence.toml", help="contract file (default: ./planfence.toml)")
    ap.add_argument("--format", choices=("text", "json"), default="text", dest="fmt")
    ap.add_argument("--version", action="version", version=f"planfence {_version()}")
    return ap


def _write(stream: TextIO, text: str) -> bool:
    """Write and flush; report failure instead of raising (BrokenPipeError, UnicodeEncodeError, ...)."""
    try:
        stream.write(text)
        stream.flush()
        return True
    except Exception:
        return False


def _report_error(fmt: str, message: str) -> int:
    _write(sys.stderr, f"planfence: error: {_safe(message)}\n")
    if fmt == "json":
        _write(sys.stdout, json.dumps({"result": "error", "message": message}) + "\n")
    return EXIT_ERROR


def _render(fmt: str, violations: list[Violation], checked: int) -> str:
    if fmt == "json":
        return (
            json.dumps(
                {
                    "result": "fail" if violations else "pass",
                    "changes_checked": checked,
                    "violations": [_json_obj(v) for v in violations],
                }
            )
            + "\n"
        )
    if violations:
        lines = [_text_line(v) for v in violations]
        lines.append(f"planfence: FAIL - {len(violations)} violation(s)")
    else:
        lines = [f"planfence: OK - {checked} change(s) checked, all enabled rules passed"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Always returns 0, 1 or 2; never raises for ordinary failures."""
    fmt = "text"
    try:
        args = _build_parser().parse_args(argv)
        fmt = args.fmt
        config = load_config(args.config)
        plan = load_plan(
            args.plan, plan_source=config.plan_source, state_only_plans=config.state_only_plans
        )
        violations = check(plan.changes, config)
        checked = sum(1 for c in plan.changes if c.is_change)
        output = _render(fmt, violations, checked)
    except _ArgExit as exc:
        return exc.status if exc.status in (0, EXIT_ERROR) else EXIT_ERROR
    except (PlanError, ConfigError) as exc:
        return _report_error(fmt, str(exc))
    except Exception as exc:  # fail closed on anything unexpected
        return _report_error(fmt, f"internal error ({type(exc).__name__})")

    # Result is decided; if it cannot be delivered, that is an error (2), never 0 or 1.
    if not _write(sys.stdout, output):
        _write(sys.stderr, "planfence: error: could not write output\n")
        return EXIT_ERROR
    return EXIT_VIOLATIONS if violations else EXIT_OK
