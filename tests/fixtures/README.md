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
| empty.json | no resources at all | **no `resource_changes` key** |
| count.json | `count` 3 -> 2 | `[0]`,`[1]` -> `["no-op"]`, `terraform_data.counted[2]` -> `["delete"]` |
| for_each.json | keys {a,b} -> {a,c}, value of a changed | `["a"]` -> `["update"]`, `["b"]` -> `["delete"]`, `["c"]` -> `["create"]` |
| module.json | local module input changed | `module.child.terraform_data.inner` -> `["update"]` |

Notes:
- `timestamp` changes on every regeneration; regenerate only the scenarios you need
  (`python scripts/make_fixtures.py --binary <bin> <scenario> ...`).
- `forget` carries `action_reason: delete_because_no_resource_config`, the same as a
  real delete. Rules must use `actions`, never `action_reason`.

## handmade

None yet. Hand-written fixtures (e.g. stateful cloud types, `deposed` objects)
must be listed here as `handmade` until verified against a real plan.
