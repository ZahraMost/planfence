# planguard

A deterministic guardrail for Terraform / OpenTofu plans. It reads the JSON from
`terraform show -json <planfile>` and checks it against a small contract file,
answering one question before `apply`: **does this plan stay inside the contract?**

- No network, no AI, no cloud credentials.
- Python >= 3.11, standard library only (pytest is a dev-only dependency).
- Fail-closed: if the plan is not fully understood, planguard never says "OK".

## Why

AI-generated infrastructure code often passes `terraform validate` but makes
changes nobody intended: deletes, replacements, changes outside the intended
scope. planguard catches those cheaply and repeatably.

## Quick start

```sh
# from a checkout (no install needed)
PYTHONPATH=src python -m planguard plan.json --config planguard.toml

# or install it (provides the `planguard` command)
pip install .

terraform plan -out=tfplan
terraform show -json tfplan > plan.json
planguard plan.json --config planguard.toml
```

```
$ planguard plan.json --config planguard.toml
[error] no-replace terraform_data.example (terraform_data) delete,create
[critical] stateful-protect terraform_data.example (terraform_data) delete,create
planguard: FAIL - 2 violation(s)
```

Options: `--config PATH` (default `./planguard.toml`; a missing file is an error),
`--format text|json` (default `text`).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | plan parsed and every enabled rule passed |
| 1 | at least one rule violated |
| 2 | anything else: unreadable or invalid plan, unknown `format_version`, errored or incomplete plan, bad config, bad arguments, a crash, or failure to write the output (closed pipe, full disk) |

## Rules (v0.1)

| Rule id | Violated when | Severity |
|---|---|---|
| `no-delete` | a resource's actions are exactly `["delete"]` (a replacement or forget is not a delete) | error |
| `no-replace` | actions contain both `delete` and `create` (either order) | error |
| `scope` | a change's address matches none of the allowed patterns | error |
| `stateful-protect` | a configured stateful *type* is deleted, replaced or forgotten | critical |
| `budget` | total changes exceed `max_changes`, or creates of a type exceed its limit | error |

How changes are counted:

- A **change** is any `resource_changes` entry whose actions are not `["no-op"]` and not `["read"]`.
- `read` (a data source read) is never a change: it is ignored by `scope` and `budget`.
- `forget` (a `removed` block with `destroy = false`) **is** a change: it counts toward the budget,
  is subject to `scope`, and is flagged by `stateful-protect`, but not by `no-delete`.
- A replacement counts as **one** change and **one** create for its type.
- Plans containing things planguard cannot judge are **refused (exit 2)** rather than passed:
  a non-empty top-level `action_invocations` (Terraform actions) or `deferred_changes`, and any
  change with an `importing` object (an `import` block, even when the action is a no-op).
  Absent or empty is fine.
- An absent `resource_changes` key means "nothing planned" (Terraform omits it for empty plans),
  accepted only when `format_version` is known, `errored` is false and `complete` is true.

## Configuration: `planguard.toml`

See [examples/planguard.toml](examples/planguard.toml).

```toml
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]

[scope]
allow = ["module.app.*", 'aws_instance.web["blue"]']

[stateful]
types = ["aws_db_instance", "aws_s3_bucket"]

[budget]
max_changes = 10
[budget.max_creates_per_type]
aws_instance = 2
```

- **Unknown keys are an error (exit 2)**, at every level. So are wrong types, unknown rule ids,
  a missing section for an enabled rule, and a section for a rule that is not enabled.
- **Address patterns** are not fnmatch: only `*` (any characters, including dots and quotes) and
  `?` (one character) are wildcards. `[`, `]` and everything else are literal, matching is
  case-sensitive, so `aws_instance.web[0]` and `aws_instance.web["blue"]` mean exactly that.
- **Warning: a lone `*` in `scope.allow` allows everything** (every address matches it), which makes
  the `scope` rule meaningless. Likewise `module.*`-style prefixes allow everything under that
  prefix. Use the narrowest patterns you can, and review any `*` in the allowlist like a firewall
  rule that says "allow all".
- **Limits:** at most 500 `scope.allow` patterns, each at most 512 characters (more is exit 2).
  Patterns without wildcards and patterns of the form `prefix*` are matched with fast paths
  (same semantics); other wildcard patterns use a linear-scan matcher.
