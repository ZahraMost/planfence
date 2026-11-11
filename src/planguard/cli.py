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

from planguard.config import ConfigError, load_config
from planguard.parser import PlanError, load_plan
from planguard.rules import Violation, check

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


def _safe(text: str | None) -> str:
    """Escape non-printable characters so untrusted addresses cannot inject
    terminal escape sequences or fake output lines."""
    if text is None:
        return "-"
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii") for ch in text)


def _text_line(v: Violation) -> str:
    line = f"[{_safe(v.severity)}] {_safe(v.rule)} {_safe(v.address)} ({_safe(v.type)}) {_safe(v.action)}"
    if v.deposed is not None:
        line += f" deposed={_safe(v.deposed)}"
    return line


def _json_obj(v: Violation) -> dict:
    return {
        "rule": v.rule,
        "severity": v.severity,
        "address": v.address,
        "type": v.type,
        "action": v.action,
        "deposed": v.deposed,
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="planguard",
        description="Check a Terraform/OpenTofu plan (terraform show -json output) against a contract.",
        allow_abbrev=False,
    )
    ap.add_argument("plan", help="path to the JSON produced by `terraform show -json <planfile>`")
    ap.add_argument("--config", default="planguard.toml", help="contract file (default: ./planguard.toml)")
    ap.add_argument("--format", choices=("text", "json"), default="text", dest="fmt")
    return ap


def _report_error(fmt: str, message: str) -> int:
    print(f"planguard: error: {message}", file=sys.stderr)
    if fmt == "json":
        print(json.dumps({"result": "error", "message": message}))
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)  # argparse exits with 2 on bad usage
    try:
        config = load_config(args.config)
        plan = load_plan(args.plan)
        violations = check(plan.changes, config)
        checked = sum(1 for c in plan.changes if c.is_change)
    except (PlanError, ConfigError) as exc:
        return _report_error(args.fmt, str(exc))
    except Exception as exc:  # fail closed on anything unexpected
        return _report_error(args.fmt, f"internal error ({type(exc).__name__})")

    if args.fmt == "json":
        print(
            json.dumps(
                {
                    "result": "fail" if violations else "pass",
                    "changes_checked": checked,
                    "violations": [_json_obj(v) for v in violations],
                }
            )
        )
    elif violations:
        for v in violations:
            print(_text_line(v))
        print(f"planguard: FAIL - {len(violations)} violation(s)")
    else:
        print(f"planguard: OK - {checked} change(s) checked, all enabled rules passed")
    return EXIT_VIOLATIONS if violations else EXIT_OK
