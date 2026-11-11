"""Generate REAL Terraform/OpenTofu plan JSON fixtures.

Each scenario runs in its own throwaway temp directory containing only the
built-in `terraform_data` resource (local state, creates nothing real).
Where a scenario needs existing state, the baseline config is applied first
(this is the single allowed `apply` exception documented in CLAUDE.md).

Output: tests/fixtures/real/<scenario>.json  (output of `show -json <planfile>`)

Usage:
    python scripts/make_fixtures.py [--binary terraform|tofu|/path/to/bin]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "tests" / "fixtures" / "real"


def res(input_value: str = "a", trigger: str = "a", cbd: bool = False) -> str:
    lifecycle = "\n  lifecycle {\n    create_before_destroy = true\n  }\n" if cbd else ""
    return (
        'resource "terraform_data" "example" {\n'
        f'  input            = "{input_value}"\n'
        f'  triggers_replace = ["{trigger}"]\n'
        f"{lifecycle}"
        "}\n"
    )


# scenario -> (baseline config or None if no prior state, planned config)
SCENARIOS: dict[str, tuple[str | None, str]] = {
    "create": (None, res()),
    "update": (res(input_value="a"), res(input_value="b")),
    "delete": (res(), ""),
    "replace": (res(trigger="a"), res(trigger="b")),
    "replace_cbd": (res(trigger="a", cbd=True), res(trigger="b", cbd=True)),
    "noop": (res(), res()),
}


def find_binary(requested: str | None) -> str:
    candidates = [requested] if requested else ["terraform", "tofu"]
    for name in candidates:
        path = shutil.which(name) if name else None
        if path:
            return path
    sys.exit(f"error: no terraform/tofu binary found (tried {candidates})")


def run(binary: str, args: list[str], cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.run(
        [binary, *args], cwd=cwd, env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(f"error: `{Path(binary).name} {' '.join(args)}` failed in {cwd}")
    return proc.stdout


def make_scenario(binary: str, name: str, baseline: str | None, planned: str) -> dict:
    env = dict(os.environ, CHECKPOINT_DISABLE="1", TF_IN_AUTOMATION="1")
    with tempfile.TemporaryDirectory(prefix=f"planguard-{name}-") as tmp:
        work = Path(tmp)
        main_tf = work / "main.tf"
        main_tf.write_text(baseline if baseline is not None else planned, encoding="utf-8")
        run(binary, ["init", "-input=false", "-no-color"], work, env)
        if baseline is not None:
            run(binary, ["apply", "-auto-approve", "-input=false", "-no-color"], work, env)
            main_tf.write_text(planned, encoding="utf-8")
        run(binary, ["plan", "-input=false", "-no-color", "-out=tfplan"], work, env)
        raw = run(binary, ["show", "-json", "tfplan"], work, env)
    # temp dir (state, plan, .terraform) is deleted on leaving the `with` block
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary", help="terraform or tofu executable (default: auto-detect)")
    args = ap.parse_args(argv)
    binary = find_binary(args.binary)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, (baseline, planned) in SCENARIOS.items():
        data = make_scenario(binary, name, baseline, planned)
        out = OUT_DIR / f"{name}.json"
        out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        actions = [rc["change"]["actions"] for rc in data.get("resource_changes", [])]
        print(f"{name:12s} -> {out.relative_to(ROOT)}  actions={actions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
