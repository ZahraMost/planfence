"""Manual mutation testing for planguard (standard library only).

For every mutation below the script copies src/ to a fresh temp directory,
applies ONE small source change to the copy, runs the test suite against that
copy and records whether any test failed (KILLED) or not (SURVIVED). The real
src/ is never modified (its hash is checked before and after).

A SURVIVED mutant means the tests cannot tell the difference: either add a test
that kills it or explain it in EQUIVALENT below (a change that cannot alter
behaviour).

Usage:
    python scripts/mutation_check.py [--jobs N] [--only ID ...] [--timeout SECONDS]

Exit status: 0 if every mutant is killed or explained, 1 otherwise (2 on setup errors).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


@dataclass(frozen=True)
class Mutation:
    id: str
    file: str  # relative to src/planguard/
    old: str  # must occur exactly once in the file
    new: str
    desc: str


M = Mutation

MUTATIONS: list[Mutation] = [
    # -- plumbing check: this one MUST be killed, otherwise tests are not running against the copy
    M("M00", "cli.py", "EXIT_OK = 0", "EXIT_OK = 5", "SANITY: exit code for success 0 -> 5"),
    # -- parser.py: action semantics
    M("M01", "parser.py", 'return "delete" in self.actions and "create" in self.actions',
      'return "delete" in self.actions or "create" in self.actions', "is_replace: and -> or"),
    M("M02", "parser.py", 'return self.actions == ("delete",)',
      'return self.actions in (("delete",), ("forget",))', "is_delete also accepts forget"),
    M("M03", "parser.py", 'return "create" in self.actions',
      'return self.actions == ("create",)', "is_create ignores replacements"),
    M("M04", "parser.py", 'frozenset({("no-op",), ("read",)})', 'frozenset({("no-op",)})',
      "read counts as a change"),
    M("M05", "parser.py", 'return (self.address, self.deposed or "")', 'return (self.address, "")',
      "Change.sort_key drops the deposed key"),
    M("M06", "parser.py", "changes.sort(key=lambda c: c.sort_key)", "pass", "parser no longer sorts changes"),
    # -- parser.py: fail-closed checks
    M("M07", "parser.py", "if errored:", "if False:", "remove the errored check"),
    M("M08", "parser.py", 'if "complete" in data and not data["complete"]:', "if False:", "remove the complete check"),
    M("M09", "parser.py", "if not isinstance(errored, bool) or not isinstance(complete, bool):", "if False:",
      "remove the errored/complete boolean type check"),
    M("M10", "parser.py", 'if change.get("importing") is not None:', "if False:", "remove the importing check"),
    M("M11", "parser.py", 'if change.get("importing") is not None:', 'if change.get("importing"):',
      "importing check uses truthiness (empty object passes)"),
    M("M12", "parser.py", '_UNJUDGEABLE_TOP_LEVEL = ("action_invocations", "deferred_changes")',
      '_UNJUDGEABLE_TOP_LEVEL = ("deferred_changes",)', "drop the action_invocations check"),
    M("M13", "parser.py", "if key in data and not (isinstance(data[key], list) and not data[key]):",
      "if key in data and data[key]:", "action_invocations/deferred_changes: falsy non-list values pass"),
    M("M14", "parser.py", 're.compile(r"1\\.[0-9]+")', 're.compile(r"[0-9]\\.[0-9]+")',
      "format_version accepts any single-digit major"),
    M("M15", "parser.py", 're.compile(r"1\\.[0-9]+")', 're.compile(r"1\\.[0-9]*")', 'format_version accepts "1."'),
    M("M16", "parser.py", "if len(value) > MAX_STRING_LENGTH:", "if len(value) >= MAX_STRING_LENGTH:",
      "string limit off by one (> -> >=)"),
    M("M17", "parser.py", '        ("create", "delete"),\n    }', '        ("create", "delete"),\n        ("update", "delete"),\n    }',
      'KNOWN_ACTIONS accepts ["update","delete"]'),
    M("M18", "parser.py", "if key in obj:", "if False:", "duplicate JSON keys accepted"),
    M("M19", "parser.py", "if not _is_nonempty_str(deposed):", "if False:", "deposed not validated"),
    M("M20", "parser.py", 'raw = data.get("resource_changes", [])', 'raw = data.get("resource_changes") or []',
      "null/empty-ish resource_changes accepted"),
    M("M21", "parser.py", "if len(raw) > max_bytes:", "if len(raw) >= max_bytes:", "size limit off by one"),
    # -- rules.py
    M("M22", "rules.py", "if max_changes is not None and total > max_changes:",
      "if max_changes is not None and total >= max_changes:", "budget total: > -> >="),
    M("M23", "rules.py", "if count > limit:", "if count >= limit:", "budget creates per type: > -> >="),
    M("M24", "rules.py", "if not c.is_change:", "if False:", "budget counts no-op/read entries"),
    M("M25", "rules.py", "if c.is_change and not matcher.matches(c.address)", "if not matcher.matches(c.address)",
      "scope also checks no-op/read entries"),
    M("M26", "rules.py", "(c.is_delete or c.is_replace or c.is_forget)", "(c.is_delete or c.is_replace)",
      "stateful-protect ignores forget"),
    M("M27", "rules.py", "(c.is_delete or c.is_replace or c.is_forget)", "(c.is_delete or c.is_forget)",
      "stateful-protect ignores replace"),
    M("M28", "rules.py", '_from_change("stateful-protect", SEVERITY_CRITICAL, c)',
      '_from_change("stateful-protect", SEVERITY_ERROR, c)', "stateful-protect severity critical -> error"),
    M("M29", "rules.py", '(pattern[p] == "?" or pattern[p] == text[t])',
      '(pattern[p] == "?" or pattern[p].lower() == text[t].lower())', "glob_match case-insensitive"),
    M("M30", "rules.py", "return p == lp", "return True", "glob_match: leftover pattern accepted"),
    M("M31", "rules.py", "text.startswith(self._prefixes)", "text.endswith(self._prefixes)",
      "prefix fast path uses endswith"),
    M("M32", "rules.py", 'elif p.endswith("*") and "*" not in p[:-1] and "?" not in p:',
      'elif p.endswith("*") and "?" not in p:', "prefix fast path also takes patterns with inner *"),
    M("M33", "rules.py", 'elif p.endswith("*") and "*" not in p[:-1] and "?" not in p:',
      'elif p.endswith("*") and "*" not in p[:-1]:', "prefix fast path also takes patterns with ?"),
    M("M34", "rules.py", 'if "*" not in p and "?" not in p:', 'if "*" not in p:',
      "exact fast path treats ? as a literal"),
    M("M35", "rules.py", 'return (self.address or "", self.deposed or "", self.rule, self.type or "")',
      'return (self.address or "", self.rule, self.type or "")', "Violation.sort_key drops the deposed key"),
    M("M36", "rules.py", 'if "no-replace" in config.enabled:', 'if "no-delete" in config.enabled:',
      "no-replace wired to the no-delete switch"),
    M("M37", "rules.py", 'if "scope" in config.enabled:', 'if "scope" not in config.enabled:',
      "scope switch inverted"),
    # -- config.py
    M("M38", "config.py", "    unknown = sorted(set(data) - _TOP_KEYS)\n    if unknown:",
      "    unknown = sorted(set(data) - _TOP_KEYS)\n    if False:", "unknown top-level config keys accepted"),
    M("M39", "config.py", "    unknown = sorted(set(value) - allowed)\n    if unknown:",
      "    unknown = sorted(set(value) - allowed)\n    if False:", "unknown keys inside config sections accepted"),
    M("M40", "config.py", "if len(allow) > MAX_ALLOW_PATTERNS:", "if len(allow) >= MAX_ALLOW_PATTERNS:",
      "allow-pattern count limit off by one"),
    M("M41", "config.py", "if any(len(p) > MAX_PATTERN_LENGTH for p in allow):",
      "if any(len(p) >= MAX_PATTERN_LENGTH for p in allow):", "pattern length limit off by one"),
    M("M42", "config.py", "if isinstance(value, bool) or not isinstance(value, int) or value < 0:",
      "if not isinstance(value, int) or value < 0:", "booleans accepted as budget numbers"),
    M("M43", "config.py", "if len(set(enabled)) != len(enabled):", "if False:", "duplicate rule ids accepted"),
    # -- cli.py
    M("M44", "cli.py", "EXIT_VIOLATIONS = 1", "EXIT_VIOLATIONS = 2", "violations exit with 2"),
    M("M45", "cli.py", "EXIT_ERROR = 2", "EXIT_ERROR = 1", "errors exit with 1"),
    M("M46", "cli.py", "return EXIT_VIOLATIONS if violations else EXIT_OK", "return EXIT_OK",
      "violations never change the exit code"),
    M("M47", "cli.py", "ch if (ch.isascii() and ch.isprintable())", "ch if ch.isprintable()",
      "text output keeps printable non-ASCII characters"),
    M("M48", "cli.py", 'could not write output\\n")\n        return EXIT_ERROR',
      'could not write output\\n")\n        return EXIT_VIOLATIONS if violations else EXIT_OK',
      "failed stdout write keeps the 0/1 result"),
    M("M49", "cli.py", "checked = sum(1 for c in plan.changes if c.is_change)", "checked = len(plan.changes)",
      "changes_checked counts no-op/read entries"),
    M("M50", "cli.py", 'what = f"total changes {v.count} > max_changes {v.limit}"',
      'what = f"total changes {v.count} >= max_changes {v.limit}"', "budget text shows >= instead of >"),
    M("M51", "cli.py", "return exc.status if exc.status in (0, EXIT_ERROR) else EXIT_ERROR", "return exc.status",
      "argparse exit status passed through unchecked"),
    M("M52", "cli.py", '"message": message}) + "\\n")\n    return EXIT_ERROR',
      '"message": message}) + "\\n")\n    return EXIT_VIOLATIONS', "JSON error report returns 1"),
    # -- __main__.py
    M("M53", "__main__.py", "        _silence_stdout()\n", "        pass\n", "do not redirect broken stdout"),
    M("M54", "__main__.py", "if type(code) is not int or code not in (0, 1, 2):", "if False:",
      "invalid return values pass through"),
    M("M55", "__main__.py", "    except BaseException as exc:  # incl.", "    except Exception as exc:  # incl.",
      "run() lets KeyboardInterrupt/SystemExit escape"),
    M("M56", "__main__.py", '_note("planguard: error: could not write output")\n        code = EXIT_ERROR',
      '_note("planguard: error: could not write output")', "failing final flush keeps the old exit code"),
    # -- second round: tests that may pass for the wrong reason
    M("M57", "config.py", "    if bad:\n", "    if False:\n", "unknown rule ids accepted"),
    M("M58", "config.py", "if (rule in enabled) != (section in data):",
      "if (rule in enabled) and (section not in data):", "section for a disabled rule accepted"),
    M("M59", "config.py", "if (rule in enabled) != (section in data):",
      "if (rule not in enabled) and (section in data):", "enabled rule without its section accepted"),
    M("M60", "config.py", "if not isinstance(value, list) or not value:", "if not isinstance(value, list):",
      "empty string lists accepted"),
    M("M61", "config.py", 'if not all(isinstance(v, str) and v != "" for v in value):',
      "if not all(isinstance(v, str) for v in value):", "empty strings in lists accepted"),
    M("M62", "config.py", "        if not budget:\n", "        if False:\n", "empty [budget] accepted"),
    M("M63", "config.py", 'if rtype == "":', "if False:", "empty type name in max_creates_per_type accepted"),
    M("M64", "config.py", "if not isinstance(table, dict) or not table:", "if not isinstance(table, dict):",
      "empty max_creates_per_type table accepted"),
    M("M65", "config.py", "if len(raw) > max_bytes:", "if len(raw) >= max_bytes:", "config size limit off by one"),
    M("M66", "parser.py", 'return isinstance(value, str) and value != ""', "return isinstance(value, str)",
      "empty address/type/deposed accepted"),
    M("M67", "rules.py", '_from_change("no-delete", SEVERITY_ERROR, c)', '_from_change("no-delete", SEVERITY_CRITICAL, c)',
      "no-delete severity error -> critical"),
    M("M68", "rules.py", "elif star != -1:", "elif False:", "glob_match: no backtracking after *"),
    M("M69", "rules.py", "if text in self._exact:", "if text.lower() in self._exact:",
      "exact fast path matches case-insensitively (lowercase text)"),
    M("M70", "cli.py", "if v.deposed is not None:", "if False:", "deposed key not shown in text output"),
    M("M71", "rules.py", "if c.type in types and", "if any(c.type.startswith(t) for t in types) and",
      "stateful types matched by prefix instead of exactly"),
    M("M72", "parser.py", "_FORMAT_VERSION_RE.fullmatch(version) is None", "_FORMAT_VERSION_RE.match(version) is None",
      "format_version only matched at the start"),
    M("M73", "parser.py", "if not isinstance(actions, list) or not actions:", "if not isinstance(actions, list):",
      "empty actions list reaches the KNOWN_ACTIONS check instead of its own check"),
    M("M74", "rules.py", "count = creates.get(rtype, 0)", "count = creates.get(rtype, 1)",
      "budget: unseen types count as one create"),
    # -- plan_source (OpenTofu mode)
    M("M75", "parser.py", "        if not isinstance(errored, bool):\n", "        if False:\n",
      "opentofu mode skips the errored presence/type check"),
    M("M76", "parser.py", 'if "complete" in data and not isinstance(data["complete"], bool):', "if False:",
      "opentofu mode accepts a non-boolean complete"),
    M("M77", "parser.py", 'if "complete" in data and not data["complete"]:',
      'if plan_source == "terraform" and "complete" in data and not data["complete"]:',
      "opentofu mode accepts complete=false"),
    M("M78", "parser.py", "if not isinstance(errored, bool) or not isinstance(complete, bool):",
      'if not isinstance(errored, bool) or ("complete" in data and not isinstance(complete, bool)):',
      "default (terraform) mode accepts a missing complete"),
    M("M79", "parser.py", "if plan_source not in PLAN_SOURCES:", "if False:", "parser does not validate the mode"),
    M("M80", "config.py", "if not isinstance(plan_source, str) or plan_source not in PLAN_SOURCES:",
      "if not isinstance(plan_source, str):", "config does not validate the plan_source value"),
    M("M81", "config.py", "if not isinstance(plan_source, str) or plan_source not in PLAN_SOURCES:",
      "if plan_source not in PLAN_SOURCES:", "config does not check the plan_source type separately"),
    M("M82", "config.py", '    plan_source = "terraform"\n    if "plan_source" in data:',
      '    plan_source = "opentofu"\n    if "plan_source" in data:', "config default plan_source is opentofu"),
    M("M83", "config.py", '    plan_source: str = "terraform"\n', '    plan_source: str = "opentofu"\n',
      "Config dataclass default plan_source is opentofu"),
    M("M84", "cli.py", "plan_source=config.plan_source", 'plan_source="terraform"',
      "CLI ignores plan_source from the config"),
    M("M85", "cli.py", "plan_source=config.plan_source", 'plan_source="opentofu"',
      "CLI always uses opentofu mode"),
    M("M86", "parser.py", "return parse_plan(data, plan_source)", "return parse_plan(data)",
      "load_plan drops the mode"),
    M("M87", "parser.py", 'PLAN_SOURCES = ("terraform", "opentofu")', 'PLAN_SOURCES = ("terraform", "opentofu", "tofu")',
      "an extra plan_source value is accepted"),
    M("M88", "parser.py", "        if not isinstance(errored, bool):\n            raise PlanError(\"'errored' must be a present boolean",
      "        if errored is None:\n            raise PlanError(\"'errored' must be a present boolean",
      "opentofu mode only rejects a missing errored (wrong types pass)"),
]

# Mutants that cannot change observable behaviour. id -> reason.
EQUIVALENT: dict[str, str] = {
    "M81": "non-string values can never equal either allowed string, so the `in PLAN_SOURCES` "
           "membership test already rejects them; the separate isinstance check is defensive only.",
    "M73": "an empty actions list is still refused (exit 2) by the KNOWN_ACTIONS membership check right "
           "after it; only the error message differs.",
    "M51": "argparse only ever calls exit() with status 0 (help) or 2 (usage error), so the "
           "`in (0, EXIT_ERROR)` guard never changes a value; it is defensive only.",
}


def tree_hash(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def run_pytest(src_copy: Path, timeout: int) -> tuple[str, str]:
    """Run the suite against src_copy. Returns (status, detail); status is PASS / FAIL / TIMEOUT / ERROR."""
    env = dict(
        os.environ,
        PYTHONPATH=str(src_copy),
        PLANGUARD_SRC=str(src_copy),
        PLANGUARD_FUZZ_N="4000",
        PYTHONDONTWRITEBYTECODE="1",
    )
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider",
           "-o", f"pythonpath={src_copy}", "tests"]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"no result within {timeout}s"
    out = proc.stdout + proc.stderr
    if proc.returncode == 0:
        return "PASS", ""
    failed = re.search(r"^(?:FAILED|ERROR) (\S+)", out, re.MULTILINE)
    if proc.returncode in (1, 2, 3, 4):
        return "FAIL", failed.group(1) if failed else f"pytest exit {proc.returncode}"
    return "ERROR", f"pytest exit {proc.returncode}"


def run_mutant(mut: Mutation, timeout: int) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix=f"planguard-mut-{mut.id}-") as tmp:
        copy = Path(tmp) / "src"
        shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        target = copy / "planguard" / mut.file
        text = target.read_text(encoding="utf-8")
        count = text.count(mut.old)
        if count != 1:
            return "INVALID", f"pattern occurs {count} times in {mut.file} (need exactly 1)"
        target.write_text(text.replace(mut.old, mut.new), encoding="utf-8")
        try:
            py_compile.compile(str(target), cfile=str(Path(tmp) / "x.pyc"), doraise=True)
        except py_compile.PyCompileError as exc:
            return "INVALID", f"mutant does not compile: {exc.msg}"
        status, detail = run_pytest(copy, timeout)
    if status == "PASS":
        return "SURVIVED", ""
    if status in ("FAIL", "TIMEOUT"):
        return "KILLED", detail if status == "FAIL" else f"timeout ({detail})"
    return "INVALID", detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--only", nargs="*", help="run only these mutation ids")
    ap.add_argument("--timeout", type=int, default=300, help="per-mutant test timeout in seconds")
    args = ap.parse_args(argv)

    ids = [m.id for m in MUTATIONS]
    if len(ids) != len(set(ids)):
        print("duplicate mutation ids", file=sys.stderr)
        return 2
    selected = [m for m in MUTATIONS if not args.only or m.id in args.only]
    before = tree_hash(SRC)

    # Baseline: the unmutated copy must pass, otherwise "killed" would mean nothing.
    with tempfile.TemporaryDirectory(prefix="planguard-mut-base-") as tmp:
        copy = Path(tmp) / "src"
        shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        status, detail = run_pytest(copy, args.timeout)
    if status != "PASS":
        print(f"baseline (unmutated copy) did not pass: {status} {detail}", file=sys.stderr)
        return 2
    print(f"baseline OK; running {len(selected)} mutants with {args.jobs} workers ...", file=sys.stderr)

    results: dict[str, tuple[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_mutant, m, args.timeout): m for m in selected}
        for fut in concurrent.futures.as_completed(futures):
            m = futures[fut]
            results[m.id] = fut.result()
            print(f"  {m.id} {results[m.id][0]}", file=sys.stderr)

    if tree_hash(SRC) != before:
        print("ERROR: the real src/ changed during the run (this must never happen)", file=sys.stderr)
        return 2

    rows = []
    for m in selected:
        status, detail = results[m.id]
        if status == "SURVIVED" and m.id in EQUIVALENT:
            status, detail = "EQUIVALENT", EQUIVALENT[m.id]
        rows.append((m.id, f"{m.file}: {m.desc}", status, detail))
    w1 = max(len(r[1]) for r in rows)
    print(f"\n{'ID':<5} {'MUTATION':<{w1}}  {'RESULT':<10} KILLED BY / NOTE")
    print(f"{'-' * 5} {'-' * w1}  {'-' * 10} {'-' * 40}")
    for mid, desc, status, detail in rows:
        print(f"{mid:<5} {desc:<{w1}}  {status:<10} {detail}")
    count = lambda s: sum(1 for r in rows if r[2] == s)  # noqa: E731
    killed, surv, equiv, invalid = count("KILLED"), count("SURVIVED"), count("EQUIVALENT"), count("INVALID")
    scored = killed + surv
    print(f"\n{len(rows)} mutants: {killed} killed, {surv} survived, {equiv} equivalent (explained), {invalid} invalid"
          + (f"; mutation score {100 * killed / scored:.0f}% (excluding equivalents)" if scored else ""))
    if any(r[0] == "M00" and r[2] != "KILLED" for r in rows):
        print("ERROR: sanity mutant M00 was not killed; tests are not running against the mutated copy", file=sys.stderr)
        return 2
    return 0 if surv == 0 and invalid == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
