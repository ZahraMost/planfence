# Test fixtures

## Providers downloaded

None. Every generated fixture uses only the built-in `terraform_data` resource and the built-in
`terraform_remote_state` data source (`terraform.io/builtin/terraform`), so `init` never downloaded a
provider. If a future fixture needs one, only the non-cloud hashicorp/random, hashicorp/local or
hashicorp/null may be used and their exact versions must be recorded here.

## real/ (generated, not hand-edited)

Produced by `scripts/make_fixtures.py` with Terraform 1.17.2 (darwin_arm64),
using only the built-in `terraform_data` resource and `terraform_remote_state`
data source in throwaway temp directories. All have `format_version` "1.2",
`errored` false, `complete` true.

| File | Scenario | resource_changes (address -> actions) |
|---|---|---|
| create.json | new resource | `terraform_data.example` -> `["create"]` |
| update.json | `input` changed | `terraform_data.example` -> `["update"]` |
| delete.json | resource removed from config | `terraform_data.example` -> `["delete"]` |
| replace.json | `triggers_replace` changed | `terraform_data.example` -> `["delete","create"]` |
| replace_cbd.json | same, `create_before_destroy` | `terraform_data.example` -> `["create","delete"]` |
| noop.json | no config change | `terraform_data.example` -> `["no-op"]` |
| forget.json | `removed` block, `destroy = false` | `terraform_data.example` -> `["forget"]` |
| read.json | data source deferred via `depends_on` | `data.terraform_remote_state.src` -> `["read"]`, `terraform_data.gate` -> `["create"]` |
| read_immediate.json | data source read at plan time | **no `resource_changes` key** |
| import.json | `import` block, no `triggers_replace` | `terraform_data.example` -> `["update"]` with `change.importing = {id}` |
| import_replace.json | `import` block + `triggers_replace` | `terraform_data.example` -> `["delete","create"]` with `change.importing` |
| import_noop.json | `import` block, nothing else differs | `terraform_data.example` -> `["no-op"]` with `change.importing` (an import hidden behind a no-op) |
| empty.json | no resources at all | **no `resource_changes` key** |
| count.json | `count` 3 -> 2 | `[0]`,`[1]` -> `["no-op"]`, `terraform_data.counted[2]` -> `["delete"]` |
| for_each.json | keys {a,b} -> {a,c}, value of a changed | `["a"]` -> `["update"]`, `["b"]` -> `["delete"]`, `["c"]` -> `["create"]` |
| module.json | local module input changed | `module.child.terraform_data.inner` -> `["update"]` |

Notes:
- `timestamp` changes on every regeneration; regenerate only the scenarios you need
  (`python scripts/make_fixtures.py --binary <bin> <scenario> ...`).
- `forget` carries `action_reason: delete_because_no_resource_config`, the same as a
  real delete. Rules must use `actions`, never `action_reason`.

## opentofu/ (generated, not hand-edited)

The same 16 scenarios as `real/`, generated with **OpenTofu 1.13.9** (darwin_arm64) by
`python scripts/make_fixtures.py --binary <tofu> --out tests/fixtures/opentofu`.

Compared with the Terraform 1.17.2 files of the same name: `format_version` ("1.2"), the exact
`actions` lists, `action_reason`, `change.importing`, `errored` (false) and whether
`resource_changes` is present are **identical in all 16 scenarios**. The one difference:

| Top-level key | Terraform 1.17.2 | OpenTofu 1.13.9 |
|---|---|---|
| `errored` | present, boolean | present, boolean |
| `complete` | present, boolean | **absent** |
| `applyable` | present, boolean | **absent** |

planguard therefore needs `plan_source = "opentofu"` in its config to accept these files.
The three `import*` files are refused in both modes (`change.importing`).

## partial/ (generated): are partial plans recognisable?

Two resources `a` and `b`, both changed (`input` "1" -> "2"), planned three ways. Files:
`partial/<tool>/two_full.json`, `two_target.json` (`-target=terraform_data.a`) and, for
OpenTofu only, `two_exclude.json` (`-exclude=terraform_data.b`; Terraform 1.17.2 has no
`-exclude`).

| Tool | Plan | `resource_changes` | `complete` | `applyable` | Any other signal? |
|---|---|---|---|---|---|
| Terraform 1.17.2 | full | a, b | true | true | n/a |
| Terraform 1.17.2 | `-target=a` | a | **false** | true | `complete` is the signal |
| OpenTofu 1.13.9 | full | a, b | absent | absent | n/a |
| OpenTofu 1.13.9 | `-target=a` | a | absent | absent | **none** (same key set; only the lists are shorter and ids differ) |
| OpenTofu 1.13.9 | `-exclude=b` | a | absent | absent | **none** (same as above) |

In OpenTofu the only differences from the full plan are derived content (fewer entries in
`resource_changes` and `planned_values`, random resource ids). A heuristic such as "configured
resources missing from `planned_values`" would also fire for legitimate `count = 0`, empty
`for_each` and destroyed resources, so it is not a reliable signal and planguard does not use it.

## modes/ (generated): plan flags and modes, real output of both tools

