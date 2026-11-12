# planfence (working name)

Deterministic guardrail for Terraform/OpenTofu plans. It reads the JSON from
`terraform show -json <planfile>` and checks it against a small contract file.
No network, no AI, no cloud credentials. Standard library only (Python >= 3.11).

## Why
AI-generated infrastructure code often passes `terraform validate` but makes
changes nobody intended: deletes, replacements, changes outside the intended
scope. planfence answers one question before apply:
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
- Entry point is `planfence.__main__.run` (used by `python -m planfence` and the
  console script). It imports cli lazily and maps import errors, KeyboardInterrupt,
  stray SystemExit, invalid return values and a failing final flush to 2.
  After a failed stdout it redirects fd 1 to the null device (otherwise Python
  exits with 120 at shutdown).
- cli.main() decides the result first, then writes it. It never raises for
  ordinary failures and returns only 0, 1 or 2 (argparse help -> 0, usage error -> 2).

## Input facts (verified against Terraform 1.17.2 and OpenTofu 1.13.9, see tests/fixtures/README.md)
- Top level: `format_version`, `errored`, `complete`, `resource_changes` (list)
- `format_version`: accept any string matching `^1\.\d+$` (seen: "1.2").
  Anything else -> exit 2. Minor versions only add backward-compatible
  properties, so IGNORE unknown properties; reject other major versions.
  Verified with OpenTofu 1.13.9 and Terraform 1.17.2 on 16 scenarios: both give
  format_version "1.2" and identical `actions` lists (tests/fixtures/opentofu vs real).
- plan_source (config key, default "terraform"; anything else -> exit 2):
  - "terraform" (strict): `errored` and `complete` must be present booleans.
    Exit 2 if `errored` is true or `complete` is not true.
  - "opentofu": OpenTofu 1.13.9's plan JSON has no `complete` and no `applyable`.
    `errored` is still required (boolean, false). `complete` is optional; if
    present it must be boolean true (false or wrong type -> exit 2).
  - Both modes: do NOT require `applyable` (false for a valid no-op plan). `errored`
    (and `complete`) also tell a plan file apart from a state file.
  - The mode is passed explicitly: config -> load_plan(path, plan_source=...) -> parse_plan.
  - Terraform sets `complete: false` for plans made with -target (verified with a
    real fixture, tests/fixtures/partial/terraform). OpenTofu `-target` and
    `-exclude` plans carry NO signal at all (same keys and flags as a full plan;
    tests/fixtures/partial/opentofu), so OpenTofu partial plans cannot be
    recognised: documented limitation. The trusted workflow must run `tofu plan`
    itself without -target/-exclude (planfence cannot enforce it).
- `resource_changes` is ABSENT (not empty) when nothing is planned (verified:
  empty config, and data sources read at plan time). Treat absent as zero
  changes ONLY when format_version is known, errored is false and complete
  is true. If present, it must be a list.
- Content planfence cannot judge -> exit 2 (fail closed), error messages name
  the field only, never values:
  - top-level `action_invocations` present and not an empty list
  - top-level `deferred_changes` present and not an empty list
  - any resource_changes entry with a non-null `change.importing` (an import
    happens in addition to the actions, e.g. import + no-op). Verified with real
    fixtures (import, import_replace, import_noop).
  Absent, `[]` (and `importing: null`) are fine.
- String limit: `address`, `type` and `deposed` longer than 4096 characters
  (characters, not bytes) -> exit 2; message names field and index only.
- `previous_address` (moved blocks; verified with real fixtures tests/fixtures/moved): when
  present it must be a non-empty string of at most 4096 characters, else exit 2 (message names
  field and index only). Equal to `address` -> not a move (ignored). Change.previous_address is
  None for non-moves; `Change.is_move`.
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
- Moves: the scope rule treats every move as a change, whatever its action (even ["no-op"]);
  for a moved resource BOTH its address and its `previous_address` must match the allowlist.
  Non-moves unchanged. Budget unchanged: a pure move is not counted as a change
  (a move + update counts once). no-delete / no-replace / stateful-protect judge actions only.
  Real bypass that existed (fixture moved_update): only the NEW address allowed passed although
  the updated resource lived at the OLD address. Across PRs: each plan is judged on its own;
  the allowlist is the contract.
