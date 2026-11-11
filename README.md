# planguard

Work in progress. See CLAUDE.md.

## What planguard does NOT check (v0.1)

- Security, cost, configuration correctness, or live infrastructure.
- `output_changes` and `resource_drift` in the plan JSON: only `resource_changes` is checked.