`modes/terraform/` (Terraform 1.17.2) and `modes/opentofu/` (OpenTofu 1.13.9), generated with
`scripts/make_fixtures.py --out tests/fixtures/modes/<dir> replace_flag refresh_only refresh_only_output_drift`
(non-default scenarios, built-in resources only). Verified by `tests/test_plan_modes.py`.

| Scenario | How it is produced | `resource_changes` | Other top-level content | planguard (strictest contract) |
|---|---|---|---|---|
| `replace_flag` | unchanged config, `plan -replace=terraform_data.example` | `terraform_data.example` -> `["delete","create"]`, `action_reason: replace_by_request`, **no** `replace_paths` | none | exit 1 (`no-replace`, budget) |
| `refresh_only` | unchanged config, `plan -refresh-only` | **key absent** | no `resource_drift`, no `output_changes`; Terraform: `applyable: false` | exit 0 |
| `refresh_only_output_drift` | `terraform_remote_state` reads a local producer state; the producer's output changes ("hello" -> "changed") between the consumer's apply and `plan -refresh-only` | **key absent** (the resource that depends on it is not listed) | `output_changes.greeting` = `["update"]` hello -> changed; `relevant_attributes`; Terraform: `applyable: true` | **exit 0** |

Observations that matter:
- `-replace` is indistinguishable from a config-driven replacement except for `action_reason`
  (`replace_by_request` vs `replace_because_cannot_update`) and the missing `replace_paths`;
  planguard judges `actions` only, so `no-replace` catches both.
- A `-refresh-only` plan that would change the state (here an output; Terraform reports it as
  `applyable: true`) has nothing in `resource_changes`, so planguard passes it. This is a documented
  limitation: `output_changes` and `resource_drift` are not checked.
- **Not produced:** real `resource_drift` (a managed resource changed outside Terraform). It cannot
  be created offline with built-in resources (`terraform_data` has no external system), and editing
  the state file by hand would be faking it, so there is no fixture for it.

## moved/ (generated): `moved` blocks, real output of both tools

`moved/terraform/` (Terraform 1.17.2) and `moved/opentofu/` (OpenTofu 1.13.9), generated with
`scripts/make_fixtures.py --out tests/fixtures/moved/<dir> moved_only moved_update moved_into_module`
(non-default scenarios, built-in `terraform_data` only, no providers downloaded).

| Scenario | Config change | `address` | `previous_address` | `actions` | `action_reason` | `module_address` |
|---|---|---|---|---|---|---|
| `moved_only` | rename `old` -> `new`, nothing else | `terraform_data.new` | `terraform_data.old` | `["no-op"]` | none | none |
| `moved_update` | rename `old` -> `new` and `input` "a" -> "b" | `terraform_data.new` | `terraform_data.old` | `["update"]` | none | none |
| `moved_into_module` | move `terraform_data.thing` into local module `m` | `module.m.terraform_data.thing` | `terraform_data.thing` | `["no-op"]` | none | `module.m` |

Both tools are identical (apart from OpenTofu's missing `complete` / `applyable`). Terraform reports
`applyable: true` for the pure moves: applying them changes the state. The only extra key on an entry
is `previous_address` (plus `module_address` for the module case); the top-level shape is unchanged.

## outputs/ (generated): ordinary plans that have an output

`outputs/terraform/` (Terraform 1.17.2) and `outputs/opentofu/` (OpenTofu 1.13.9), non-default scenarios
(`--out tests/fixtures/outputs/<dir> output_unchanged output_changes_with_resource output_added_only`).
Identical in both tools. `resource_drift` is absent in all of them.

| Scenario | `resource_changes` | `output_changes` |
|---|---|---|
| `output_unchanged` | `terraform_data.example` -> `["no-op"]` | `o` -> `["no-op"]` |
| `output_changes_with_resource` | `terraform_data.example` -> `["update"]` | `o` -> `["update"]` |
| `output_added_only` | `terraform_data.example` -> `["no-op"]` | `o` -> `["create"]` |

An unchanged output is listed with `["no-op"]`; that is why the `state_only_plans = "refuse"` rule looks
for output actions that are not exactly `["no-op"]`.

## handmade

Hand-written, NOT verified against a real plan. They exist only to test that
planguard fails closed (exit 2). Terraform 1.17.2 cannot produce them with
built-in resources only (no `-allow-deferral` flag in this build, and the
built-in provider defines no actions), so the exact JSON shape of the entries
is unverified; only the presence of the top-level key matters to planguard.
Both are `real/noop.json` plus one extra key.

| File | Extra key | Why handmade |
|---|---|---|
| handmade/action_invocations.json | `action_invocations: [{...}]` | needs a provider with actions (Terraform actions) |
| handmade/deferred_changes.json | `deferred_changes: [{...}]` | needs `plan -allow-deferral` (not available here) |
| handmade/resource_drift.json | `resource_drift: [{...}]` (one `update` of `terraform_data.example`), on top of `real/noop.json` | drift of a managed resource needs something outside Terraform to change it; `terraform_data` has no external system and editing the state by hand would be faking |

Other hand-built plans live inline in the tests (e.g. `deposed` entries, `output_changes`,
`resource_drift`) and are not files.
