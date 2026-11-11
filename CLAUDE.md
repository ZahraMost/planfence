# planguard (working name)

Deterministic guardrail for Terraform/OpenTofu plans. It reads the JSON from
`terraform show -json <planfile>` and checks it against a small contract file.
No network, no AI, no cloud credentials. Standard library only (Python >= 3.11).

## Why
AI-generated infrastructure code often passes `terraform validate` but makes
changes nobody intended: deletes, replacements, changes outside the intended
scope. planguard answers one question before apply:
"does this plan stay inside the contract?"

## Exit codes (fail-closed)
- 0: plan parsed and every rule passed
- 1: at least one rule violated
- 2: anything else (bad JSON, unknown format_version, unreadable file, crash)
Never print "OK" or exit 0 if the plan was not fully understood.
Hardening (tested in tests/test_exit_hardening.py):
- ANY failure ends in exit 2, never 0 or 1: bad plan/config/arguments,
  unexpected exceptions, and failures while printing (BrokenPipeError,
  UnicodeEncodeError, OSError). If the result cannot be delivered, exit 2.
- Entry point is `planguard.__main__.run` (used by `python -m planguard` and the
  console script). It imports cli lazily and maps import errors, KeyboardInterrupt,
  stray SystemExit, invalid return values and a failing final flush to 2.
  After a failed stdout it redirects fd 1 to the null device (otherwise Python
  exits with 120 at shutdown).
- cli.main() decides the result first, then writes it. It never raises for
  ordinary failures and returns only 0, 1 or 2 (argparse help -> 0, usage error -> 2).

## Input facts (verified against Terraform 1.17.2, see tests/fixtures/README.md)
- Top level: `format_version`, `errored`, `complete`, `resource_changes` (list)
- `format_version`: accept any string matching `^1\.\d+$` (seen: "1.2").
  Anything else -> exit 2. Minor versions only add backward-compatible
  properties, so IGNORE unknown properties; reject other major versions.
  OpenTofu's format_version is NOT verified yet.
- `errored` and `complete` must be present booleans. Exit 2 if `errored` is
  true or `complete` is not true. Do NOT require `applyable` (it is false for
  a valid no-op plan). These keys also tell a plan file apart from a state file.
- `resource_changes` is ABSENT (not empty) when nothing is planned (verified:
  empty config, and data sources read at plan time). Treat absent as zero
  changes ONLY when format_version is known, errored is false and complete
  is true. If present, it must be a list.
- Content planguard cannot judge -> exit 2 (fail closed), error messages name
  the field only, never values:
  - top-level `action_invocations` present and not an empty list
  - top-level `deferred_changes` present and not an empty list
  - any resource_changes entry with a non-null `change.importing` (an import
    happens in addition to the actions, e.g. import + no-op). Verified with real
    fixtures (import, import_replace, import_noop).
  Absent, `[]` (and `importing: null`) are fine.
- String limit: `address`, `type` and `deposed` longer than 4096 characters
  (characters, not bytes) -> exit 2; message names field and index only.
- Each item: `address`, `type`, `change.actions`; `deposed` key when present
  (not yet seen in a real fixture).
- Known `actions` lists (all verified with real fixtures):
  ["no-op"], ["create"], ["read"], ["update"], ["delete"], ["forget"],
  ["delete","create"] or ["create","delete"] for a replacement.
  Any other value, or an empty/non-list `actions`, -> exit 2.
- Use `actions` only, never `action_reason` (a forget carries
  `delete_because_no_resource_config`, the same as a real delete).
- Addresses can contain `[0]`, `["key"]` and `module.x.` prefixes.
Do not assume more than this. If a required field is missing, fail closed (exit 2).

## Action semantics
- A "change" is any `resource_changes` entry whose actions are not ["no-op"]
  and not ["read"].
- "read" is not a change: excluded from budget and scope.
- "forget" is a change: counts toward budget, subject to scope, flagged by
  stateful-protect for stateful types; NOT flagged by no-delete.
- A replacement counts as 1 change and as 1 create for its type.
- Count only `resource_changes`; never `resource_drift` or `output_changes`.
- Identify and sort entries by `address` plus `deposed` when present.

## Address matching
Custom matcher, NOT fnmatch. Only `*` (any characters, including dots and
quotes) and `?` (one character) are wildcards. `[` and everything else is
literal. Case-sensitive. Tests must cover `[0]` and `["key"]` addresses.

## Rules for v0.1
Each rule needs: one failing fixture, one passing fixture, unit tests.
1. no-delete: actions is exactly ["delete"]
2. no-replace: actions contains both "delete" and "create"
3. scope: only addresses matching allowed patterns (see Address matching)
   may change; "no-op" and "read" entries are ignored
4. stateful-protect: delete, replace or forget of configured stateful types
   is `critical`
5. budget: `max_changes` (total changes) and `max_creates_per_type`
   (creates per resource type). Violated when the count is strictly greater
   than the limit.

## Config: planguard.toml (tomllib, strict)
```toml
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]

[scope]                         # required iff "scope" is enabled
allow = ["module.app.*", 'aws_instance.web["blue"]']

[stateful]                      # required iff "stateful-protect" is enabled
types = ["aws_db_instance"]     # exact type names, no wildcards, NO default list

[budget]                        # required iff "budget" is enabled
max_changes = 10                # optional integer >= 0
[budget.max_creates_per_type]   # optional table: type -> integer >= 0
aws_instance = 2
```
- Unknown keys at ANY level -> exit 2 (config keys under max_creates_per_type
  are type names and are free-form). Wrong types, bool where int is expected,
  negative numbers, unknown or duplicate rule ids -> exit 2.
