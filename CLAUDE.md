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

## Input facts (VERIFY against real plan files before relying on them)
- Top level: `format_version`, `resource_changes` (list)
- Each item: `address`, `type`, `change.actions`
  - examples: ["no-op"], ["create"], ["update"], ["delete"],
    ["delete","create"] or ["create","delete"] for a replacement
Do not assume more than this. If a field is missing, fail closed (exit 2).

## Rules for v0.1
Each rule needs: one failing fixture, one passing fixture, unit tests.
1. no-delete: actions is exactly ["delete"]
2. no-replace: actions contains both "delete" and "create"
3. scope: only addresses matching allowed patterns (fnmatch) may change;
   "no-op" entries are ignored
4. stateful-protect: delete or replace of configured stateful types is `critical`
5. budget: max total changes; max creates per resource type

Config: `planguard.toml`, parsed with `tomllib`.
Contains: enabled rules, allowlist patterns, stateful types, budget numbers.
Unknown keys -> exit 2.

## Output
- Text (default) and `--format json`.
- Print ONLY address, type, action and rule id.
- NEVER print attribute values (they may contain secrets).
- Deterministic ordering (sort by address).

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

## Workflow
- Tests first. Small commits (one rule or one step per commit).
- Run the full test suite before every commit.
- When unsure about Terraform behaviour, say so and ask me to verify with a
  real `terraform` command. Never guess.
- README must state what planguard does NOT claim: it does not check security,
  cost, configuration correctness, or live infrastructure.
