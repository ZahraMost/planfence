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
5. budget: max total changes; max creates per resource type

Config: `planguard.toml`, parsed with `tomllib`.
Contains: enabled rules, allowlist patterns, stateful types, budget numbers.
Unknown keys -> exit 2.

## Output
- Text (default) and `--format json`.
- Print ONLY address, type, action and rule id.
- NEVER print attribute values (they may contain secrets).
- Deterministic ordering (sort by address, then deposed key).

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

## Workflow
- Tests first. Small commits (one rule or one step per commit).
- Run the full test suite before every commit.
- When unsure about Terraform behaviour, say so and ask me to verify with a
  real `terraform` command. Never guess.
- README must state what planguard does NOT claim: it does not check security,
  cost, configuration correctness, or live infrastructure. Nor, in v0.1,
  `output_changes` or `resource_drift`.
