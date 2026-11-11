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
| `scope` | a change's address matches none of the allowed patterns; for a moved resource, its address or its `previous_address` matches none | error |
| `stateful-protect` | a configured stateful *type* is deleted, replaced or forgotten | critical |
| `budget` | total changes exceed `max_changes`, or creates of a type exceed its limit | error |

How changes are counted:

- A **change** is any `resource_changes` entry whose actions are not `["no-op"]` and not `["read"]`.
- `read` (a data source read) is never a change: it is ignored by `scope` and `budget`.
- `forget` (a `removed` block with `destroy = false`) **is** a change: it counts toward the budget,
  is subject to `scope`, and is flagged by `stateful-protect`, but not by `no-delete`.
- A replacement counts as **one** change and **one** create for its type.
- **Moves** (`moved` blocks) are described under "Moved resources" below: the `scope` rule treats every
  move as a change, but a pure move is not counted as a change for `budget`.
- Plans containing things planguard cannot judge are **refused (exit 2)** rather than passed:
  a non-empty top-level `action_invocations` (Terraform actions) or `deferred_changes`, and any
  change with an `importing` object (an `import` block, even when the action is a no-op).
  Absent or empty is fine.
- An absent `resource_changes` key means "nothing planned" (Terraform omits it for empty plans),
  accepted only when `format_version` is known, `errored` is false and `complete` is true.

### Moved resources

A resource that a `moved` block renames or relocates appears in `resource_changes` with a
`previous_address`. Verified with real Terraform 1.17.2 and OpenTofu 1.13.9 plans
([tests/fixtures/README.md](tests/fixtures/README.md)): a pure rename or move into a module is
`["no-op"]`, a move together with an input change is `["update"]` at the **new** address.

- **The bypass that existed.** `scope` used to look only at the new address, so a resource outside the
  allowlist could be moved to an allowed address and changed in the same plan. With the real fixture
  `moved_update` and only `terraform_data.new` allowed, planguard passed although the resource being
  updated lived at `terraform_data.old`.
- **The rule.** `scope` treats every move as a change, whatever its action (even `["no-op"]`), and a
  moved entry passes only if both its address and its `previous_address` match the allowlist; otherwise
  it is a `scope` violation. Entries that are not moves are unchanged (no-op and read are still ignored).
- **Everything else is unchanged.** `no-delete`, `no-replace` and `stateful-protect` judge actions only.
  For `budget`, a pure move is not counted as a change (a move with an update counts once).
- **Validation.** `previous_address`, when present, must be a non-empty string of at most 4096
  characters (null, wrong type, empty or longer is exit 2; the message names the field and the entry
  index only). A `previous_address` equal to `address` is not a move and is ignored.
- **Output.** Violations of moved entries show where they came from: text appends `moved-from=ADDR`
  (ASCII-escaped, after `deposed=`), JSON violation objects always have a `previous_address` field
  (`null` when there is none).
- **Across pull requests.** planguard judges one plan at a time. A PR that moves a resource into scope
  and a later PR that changes it are each judged on its own against the allowlist; the contract is the
  allowlist, and a resource inside it may be changed however it got there.

## Configuration: `planguard.toml`

See [examples/planguard.toml](examples/planguard.toml).

```toml
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]
# plan_source = "opentofu"   # optional, default "terraform"; see the OpenTofu section

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
   "deposed": null, "previous_address": null, "count": 6, "limit": 5},
  {"rule": "no-replace", "severity": "error", "address": "terraform_data.example",
   "type": "terraform_data", "action": "delete,create", "deposed": null, "previous_address": null}]}
```

With `--format json`, errors (exit 2) also print `{"result": "error", "message": "..."}` on stdout
and the message on stderr.

## OpenTofu

Set `plan_source = "opentofu"` in the contract (`planguard.toml`; put it in the protected,
base-branch copy, see "Using planguard safely in CI"). The default is `"terraform"`, which is
exactly the strict behaviour described above. Any other value is exit 2.

Why: **OpenTofu's plan JSON has no `complete` and no `applyable`**, while planguard's default
mode requires `errored` and `complete` to be present booleans (Terraform sets `complete` to
`false` for partial plans). In `"opentofu"` mode:

- `errored` is still required, must be a boolean and must be false;
- `complete` is optional: absent is fine, but if present it must be boolean `true`;
- `applyable` is still ignored; everything else is unchanged (a state JSON without `errored`
  is still refused, so are import blocks, `action_invocations`, `deferred_changes`, ...).

```toml
enabled_rules = ["no-delete", "scope"]
plan_source = "opentofu"
```