- `enabled_rules` is required and non-empty. A rule that is enabled but lacks
  its section, or a section whose rule is not enabled -> exit 2.
- Empty allow / types lists, an empty [budget], empty strings -> exit 2.
- Limits: at most 500 `scope.allow` patterns, each at most 512 characters
  (counted in characters) -> exit 2 above that. Error messages never echo patterns.
- scope matching uses AddressMatcher: no-wildcard patterns -> set lookup,
  `prefix*` (single trailing `*`, no `?`) -> startswith, everything else ->
  glob_match. Semantics MUST stay identical to glob_match (fuzz-tested).
- glob_match work per call is bounded by pattern length (<= 512) x address length
  (<= 4096, see Input facts). CI `timeout-minutes` is still recommended.
- Config path: `--config` (default `./planguard.toml`); a missing file -> exit 2.
  Config file size limit 1 MiB.
- What counts as "stateful" is the USER's decision (security behaviour): never
  add a built-in default list without asking.

## Output
- Text (default) and `--format json`.
- Print ONLY: rule id, severity (`error`, or `critical` for stateful-protect),
  address, type, action, and the `deposed` key when present. For budget
  violations also the integers `count` and `limit`. Nothing else.
- NEVER print attribute values (they may contain secrets).
- Deterministic ordering (address, then deposed key, then rule, then type).
- Text is pure ASCII: control and non-ASCII characters in addresses/types are
  escaped (no terminal injection, no UnicodeEncodeError). Text lines:
  `[error] no-delete aws_x.y (aws_x) delete` (+ ` deposed=KEY`), then
  `planguard: FAIL - N violation(s)` or
  `planguard: OK - N change(s) checked, all enabled rules passed`.
- Budget violations have no address (JSON `address`/`type`/`action` may be null):
  - text: `[error] budget total changes 6 > max_changes 5`
  - text: `[error] budget creates of aws_instance 3 > limit 2`
  - JSON: the same violation object plus integer `count` and `limit`.
    Non-budget violations do NOT have these keys.
- JSON document: `{"result": "pass"|"fail", "changes_checked": N,
  "violations": [{rule, severity, address, type, action, deposed}, ...]}`.
  On exit 2 with `--format json`, stdout is `{"result": "error", "message": ...}`
  and the message is also written to stderr. Error messages never contain plan values.

## NOT verified / known gaps (keep this list honest)
- OpenTofu: `format_version` and action lists are NOT verified (only Terraform 1.17.2).
- `deposed`: supported (sorting, display, all rules) but no real plan fixture
  has one; tests use hand-built plans.
- Python 3.11 and 3.12: untested locally (only 3.13). CI matrix is the first check.
- `moved` blocks: not verified. planguard ignores `previous_address` and judges
  only `actions`; a pure move may appear as a no-op/update.
- `action_invocations` and `deferred_changes`: the exit-2 rule is tested only
  with HAND-MADE fixtures (tests/fixtures/handmade/); the real JSON shape of
  their entries is unverified (not producible with Terraform 1.17.2 + built-in
  resources). `importing` IS verified with real fixtures.
- Action lists not seen in real output (e.g. ["create","forget"]) -> exit 2.
- `output_changes` and `resource_drift` are deliberately not checked in v0.1.
- The GitHub Actions workflows have not run on GitHub; the example workflow's
  third-party action versions and the install URL are unverified.

## Hard constraints
- Runtime dependencies: none (stdlib only). Dev dependency allowed: pytest.
- All scripts must be cross-platform (Python, not bash/PowerShell).
- Never use cloud providers or real credentials.
- Never run `terraform apply` or `terraform destroy` anywhere, EXCEPT inside
  throwaway temp directories created by scripts/make_fixtures.py that contain
  only the built-in `terraform_data` resource (local state, creates nothing real).
  Real delete/replace/no-op plans need an existing state, so this exception is needed.
- Fixtures that cannot be generated this way (stateful cloud types) are
  hand-written and listed as `handmade` in tests/fixtures/README.md
  until verified against a real plan.
- Treat input as untrusted: size limit, no eval/exec, no network.

## Autonomy
You may run any non-destructive local command needed for the task
(python, pip inside .venv, pytest, git add/commit, terraform init/plan/show
inside the fixture temp directories).
Ask first before: installing system software, git push or adding remotes,
creating GitHub repositories, deleting files outside this project,
or any network use other than `pip install` and Terraform's own init.

## Environment (this machine)
- Use `.venv/bin/python` (Python 3.13). `python3` on this machine is 3.9 - do not use it.
- Terraform is at /opt/homebrew/bin/terraform (may not be on PATH in tool shells).
- Without installing: `PYTHONPATH=src .venv/bin/python -m planguard`.
- Installed editable in .venv: `.venv/bin/planguard` (entry point `planguard.__main__:run`).
- Tests: `.venv/bin/python -m pytest -q`.

## Workflow
- Tests first. Small commits (one rule or one step per commit).
- Run the full test suite before every commit.
- tests/test_fuzz.py: deterministic differential fuzzing of cli.main against an
  independent oracle written inside the test (exit code must equal the oracle's;
  no planted secret in output; no hidden "internal error"). Env PLANGUARD_FUZZ_N
  sets the iteration count (default 20000). `gen_case(i)` replays iteration i.
- Tests honour PLANGUARD_SRC (see tests/conftest.py SRC) so subprocess tests can
  run against another copy of src/.
- When unsure about Terraform behaviour, say so and ask me to verify with a
  real `terraform` command. Never guess.
- README must state what planguard does NOT claim: it does not check security,
  cost, configuration correctness, or live infrastructure. Nor, in v0.1,
  `output_changes` or `resource_drift`.