- Count only `resource_changes`; never `resource_drift` or `output_changes`: this is the behaviour by default
  (state_only_plans = "allow"); with "refuse" they only make a zero-change plan exit 2 (see state_only_plans).
- Identify and sort entries by `address`, then `deposed`, then `previous_address`.

## Address matching
Custom matcher, NOT fnmatch. Only `*` (any characters, including dots and
quotes) and `?` (one character) are wildcards. `[` and everything else is
literal. Case-sensitive. Tests must cover `[0]` and `["key"]` addresses.

## Rules for v0.1
Each rule needs: one failing fixture, one passing fixture, unit tests.
1. no-delete: actions is exactly ["delete"]
2. no-replace: actions contains both "delete" and "create"
3. scope: only addresses matching allowed patterns (see Address matching)
   may change; "no-op" and "read" entries are ignored. Moves (previous_address):
   for a moved resource BOTH its address and its `previous_address` must match the allowlist,
   even when the action is "no-op".
4. stateful-protect: delete, replace or forget of configured stateful types
   is `critical`
5. budget: `max_changes` (total changes) and `max_creates_per_type`
   (creates per resource type). Violated when the count is strictly greater
   than the limit.

## Config: planfence.toml (tomllib, strict)
```toml
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]
plan_source = "terraform"       # optional: "terraform" (default, strict) | "opentofu"

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
- Config path: `--config` (default `./planfence.toml`); a missing file -> exit 2.
  Config file size limit 1 MiB.
- What counts as "stateful" is the USER's decision (security behaviour): never
  add a built-in default list without asking.

## Output
- Text (default) and `--format json`.
- Print ONLY: rule id, severity (`error`, or `critical` for stateful-protect),
  address, type, action, the `deposed` key when present, and `moved-from=ADDR` (the
  previous address of a moved entry, ASCII-escaped). JSON violation objects always have
  `previous_address` (null when none). For budget violations also the integers `count`
  and `limit`. Nothing else.
- NEVER print attribute values (they may contain secrets).
- Deterministic ordering (address, then deposed key, then rule, then type).
- Text is pure ASCII: control and non-ASCII characters in addresses/types are
  escaped (no terminal injection, no UnicodeEncodeError). Text lines:
  `[error] no-delete aws_x.y (aws_x) delete` (+ ` deposed=KEY`), then
  `planfence: FAIL - N violation(s)` or
  `planfence: OK - N change(s) checked, all enabled rules passed`.
- Budget violations have no address (JSON `address`/`type`/`action` may be null):
  - text: `[error] budget total changes 6 > max_changes 5`
  - text: `[error] budget creates of aws_instance 3 > limit 2`
  - JSON: the same violation object plus integer `count` and `limit`.
    Non-budget violations do NOT have these keys.
- JSON document: `{"result": "pass"|"fail", "changes_checked": N,
  "violations": [{rule, severity, address, type, action, deposed}, ...]}`.
  On exit 2 with `--format json`, stdout is `{"result": "error", "message": ...}`
  and the message is also written to stderr. Error messages never contain plan values.

## Using planfence safely in CI (must stay in README too; tests/test_docs.py checks both)
- Contract from a protected place: if the same pull request can edit planfence.toml
  its author can weaken the contract. Load it from the base branch (the example
  workflow uses `git show origin/<base>:planfence.toml`) or protect it with CODEOWNERS
  and branch protection. Same for the workflow file and the planfence version.
- Apply exactly the plan file you checked: `terraform apply tfplan` with the same
  binary plan that `terraform show -json` converted; never re-plan between check and
  apply (record/verify a sha256 of tfplan). Plan files can contain secrets: no artifacts.
- The example workflow only CHECKS (tests/test_workflow_contract.py enforces: same `tfplan`
  file in plan/show/checksum/commented apply, order plan -> show -> checksum -> contract
  load -> check, no second plan, no apply, timeout-minutes on every job; and executes the
  contract step against a real git origin). An apply step must use the same file, ideally in
  the same job, gated by a protected environment with required reviewers. The checksum only
  detects accidental change inside the trusted job. Moving the binary plan between jobs means
  uploading an artifact, and it can contain secrets.
- planfence itself reads only the plan file (and its config), but creating the plan
  may contact providers, read and refresh state, and run data sources (e.g. `external`),
  before planfence starts. Never claim planfence "never touches state or providers" for the
  whole CI run.
- Versions: planfence does not check `terraform_version`, provider versions or provider
  identities. Pin Terraform/OpenTofu and providers in CI (.terraform.lock.hcl,
  required_version, required_providers); plans from other versions are unverified.
- The workflow file and the planfence version are part of the trust boundary: they decide what
  is checked. Protect them like the contract with CODEOWNERS (examples/CODEOWNERS: /planfence.toml, /.github/,
  /CODEOWNERS itself) plus branch protection requiring code-owner review.
- planfence does not see provisioners (local-exec, remote-exec), data source side
  effects (e.g. the `external` data source runs at plan time), or what a create actually
  does; it never judges attribute values.
- A pass is not an approval: exit 0 means "inside the contract", a filter in front of
  human review, not a replacement for it.

## Plan modes and flags (verified with real fixtures, tests/fixtures/modes + partial)
planfence sees only the plan JSON, not the command line.
- -target: Terraform sets `complete: false` -> refused (exit 2). OpenTofu: no signal,
  cannot be recognised (see plan_source above); run `tofu plan` without -target/-exclude.
- -refresh-only: NO `resource_changes` key -> "0 changes", exit 0 even with the strictest
  contract. With real drift (remote state output changed) the only trace is `output_changes`
  (Terraform also `applyable: true`), which is not checked by default (state_only_plans = "allow";
  "refuse" exits 2). A pass is not "nothing changes"; applying it updates state. `resource_drift`
  could not be produced offline: unverified.
- -replace=ADDR: `["delete","create"]` with `action_reason: replace_by_request` and no
  `replace_paths`; same `actions` as a config-driven replacement. planfence cannot know the
  flag: keep `no-replace` enabled if replacements are not allowed (no-delete does not fire).

## state_only_plans (config key, default "allow"; anything else -> exit 2)
- `state_only_plans = "refuse"`: exit 2 when resource_changes has no resource changes (no entry other than no-op /
  read; moves are NOT counted as changes) AND (an output_changes entry with actions other
  than exactly ["no-op"], OR a non-empty resource_drift, OR at least one moved entry = a
  pure move). Plans that also change resources are never refused by this key (even
  malformed output_changes/resource_drift are ignored then). In the zero-change case a
  non-object output_changes / non-list resource_drift is refused too. Messages name fields only.
- Verified with real plans (tests/fixtures/outputs, modes, moved; both tools): unchanged output
  = ["no-op"] (not refused); output changing with a resource change (not refused); refresh-only
  with real output drift (refused); output only added, `output_added_only` (refused); pure move
  (refused) - all of this only with state_only_plans = "refuse", by default they pass.
  `resource_drift` only via the hand-made tests/fixtures/handmade/resource_drift.json
  (real drift cannot be produced offline).
- Recommended for any CI gate; default stays "allow" so existing contracts keep working.
- Passed explicitly: config -> load_plan(path, plan_source=..., state_only_plans=...) -> parse_plan.

## README: Related tools policy
The README's Related tools section may only state claims taken from the other tools' public documentation, each date-stamped (never superlatives or "better than", and never a claim that another tool lacks a feature unless that was verified); its links are allowlisted in tests/test_docs.py, and you must update the date whenever the section is edited.

## NOT verified / known gaps (keep this list honest)
- OpenTofu: verified only for 1.13.9 and only on the 16 terraform_data scenarios (same as
  Terraform 1.17.2 except no `complete`/`applyable`). OpenTofu partial plans (-target /
  -exclude) cannot be recognised (no signal): documented limitation. Other OpenTofu or
  Terraform versions and real providers are unverified.
- `deposed`: supported (sorting, display, all rules) but no real plan fixture
  has one; tests use hand-built plans.
- Python 3.11 and 3.12: untested locally (only 3.13). CI matrix is the first check.
- `action_invocations` and `deferred_changes`: the exit-2 rule is tested only
  with HAND-MADE fixtures (tests/fixtures/handmade/); the real JSON shape of
  their entries is unverified (not producible with Terraform 1.17.2 / OpenTofu 1.13.9 + built-in
  resources). `importing` IS verified with real fixtures.
- Action lists not seen in real output (e.g. ["create","forget"]) -> exit 2.
- `output_changes` and `resource_drift` are not judged by default (state_only_plans = "allow"); with
  "refuse" they only make a zero-change plan exit 2. Real
  `resource_drift` could not be produced offline (no fixture); real output drift under
  -refresh-only was (tests/fixtures/modes) and passes with exit 0.
- The GitHub Actions workflows have not run on GitHub; the example workflow's
  third-party action versions and the install URL are unverified.

## Hard constraints
- Runtime dependencies: none (stdlib only). Dev dependency allowed: pytest.
- All scripts must be cross-platform (Python, not bash/PowerShell).
- Never use cloud providers or real credentials. The ONLY providers fixture generation may
  download through `init` are the non-cloud hashicorp/random, hashicorp/local and hashicorp/null;
  record their exact versions in tests/fixtures/README.md when a fixture uses one. Only
  providers/terraform/local_file_replace does (hashicorp/local and hashicorp/random; versions in its
  PROVIDERS.txt); every other fixture uses only the built-in terraform_data and terraform_remote_state.
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
- Without installing: `PYTHONPATH=src .venv/bin/python -m planfence`.
- Installed editable in .venv: `.venv/bin/planfence` (entry point `planfence.__main__:run`).
- Tests: `.venv/bin/python -m pytest -q`.

## Workflow
- Tests first. Small commits (one rule or one step per commit).
- Run the full test suite before every commit.
- Quality gates (dev tools only, configured in pyproject.toml, run by a separate CI job with
  exact pinned versions): `.venv/bin/ruff check src` and `.venv/bin/mypy` (strict, src only).
  Keep both clean; fix reports without changing behaviour (if a tool wants a behaviour change,
  stop and list it). tests/test_quality_gates.py runs them as part of the suite.
- tests/test_fuzz.py: deterministic differential fuzzing of cli.main against an
  independent oracle written inside the test (exit code must equal the oracle's;
  no planted secret in output; no hidden "internal error"). Env PLANFENCE_FUZZ_N
  sets the iteration count (default 20000). `gen_case(i)` replays iteration i.
- scripts/mutation_check.py: 126 source mutations run against temp copies of src/ (never
  edits src/). Every mutant must be KILLED or listed in its EQUIVALENT dict with a
  reason; add a test for any survivor. Run it after changing rules/parser/config/cli.
  Also run it with `--pytest-args "tests --ignore=tests/test_fuzz.py"`: every mutant must
  be killed by the deterministic tests alone, not by luck in the fuzzer.
- Timing policy: no wall-clock assertions in tests (slow CI runners must not fail them; a
  30 s bound once failed on a 7x slower runner and, worse, missed real regressions). Instead:
  operation counts (how often glob_match / the matcher constructor / the sort key run) and the
  scaling ratio (4x the input must take < 9x the CPU time; linear ~4, quadratic ~16; CPU time, not
  wall time, with 3 attempts), all in tests/test_scaling.py and tests/test_scope_performance.py. The only
  time limit left is `hang_guard` (tests/timing.py): a 15-minute SIGALRM watchdog against endless
  hangs, never a speed test. tests/timing.py is the only test module allowed to read a clock
  (a doc test enforces it). Mutants M122-M125 are correct-but-slow variants that these tests must kill.
  CI/subprocess timeouts (build 600 s, install 300 s, mutation 300 s, jobs 10 min) are hang guards.
- Tests honour PLANFENCE_SRC (see tests/conftest.py SRC) so subprocess tests can
  run against another copy of src/.
- When unsure about Terraform behaviour, say so and ask me to verify with a
  real `terraform` command. Never guess.
- README must state what planfence does NOT claim: it does not check security,
  cost, configuration correctness, or live infrastructure. Nor, by default
  (state_only_plans = "allow"), `output_changes` or `resource_drift`.