**Limitation: OpenTofu partial plans cannot be recognised.** Terraform marks a plan made with
`-target` as `complete: false`, which planguard refuses. We compared OpenTofu 1.13.9 plans made
with `-target=...` and `-exclude=...` against the full plan of the same config: same top-level
keys, same flags, same structure; the only differences are fewer entries (and random ids). There
is no reliable signal, so planguard cannot tell and will judge only the resources that are in
the plan. A trusted pipeline must therefore run `tofu plan` itself, **without `-target` or
`-exclude`**, and apply exactly that plan. planguard cannot enforce this.

## Plan modes and flags

planguard sees only the finished plan JSON, not the command line that produced it.

- **`-target`.** Terraform marks such a plan `complete: false`, and planguard refuses it (exit 2, in
  both `plan_source` modes whenever `complete` is present). OpenTofu gives no signal, so its targeted
  plans cannot be recognised; see the [OpenTofu](#opentofu) section and run `tofu plan` without
  `-target` / `-exclude`.
- **`-refresh-only`.** Verified with real Terraform 1.17.2 and OpenTofu 1.13.9
  ([tests/fixtures/README.md](tests/fixtures/README.md)): the plan has **no `resource_changes` key**, so
  planguard reports "0 changes" and exits 0, even with the strictest contract. That includes a refresh
  that would change the state: when a data source's remote state changed, the only trace is
  `output_changes` (Terraform also reports `applyable: true`), which planguard does not check. Do not
  treat a pass on a refresh-only plan as "nothing will change"; applying it updates the state.
  Drift of a managed resource (`resource_drift`) could not be produced offline and is not verified.
- **`-replace=ADDR`.** The resource appears as `["delete","create"]` with
  `action_reason: replace_by_request` (and no `replace_paths`), indistinguishable by `actions` from a
  config-driven replacement. planguard cannot know about the flag, so keep `no-replace` enabled if
  replacements are not allowed. `no-delete` does not fire on a replacement.

## What planguard does NOT claim

planguard is a narrow guardrail, not a safety guarantee. It does **not** check:

- **security**: it does not know whether a change opens a port, widens a policy, or leaks data;
- **cost**: it does not estimate or limit spend (the budget counts changes, not money);
- **configuration correctness**: a plan can stay inside the contract and still be wrong;
- **live infrastructure**: planguard itself reads only the plan file (and its config) and never contacts
  your cloud, state or providers. Creating the plan is different: `terraform plan` / `tofu plan`
  may contact providers, read and refresh state, and run data sources (for example `external`
  runs a program), all before planguard starts;
- **`output_changes`** or **`resource_drift`** in the plan JSON (v0.1 inspects `resource_changes` only).
  Example: a plan whose only changes are a deleted output (`output_changes`) or an out-of-band
  deleted database found during refresh (`resource_drift`), with an empty `resource_changes`,
  **passes with exit 0**;
- **attribute-level changes**: an `update` is an `update`, whatever it changes.

A passing result means "this plan, as written, matches the contract you wrote", nothing more.
The contract is only as good as the allowlist, stateful types and budgets you configure.

## Verified against

Plan JSON shapes were verified with real `show -json` output from **Terraform 1.17.2** and
**OpenTofu 1.13.9** (both `format_version` "1.2") on **16 scenarios** using the built-in
`terraform_data` resource: create, update, delete, replace (both action orders), no-op, forget,
read (deferred and immediate), empty plan, `count`, `for_each`, a local module, and three
`import` variants, plus three `moved` scenarios (rename, rename with a change, move into a module).
The `actions` lists, `change.importing` and the presence of
`resource_changes` are identical in both tools; the only difference is that OpenTofu's JSON has
no `complete` and no `applyable` (see [OpenTofu](#opentofu)). Fixtures and comparison tables:
[tests/fixtures/README.md](tests/fixtures/README.md). Any `format_version` of the form `1.x` is
accepted and unknown properties are ignored, but any action list not seen in real output is
rejected (exit 2) rather than guessed.

Not verified: plans containing `deposed` objects; the real JSON shape of `action_invocations` and
`deferred_changes` (refused via hand-made fixtures only, neither tool could produce them with
built-in resources); other OpenTofu/Terraform versions and providers; Python 3.11 and 3.12
(developed on 3.13; CI covers all three). See the "NOT verified" list in [CLAUDE.md](CLAUDE.md).

## Security notes

- The plan JSON contains attribute values, possibly secrets. Don't commit it or upload it as a
  CI artifact; delete it after the check.
- Input is treated as untrusted: 128 MiB size limit, resource `address`, `type` and `deposed`
  strings limited to 4096 characters (longer is exit 2, the value is never echoed), duplicate JSON keys rejected, no `eval`/`exec`,
  no network access.

## Using planguard safely in CI

planguard only checks the plan you give it against the contract you give it. These four rules
keep that meaningful.

1. **Load the contract from a protected place.** If the same pull request can edit
   `planguard.toml`, its author can weaken the contract (add `*` to `scope.allow`, raise the
   budget, empty the stateful list) and the check will pass. Read the contract from the
   **base branch**, not from the PR checkout, or protect it with **CODEOWNERS** plus
   **branch protection** (required review by the owners, required status check). The same goes for the
   workflow file that runs planguard and for the planguard version you install: pin it and take
   it from a trusted source, not from the PR. See
   [examples/github-actions/planguard-example.yml](examples/github-actions/planguard-example.yml),
   which reads the contract with `git show origin/<base>:planguard.toml`.

   The workflow file and the planguard version are part of the trust boundary: whoever can change
   them can change what is checked. A CODEOWNERS file that covers all of them (see
   [examples/CODEOWNERS](examples/CODEOWNERS)), together with branch protection that requires
   code-owner review, protects the boundary:

   ```
   /planguard.toml   @YOUR-ORG/infra-owners
   /.github/         @YOUR-ORG/infra-owners
   /CODEOWNERS       @YOUR-ORG/infra-owners
   ```

   The last line matters: if the PR author can edit CODEOWNERS itself, they can remove the protection.

2. **Apply exactly the plan file you checked.** Run `terraform apply tfplan` with the same
   binary plan file that `terraform show -json` converted, and **never re-plan between check and
   apply**: a fresh plan is a different plan that planguard has not seen. Terraform refuses a
   saved plan whose state has since changed (a stale plan), which is what you want. To make
   sure the file did not change in between, record and verify a checksum:

   ```sh
   terraform plan -out=tfplan
   terraform show -json tfplan > plan.json
   sha256sum tfplan > tfplan.sha256
   planguard plan.json --config "$RUNNER_TEMP/planguard.toml"   # exit 0 required
   # later, in the apply step (same file, no new plan):
   sha256sum -c tfplan.sha256 && terraform apply tfplan
   ```

   The binary plan and `plan.json` can contain secrets: do not publish them as artifacts.

   The example workflow only CHECKS: it creates the plan, converts it, loads the contract and
   runs planguard; it never applies anything (a test enforces this, and that the same `tfplan`
   file is used throughout, in the order plan, show, checksum, contract, check, with no second
   plan). If you add an apply step, it must use exactly that file, **ideally in the same job**
   so the binary plan never leaves the runner, and the apply should be gated by a
   **protected environment** with **required reviewers**. The checksum only detects accidental change inside
   the trusted job: anyone who can edit the job can also edit the checksum step. Moving the
   binary plan to another job means uploading an artifact, and that artifact can contain secrets.

3. **Know what planguard cannot see.** It reads `resource_changes` and nothing else. It does
   not see provisioners (for example `local-exec` or `remote-exec` commands), data source side
   effects (the `external` data source runs a program during `terraform plan`, before planguard
   even starts), or what a `create` actually does (a created IAM policy, a new open security
   group, a module that does something surprising). It cannot judge attribute values at all.

4. **A pass is not an approval.** Exit 0 means "this plan, as written, stays inside the
   contract", nothing more. It is a cheap, repeatable filter that should sit *in front of* human
   review, not replace it.

5. **Pin what produces the plan.** planguard does not check `terraform_version`, provider versions
   or provider identities, and unknown properties in the JSON are ignored. Pin the Terraform/OpenTofu
   version and the providers in CI (commit the `.terraform.lock.hcl` lock file, set `required_version`
   and `required_providers`), and treat plans produced by any other version or provider set as
   unverified: planguard was only verified with the versions listed under "Verified against".

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
# tests/test_fuzz.py runs 20,000 deterministic mutations against an independent oracle
# (about 5 s); set PLANGUARD_FUZZ_N to change the count

# mutation testing: applies 104 small source mutations to temp copies of src/ and checks
# that the tests notice each one (never edits src/ itself; about 1-2 minutes)
.venv/bin/python scripts/mutation_check.py

# regenerate real fixtures (needs terraform or tofu; uses only built-in terraform_data,
# throwaway temp directories and local state, creates nothing real)
.venv/bin/python scripts/make_fixtures.py --binary "$(command -v terraform)" [scenario ...]
```

`scripts/make_fixtures.py` is the only place `terraform apply` is ever run, and only inside its
own temporary directories.

## License

[MIT](LICENSE), copyright (c) 2026 ZahraMost.
