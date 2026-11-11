# Test fixtures

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

Other hand-built plans live inline in the tests (e.g. `deposed` entries, `output_changes`,
`resource_drift`) and are not files.
