# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - Unreleased

First release candidate. Not tagged; the package version is still `0.0.1`.
The project was formerly named planguard (renamed because that name collides with an existing PyPI project and its command); no alias is kept.

### Added
- planfence CLI: checks the JSON from `terraform show -json` / `tofu show -json` against a
  `planfence.toml` contract. Text and `--format json` output, `--version`.
- Rules: `no-delete`, `no-replace`, `scope` (custom `*` / `?` matcher), `stateful-protect`, `budget`.
- `plan_source = "terraform" | "opentofu"` (OpenTofu plan JSON has no `complete` / `applyable`).
- `state_only_plans = "allow" | "refuse"` to refuse plans whose only effects are output changes,
  resource drift or pure moves.
- Moved resources (`previous_address`): shown in violations as `moved-from=` and in JSON.
- Test suite with real Terraform 1.17.2 and OpenTofu 1.13.9 fixtures, deterministic fuzzing against an
  independent oracle, mutation testing, example workflow tests, packaging test.
- Example config, example GitHub Actions workflow, CODEOWNERS example, CI workflow.
- Dev-only quality gates: ruff and mypy (strict) on `src/`, run by a separate CI job; dependabot for the
  GitHub Actions in use.
- README: Related tools comparison (Checkov, Trivy, Conftest, terraform-compliance, Sentinel, Terraguard, AI-agent
  guardrails): factual and date-stamped, with an allowlist test for its links.

### Security
- planfence is fail-closed: exit 0 only if the plan was fully understood and every enabled rule passed; every other
  failure, including output errors and startup failures, exits 2 (violations exit 1).
- Plans with things planfence cannot judge are refused (exit 2): `importing`, `action_invocations`,
  `deferred_changes`, unknown actions, errored or incomplete plans.
- `scope` treats every move as a change and requires both the new and the previous address to be
  allowlisted.
- Attribute values are never printed; output is pure ASCII; size, string and pattern limits apply.