- **Stateful types** are matched exactly (no wildcards). planguard ships **no default list**:
  deciding what is stateful in your infrastructure is your call.

## Output

Text and `--format json` print **only** rule id, severity, address, type and action (plus the
`deposed` key when present). Attribute values are never printed, because they may contain
secrets. Order is deterministic (address, then deposed key, then rule). Control and non-ASCII
characters in addresses and types are escaped in text output.

`budget` violations are aggregate (no address) and show the numbers instead:

```
[error] budget total changes 6 > max_changes 5
[error] budget creates of aws_instance 3 > limit 2
```

In JSON, budget violations (and only those) have integer `count` and `limit` fields:

```json
{"result": "fail", "changes_checked": 6, "violations": [
  {"rule": "budget", "severity": "error", "address": null, "type": null, "action": null,
   "deposed": null, "count": 6, "limit": 5},
  {"rule": "no-replace", "severity": "error", "address": "terraform_data.example",
   "type": "terraform_data", "action": "delete,create", "deposed": null}]}
```

With `--format json`, errors (exit 2) also print `{"result": "error", "message": "..."}` on stdout
and the message on stderr.

## What planguard does NOT claim

planguard is a narrow guardrail, not a safety guarantee. It does **not** check:

- **security**: it does not know whether a change opens a port, widens a policy, or leaks data;
- **cost**: it does not estimate or limit spend (the budget counts changes, not money);
- **configuration correctness**: a plan can stay inside the contract and still be wrong;
- **live infrastructure**: it only reads the plan file, never your cloud, state, or provider;
- **`output_changes`** or **`resource_drift`** in the plan JSON (v0.1 inspects `resource_changes` only).
  Example: a plan whose only changes are a deleted output (`output_changes`) or an out-of-band
  deleted database found during refresh (`resource_drift`), with an empty `resource_changes`,
  **passes with exit 0**;
- **attribute-level changes**: an `update` is an `update`, whatever it changes.

A passing result means "this plan, as written, matches the contract you wrote", nothing more.
The contract is only as good as the allowlist, stateful types and budgets you configure.

## Verified against

Plan JSON shapes were verified with real `terraform show -json` output from **Terraform 1.17.2**
(`format_version` "1.2") using the built-in `terraform_data` resource; see
[tests/fixtures/README.md](tests/fixtures/README.md). **OpenTofu has not been verified.** Any
`format_version` of the form `1.x` is accepted and unknown properties are ignored, but any action
list not seen in real output is rejected (exit 2) rather than guessed.

Not verified: OpenTofu; plans containing `deposed` objects; `moved` blocks (planguard judges only
`actions` and ignores `previous_address`); the real JSON shape of `action_invocations` and
`deferred_changes` (refused via hand-made fixtures only); Python 3.11 and 3.12
(developed on 3.13; CI covers all three). See the "NOT verified" list in [CLAUDE.md](CLAUDE.md).

## Security notes

- The plan JSON contains attribute values, possibly secrets. Don't commit it or upload it as a
  CI artifact; delete it after the check.
- Input is treated as untrusted: 128 MiB size limit, resource `address`, `type` and `deposed`
  strings limited to 4096 characters (longer is exit 2, the value is never echoed), duplicate JSON keys rejected, no `eval`/`exec`,
  no network access.

## CI

Run planguard with a job timeout (e.g. `timeout-minutes: 5` in GitHub Actions) so an unexpectedly
huge or adversarial plan can never hang your pipeline. Both workflows in this repo set one.

- [.github/workflows/ci.yml](.github/workflows/ci.yml) runs the test suite on Python 3.11-3.13.
- [examples/github-actions/planguard-example.yml](examples/github-actions/planguard-example.yml)
  shows how to run planguard on a plan in your own pipeline (copy it into your repo; it is not
  active here).

## Development

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest

# regenerate real fixtures (needs terraform or tofu; uses only built-in terraform_data,
# throwaway temp directories and local state, creates nothing real)
.venv/bin/python scripts/make_fixtures.py --binary "$(command -v terraform)" [scenario ...]
```

`scripts/make_fixtures.py` is the only place `terraform apply` is ever run, and only inside its
own temporary directories.

## License

[MIT](LICENSE), copyright (c) 2026 ZahraMost.
