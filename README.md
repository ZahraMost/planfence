> **Status: early release (0.1.x), experimental.** planfence is a narrow guardrail, not a security audit:
> a pass means 'this plan stays inside the contract you wrote', nothing more
> (see [What planfence does NOT claim](https://github.com/ZahraMost/planfence#what-planfence-does-not-claim)).

# planfence

A deterministic guardrail for Terraform / OpenTofu plans. It reads the JSON from
`terraform show -json <planfile>` and checks it against a small contract file,
answering one question before `apply`: **does this plan stay inside the contract?**

- No network, no AI, no cloud credentials.
- Python >= 3.11, standard library only (pytest is a dev-only dependency).
- Fail-closed: if the plan is not fully understood, planfence never says "OK".

## Why

AI-generated infrastructure code often passes `terraform validate` but makes
changes nobody intended: deletes, replacements, changes outside the intended
scope. planfence catches those cheaply and repeatably.

## Quick start

```sh
# install (needs Python 3.11 or newer; no runtime dependencies; provides the `planfence` command)
pip install planfence

terraform plan -out=tfplan
terraform show -json tfplan > plan.json
planfence plan.json --config planfence.toml
```

Alternatively, install from a clone (for development, or to pin a commit), or run it from the clone without
installing:

```sh
pip install .
# or, without installing:
PYTHONPATH=src python -m planfence plan.json --config planfence.toml
```

Sample output. This is real output of the CLI (a test reproduces it exactly) on the test fixture
`tests/fixtures/real/replace.json`, a `terraform_data` resource whose `triggers_replace` changed, so it
is replaced. The demo config, saved as `demo.toml`:

```toml
enabled_rules = ["no-replace", "stateful-protect"]

[stateful]
types = ["terraform_data"]
```

```console
$ planfence tests/fixtures/real/replace.json --config demo.toml
[error] no-replace terraform_data.example (terraform_data) delete,create
[critical] stateful-protect terraform_data.example (terraform_data) delete,create
planfence: FAIL - 2 violation(s)
```

The main example, with real providers, is under "Real plan example" below.

Options: `--config PATH` (default `./planfence.toml`; a missing file is an error),
`--format text|json` (default `text`).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | plan parsed and every enabled rule passed |
| 1 | at least one rule violated |
| 2 | anything else: unreadable or invalid plan, unknown `format_version`, errored or incomplete plan, bad config, bad arguments, a crash, or failure to write the output (closed pipe, full disk) |

## Real plan example

This is an actual run, not a mock-up. It was produced with **Terraform 1.17.2** and the providers
`hashicorp/local` (v2.9.1) and `hashicorp/random` (v3.9.1); no cloud account is involved (the only
file written is a local text file in a throwaway directory). The plan JSON and Terraform's own text
output are kept in [tests/fixtures/providers/terraform/](https://github.com/ZahraMost/planfence/tree/main/tests/fixtures/providers/terraform/) together with the
exact provider versions.

The configuration (the first apply created the file with `content = "hello"`; the plan below changes it):

```hcl
terraform {
  required_providers {
    local  = { source = "hashicorp/local" }
    random = { source = "hashicorp/random" }
  }
}

resource "random_pet" "name" {
  length = 2
}

resource "local_file" "hello" {
  content  = "hello, world"
  filename = "${random_pet.name.id}.txt"
}
```

Changing the `content` of a `local_file` cannot be done in place, so Terraform plans a replacement
(`-/+`: destroy, then create). The relevant part of `terraform plan` (the derived hash attributes
that also change are left out here):

```text
  # local_file.hello must be replaced
-/+ resource "local_file" "hello" {
      ~ content              = "hello" -> "hello, world" # forces replacement
        # (3 unchanged attributes hidden)
    }

Plan: 1 to add, 0 to change, 1 to destroy.
```

In `terraform show -json` that resource is `["delete","create"]` (with `action_reason`
`replace_because_cannot_update`), and the `random_pet` is a no-op. With this `planfence.toml`:

```toml
enabled_rules = ["no-replace"]
```

planfence reports:

```console
$ terraform show -json tfplan > plan.json
$ planfence plan.json --config planfence.toml
[error] no-replace local_file.hello (local_file) delete,create
planfence: FAIL - 1 violation(s)
```

and exits with status 1. A replacement is not a plain delete, so `no-delete` alone would not have
fired: keep `no-replace` enabled if replacements are not allowed.

## Rules (v0.1)

| Rule id | Violated when | Severity |
|---|---|---|
| `no-delete` | a resource's actions are exactly `["delete"]` (a replacement or forget is not a delete) | error |
| `no-replace` | actions contain both `delete` and `create` (either order) | error |
| `scope` | a change's address matches none of the allowed patterns; for a moved resource BOTH its address and its `previous_address` must match the allowlist | error |
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
- **Exit 2 (refused, never judged halfway).** planfence exits 2 when a plan contains `change.importing`
  (an `import` block, even when the action is a no-op), a non-empty top-level `action_invocations` or
  `deferred_changes`, or an unknown or empty `actions` list; when the plan is errored (`errored: true`) or
  incomplete (`complete` is not true); or when it has an unknown `format_version` (anything but `1.x`).
  Absent or empty `action_invocations` / `deferred_changes` are fine. Input that cannot be parsed at all
  (invalid JSON, missing or malformed required fields, over-long strings) also exits 2. About incomplete
  plans: Terraform marks a plan made with `-target` as `complete: false`, which is refused; an OpenTofu
  partial plan (`-target` / `-exclude`) cannot be detected, so it is not refused (see [OpenTofu](https://github.com/ZahraMost/planfence#opentofu)).
- **`output_changes` and `resource_drift` are ignored, not refused, by default** (`state_only_plans = "allow"`):
  a plan whose only effects are output changes, drift or pure moves passes if the rules pass. With
  `state_only_plans = "refuse"` such plans exit 2, see
  [Refusing state-only plans](https://github.com/ZahraMost/planfence#refusing-state-only-plans-state_only_plans).
- An absent `resource_changes` key means "nothing planned" (Terraform omits it for empty plans),
  accepted only when `format_version` is known, `errored` is false and `complete` is true.

### Moved resources

A resource that a `moved` block renames or relocates appears in `resource_changes` with a
`previous_address`. Verified with real Terraform 1.17.2 and OpenTofu 1.13.9 plans
([tests/fixtures/README.md](https://github.com/ZahraMost/planfence/blob/main/tests/fixtures/README.md)): a pure rename or move into a module is
`["no-op"]`, a move together with an input change is `["update"]` at the **new** address.

- **The bypass that existed.** `scope` used to look only at the new address, so a resource outside the
  allowlist could be moved to an allowed address and changed in the same plan. With the real fixture
  `moved_update` and only `terraform_data.new` allowed, planfence passed although the resource being
  updated lived at `terraform_data.old`.
- **The rule.** `scope` treats every move as a change, whatever its action (even `["no-op"]`):
  for a moved resource BOTH its address and its `previous_address` must match the allowlist, otherwise it
  is a `scope` violation. Entries that are not moves are unchanged (no-op and read are still ignored).
- **Everything else is unchanged.** `no-delete`, `no-replace` and `stateful-protect` judge actions only.
  For `budget`, a pure move is not counted as a change (a move with an update counts once).
- **Validation.** `previous_address`, when present, must be a non-empty string of at most 4096
  characters (null, wrong type, empty or longer is exit 2; the message names the field and the entry
  index only). A `previous_address` equal to `address` is not a move and is ignored.
- **Output.** Violations of moved entries show where they came from: text appends `moved-from=ADDR`
  (ASCII-escaped, after `deposed=`), JSON violation objects always have a `previous_address` field
  (`null` when there is none).
- **Across pull requests.** planfence judges one plan at a time. A PR that moves a resource into scope
  and a later PR that changes it are each judged on its own against the allowlist; the contract is the
  allowlist, and a resource inside it may be changed however it got there.

## Configuration: `planfence.toml`

See [examples/planfence.toml](https://github.com/ZahraMost/planfence/blob/main/examples/planfence.toml).

```toml
enabled_rules = ["no-delete", "no-replace", "scope", "stateful-protect", "budget"]
# plan_source = "opentofu"   # optional, default "terraform"; see the OpenTofu section
# state_only_plans = "refuse"  # optional, default "allow"; recommended for CI gates

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
- **Stateful types** are matched exactly (no wildcards). planfence ships **no default list**:
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

Set `plan_source = "opentofu"` in the contract (`planfence.toml`; put it in the protected,
base-branch copy, see "Using planfence safely in CI"). The default is `"terraform"`, which is
exactly the strict behaviour described above. Any other value is exit 2.

Why: **OpenTofu's plan JSON has no `complete` and no `applyable`**, while planfence's default
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
`-target` as `complete: false`, which planfence refuses. We compared OpenTofu 1.13.9 plans made
with `-target=...` and `-exclude=...` against the full plan of the same config: same top-level
keys, same flags, same structure; the only differences are fewer entries (and random ids). There
is no reliable signal, so planfence cannot tell and will judge only the resources that are in
the plan. A trusted pipeline must therefore run `tofu plan` itself, **without `-target` or
`-exclude`**, and apply exactly that plan. planfence cannot enforce this.

## Plan modes and flags

planfence sees only the finished plan JSON, not the command line that produced it.

- **`-target`.** Terraform marks such a plan `complete: false`, and planfence refuses it (exit 2, in
  both `plan_source` modes whenever `complete` is present). OpenTofu gives no signal, so its targeted
  plans cannot be recognised; see the [OpenTofu](https://github.com/ZahraMost/planfence#opentofu) section and run `tofu plan` without
  `-target` / `-exclude`.
- **`-refresh-only`.** Verified with real Terraform 1.17.2 and OpenTofu 1.13.9
  ([tests/fixtures/README.md](https://github.com/ZahraMost/planfence/blob/main/tests/fixtures/README.md)): the plan has **no `resource_changes` key**, so
  planfence reports "0 changes" and exits 0, even with the strictest contract. That includes a refresh
  that would change the state: when a data source's remote state changed, the only trace is
  `output_changes` (Terraform also reports `applyable: true`), which planfence does not check by default
  (`state_only_plans = "allow"`). Do not
  treat a pass on a refresh-only plan as "nothing will change"; applying it updates the state.
  Set `state_only_plans = "refuse"` to close this gap (see below).
  Drift of a managed resource (`resource_drift`) could not be produced offline and is not verified.
- **`-replace=ADDR`.** The resource appears as `["delete","create"]` with
  `action_reason: replace_by_request` (and no `replace_paths`), indistinguishable by `actions` from a
  config-driven replacement. planfence cannot know about the flag, so keep `no-replace` enabled if
  replacements are not allowed. `no-delete` does not fire on a replacement.

### Refusing state-only plans: `state_only_plans`

Some plans change nothing in `resource_changes` yet still do something when applied: they update
outputs, record drift, or only move resources between addresses. The default
(`state_only_plans = "allow"`) keeps today's behaviour and does not look at them. With
`state_only_plans = "refuse"` planfence exits 2 when the plan has **no resource changes** (no entry
other than no-op or read; moves do not count as changes) **and** at least one of:

- an `output_changes` entry whose actions are not exactly `["no-op"]`;
- a non-empty `resource_drift`;
- at least one moved entry (a pure move).

Plans that also change resources are never refused by this key. Verified with real plans of both
tools ([tests/fixtures/README.md](https://github.com/ZahraMost/planfence/blob/main/tests/fixtures/README.md)): an unchanged output is listed as
`["no-op"]` (not refused); an output that changes because a resource changes comes together with the
resource change (not refused); `-refresh-only` with real output drift is refused; a plan that only
adds an output (`output_added_only`) is refused; a pure move is refused. `resource_drift` is verified
only with a hand-made fixture, because real drift of a managed resource cannot be produced offline.
Malformed `output_changes` / `resource_drift` in such a plan are refused as well. Use `"refuse"` for a
CI gate: the gate should say "nothing happens that planfence did not judge". The default stays
`"allow"` so existing contracts keep working.

## What planfence does NOT claim

planfence is a narrow guardrail, not a safety guarantee. It does **not** check:

- **security**: it does not know whether a change opens a port, widens a policy, or leaks data;
- **cost**: it does not estimate or limit spend (the budget counts changes, not money);
- **configuration correctness**: a plan can stay inside the contract and still be wrong;
- **live infrastructure**: planfence itself reads only the plan file (and its config) and never contacts
  your cloud, state or providers. Creating the plan is different: `terraform plan` / `tofu plan`
  may contact providers, read and refresh state, and run data sources (for example `external`
  runs a program), all before planfence starts;
- **`output_changes`** and **`resource_drift`**, by default: with the default `state_only_plans = "allow"`
  planfence inspects `resource_changes` only. Example: a plan whose only changes are a deleted output
  (`output_changes`) or an out-of-band deleted database found during refresh (`resource_drift`), with an
  empty `resource_changes`, **passes with exit 0**. With `state_only_plans = "refuse"` such plans (and
  plans whose only effect is a pure move) exit 2, see
  [Refusing state-only plans](https://github.com/ZahraMost/planfence#refusing-state-only-plans-state_only_plans);
- **attribute-level changes**: an `update` is an `update`, whatever it changes.

A passing result means "this plan, as written, matches the contract you wrote", nothing more.
The contract is only as good as the allowlist, stateful types and budgets you configure.

## Related tools

planfence is a small gate for what a plan does. It does not replace the tools below: most teams should also run a security scanner and keep human review. The descriptions come from each project's public documentation as of November 2026. They were not tested against planfence and may be out of date; check the linked pages.

| Tool | What it does with a plan | How planfence differs |
|---|---|---|
| [Checkov](https://github.com/bridgecrewio/checkov) | Static analysis; according to its README it has over 1000 built-in policies. It scans Terraform code and plan JSON (`checkov -f tf.json`) for security and compliance misconfigurations. Maintained by Prisma Cloud. | Checkov judges resource settings. planfence judges which resources a plan deletes, replaces, moves or adds. They are complementary. |
| [Trivy](https://trivy.dev/latest/docs/coverage/iac/terraform/) | `trivy config tfplan.json` scans plan JSON for misconfigurations. Its documentation lists limits for plan JSON, for example incomplete references in expressions that use `count` or `for_each`. | Same distinction: Trivy looks for insecure settings, planfence looks at what changes. |
| [Conftest](https://github.com/open-policy-agent/conftest) (Open Policy Agent) | Runs Rego policies against structured data, including plan JSON. It is general purpose and can express rules about actions, addresses, counts and attribute values. | Conftest can express the rules planfence ships (the article [Safe Terraform auto-apply with conftest](https://www.bejarano.io/terraform-autoapply/) has an example policy that allows only no-op, create and read actions). planfence needs no Rego, ships ready-made rules (scope that also checks the previous address of moved resources, stateful types, change budgets), refuses plans it cannot fully understand, and was verified with real Terraform and OpenTofu output. For attribute-level or custom logic use Conftest; the two can run side by side. |
| [terraform-compliance](https://github.com/terraform-compliance/cli) | A BDD framework: Gherkin feature files run against a plan (`terraform-compliance -f features -p plan.out`), readable by people who do not program. | planfence has no scenario language, only a fixed rule set configured in TOML. |
| Sentinel ([tfplan/v2 import](https://developer.hashicorp.com/terraform/cloud-docs/policy-enforcement/sentinel/import/tfplan-v2)) | HashiCorp's policy as code runs inside HCP Terraform or Terraform Enterprise and sees the plan through the tfplan/v2 import, including the actions of each change. See HashiCorp's pricing for the limits of the free edition. | planfence runs anywhere (a laptop, any CI), needs no platform and no policy language. |
| [Terraguard](https://github.com/jamian/terraguard) | A small Python CLI that checks Terraform plans against rules written in YAML (its README shows required tags). | planfence focuses on actions and addresses, including the previous address of moved resources, and reads a plan JSON file that you produce. |
| AI-agent guardrails, for example [dcg](https://github.com/Dicklesworthstone/destructive_command_guard) and [tfrev](https://pypi.org/project/tfrev) | dcg is a hook that blocks destructive commands before an AI coding agent runs them. tfrev uses an LLM to review a plan against the code change. | dcg looks at commands and tfrev uses a model. planfence has no model: it is deterministic and judges the finished plan. The layers complement each other: a hook can stop dangerous commands, planfence checks the plan before apply. |

### When planfence is a good fit

- You want a small, deterministic, fail-closed gate on what a plan does ("no deletes, no replacements, only these resources, at most N changes"), for example for AI-generated or automated changes.
- You do not want to write or maintain a policy language.
- You use Terraform, OpenTofu or both.

### When it is not enough

- You need checks on attribute values (encryption, open ports, tags, instance sizes): use Checkov, Trivy, Conftest or terraform-compliance.
- You need cost control or checks against live state.
- You need an approval workflow: planfence says whether a plan stays inside a contract, not whether it should be applied (see "Using planfence safely in CI").

## Verified against

Plan JSON shapes were verified with real `show -json` output from **Terraform 1.17.2** and
**OpenTofu 1.13.9** (both `format_version` "1.2") on **16 scenarios** using the built-in
`terraform_data` resource: create, update, delete, replace (both action orders), no-op, forget,
read (deferred and immediate), empty plan, `count`, `for_each`, a local module, and three
`import` variants, plus three `moved` scenarios (rename, rename with a change, move into a module).
The `actions` lists, `change.importing` and the presence of
`resource_changes` are identical in both tools; the only difference is that OpenTofu's JSON has
no `complete` and no `applyable` (see [OpenTofu](https://github.com/ZahraMost/planfence#opentofu)). Fixtures and comparison tables:
[tests/fixtures/README.md](https://github.com/ZahraMost/planfence/blob/main/tests/fixtures/README.md). Any `format_version` of the form `1.x` is
accepted and unknown properties are ignored, but any action list not seen in real output is
rejected (exit 2) rather than guessed.

Not verified: plans containing `deposed` objects; the real JSON shape of `action_invocations` and
`deferred_changes` (refused via hand-made fixtures only, neither tool could produce them with
built-in resources); other OpenTofu/Terraform versions and providers; Python 3.11 and 3.12
(developed on 3.13; CI covers all three). See the "NOT verified" list in [CLAUDE.md](https://github.com/ZahraMost/planfence/blob/main/CLAUDE.md).

## Security notes

Report vulnerabilities privately, see [SECURITY.md](https://github.com/ZahraMost/planfence/blob/main/SECURITY.md). Changes are listed in
[CHANGELOG.md](https://github.com/ZahraMost/planfence/blob/main/CHANGELOG.md).

- The plan JSON contains attribute values, possibly secrets. Don't commit it or upload it as a
  CI artifact; delete it after the check.
- Input is treated as untrusted: 128 MiB size limit, resource `address`, `type` and `deposed`
  strings limited to 4096 characters (longer is exit 2, the value is never echoed), duplicate JSON keys rejected, no `eval`/`exec`,
  no network access.

## Using planfence safely in CI

planfence only checks the plan you give it against the contract you give it. These four rules
keep that meaningful.

1. **Load the contract from a protected place.** If the same pull request can edit
   `planfence.toml`, its author can weaken the contract (add `*` to `scope.allow`, raise the
   budget, empty the stateful list) and the check will pass. Read the contract from the
   **base branch**, not from the PR checkout, or protect it with **CODEOWNERS** plus
   **branch protection** (required review by the owners, required status check). The same goes for the
   workflow file that runs planfence and for the planfence version you install: pin it and take
   it from a trusted source, not from the PR. See
   [examples/github-actions/planfence-example.yml](https://github.com/ZahraMost/planfence/blob/main/examples/github-actions/planfence-example.yml),
   which reads the contract with `git show origin/<base>:planfence.toml`.

   The workflow file and the planfence version are part of the trust boundary: whoever can change
   them can change what is checked. A CODEOWNERS file that covers all of them (see
   [examples/CODEOWNERS](https://github.com/ZahraMost/planfence/blob/main/examples/CODEOWNERS)), together with branch protection that requires
   code-owner review, protects the boundary:

   ```
   /planfence.toml   @ZahraMost
   /.github/         @ZahraMost
   /CODEOWNERS       @ZahraMost
   ```

   The last line matters: if the PR author can edit CODEOWNERS itself, they can remove the protection.

2. **Apply exactly the plan file you checked.** Run `terraform apply tfplan` with the same
   binary plan file that `terraform show -json` converted, and **never re-plan between check and
   apply**: a fresh plan is a different plan that planfence has not seen. Terraform refuses a
   saved plan whose state has since changed (a stale plan), which is what you want. To make
   sure the file did not change in between, record and verify a checksum:

   ```sh
   terraform plan -out=tfplan
   terraform show -json tfplan > plan.json
   sha256sum tfplan > tfplan.sha256
   planfence plan.json --config "$RUNNER_TEMP/planfence.toml"   # exit 0 required
   # later, in the apply step (same file, no new plan):
   sha256sum -c tfplan.sha256 && terraform apply tfplan
   ```

   The binary plan and `plan.json` can contain secrets: do not publish them as artifacts.

   The example workflow only CHECKS: it creates the plan, converts it, loads the contract and
   runs planfence; it never applies anything (a test enforces this, and that the same `tfplan`
   file is used throughout, in the order plan, show, checksum, contract, check, with no second
   plan). If you add an apply step, it must use exactly that file, **ideally in the same job**
   so the binary plan never leaves the runner, and the apply should be gated by a
   **protected environment** with **required reviewers**. The checksum only detects accidental change inside
   the trusted job: anyone who can edit the job can also edit the checksum step. Moving the
   binary plan to another job means uploading an artifact, and that artifact can contain secrets.

3. **Know what planfence cannot see.** It reads `resource_changes` and nothing else. It does
   not see provisioners (for example `local-exec` or `remote-exec` commands), data source side
   effects (the `external` data source runs a program during `terraform plan`, before planfence
   even starts), or what a `create` actually does (a created IAM policy, a new open security
   group, a module that does something surprising). It cannot judge attribute values at all.

4. **A pass is not an approval.** Exit 0 means "this plan, as written, stays inside the
   contract", nothing more. It is a cheap, repeatable filter that should sit *in front of* human
   review, not replace it.

5. **Pin what produces the plan.** planfence does not check `terraform_version`, provider versions
   or provider identities, and unknown properties in the JSON are ignored. Pin the Terraform/OpenTofu
   version and the providers in CI (commit the `.terraform.lock.hcl` lock file, set `required_version`
   and `required_providers`), and treat plans produced by any other version or provider set as
   unverified: planfence was only verified with the versions listed under "Verified against".

## CI

Run planfence with a job timeout (e.g. `timeout-minutes: 5` in GitHub Actions) so an unexpectedly
huge or adversarial plan can never hang your pipeline. Both workflows in this repo set one.

- [.github/workflows/ci.yml](https://github.com/ZahraMost/planfence/blob/main/.github/workflows/ci.yml) runs the test suite on Python 3.11-3.13.
- [examples/github-actions/planfence-example.yml](https://github.com/ZahraMost/planfence/blob/main/examples/github-actions/planfence-example.yml)
  shows how to run planfence on a plan in your own pipeline (copy it into your repo; it is not
  active here).

## Development

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest
# tests/test_fuzz.py runs 20,000 deterministic mutations against an independent oracle
# (about 5 s); set PLANFENCE_FUZZ_N to change the count

# quality gates (dev tools only, no runtime dependencies; CI runs them as a separate job)
.venv/bin/python -m pip install ruff mypy
.venv/bin/ruff check src      # configured in pyproject.toml
.venv/bin/mypy                # strict, files = ["src"]

# mutation testing: applies 126 small source mutations to temp copies of src/ and checks
# that the tests notice each one (never edits src/ itself; about 1-2 minutes)
.venv/bin/python scripts/mutation_check.py

# there are no wall-clock assertions in the tests: speed is checked by operation counts and by the
# scaling ratio of CPU time (tests/timing.py, tests/test_scaling.py), plus a 15-minute `hang_guard`
# watchdog against endless hangs, so slow CI runners cannot fail them
# the packaging test builds the wheel and sdist, installs the wheel into a fresh venv and
# uses it (needs `pip install build` and network access; skipped with a reason otherwise)

# regenerate real fixtures (needs terraform or tofu; uses only built-in terraform_data,
# throwaway temp directories and local state, creates nothing real)
.venv/bin/python scripts/make_fixtures.py --binary "$(command -v terraform)" [scenario ...]
```

`scripts/make_fixtures.py` is the only place `terraform apply` is ever run, and only inside its
own temporary directories.

## License

[MIT](https://github.com/ZahraMost/planfence/blob/main/LICENSE), copyright (c) 2026 ZahraMost.
