# Security policy

## Supported versions

Only the latest `0.1.x` release and the current `main` branch are supported.
Older versions are not supported; fixes are not backported.

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub's private vulnerability reporting
(security advisories): open the repository's **Security** tab and choose **Report a vulnerability**.
Do not open a public issue or pull request for a vulnerability.

Please include:

- the output of `planfence --version` and the Terraform or OpenTofu version that produced the plan;
- a minimal plan JSON and `planfence.toml` that reproduce the problem (remove any secrets from the
  plan first: plan files contain attribute values);
- what you expected and what planfence did (exit code and output).

Reports are handled on a best-effort basis; there is no service-level agreement.

## What is a vulnerability (In scope)

- A **false pass**: a plan that violates the contract according to the documented rules, but planfence
  exits 0 (including bypasses of `scope`, `no-delete`, `no-replace`, `stateful-protect` or `budget`).
- A **wrong exit code**: exit 0 or 1 when the plan was not fully understood (it must be 2), or any
  failure path (crash, closed pipe, encoding error) that ends in anything but exit 2.
- Output that prints **attribute values** (possible secrets), or that lets an untrusted plan inject
  terminal escape sequences or fake output lines.
- A crash, hang or excessive resource use caused by an untrusted plan file or contract file within the
  documented limits.
- A flaw in the example workflow or documented CI guidance that lets a pull request weaken the contract.

## Out of scope

These are documented limitations, not vulnerabilities (see the README):

- anything planfence states it does not check: security of the infrastructure, cost, configuration
  correctness, live state, provisioners (`local-exec`, `remote-exec`), data source side effects (for
  example the `external` data source runs during `terraform plan`), what a create actually does;
- `output_changes` and `resource_drift` unless `state_only_plans = "refuse"` is set;
- OpenTofu plans made with `-target` or `-exclude`, which cannot be recognised as partial;
- plans from Terraform/OpenTofu or provider versions that were not verified (planfence does not check
  `terraform_version` or providers; pin them in CI);
- a contract that is too permissive, such as a lone `*` in `scope.allow`, or a contract that a pull
  request can edit (load it from the base branch);
- vulnerabilities in Terraform, OpenTofu, providers or GitHub Actions themselves.

## What planfence does not guarantee

planfence is a narrow, deterministic guardrail. A pass is not an approval: exit 0 means only that this
plan, as written, stays inside the contract you wrote. It does not guarantee that applying the plan is
safe, that the plan is the one that will be applied (apply exactly the plan file you checked), or that
the contract is complete. Use it in front of human review, not instead of it.
