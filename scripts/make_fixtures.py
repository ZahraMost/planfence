"""Generate REAL Terraform/OpenTofu plan JSON fixtures.

Each scenario runs in its own throwaway temp directory containing only the
built-in `terraform_data` resource / `terraform_remote_state` data source
(local state, creates nothing real). Where a scenario needs existing state,
the baseline config is applied first (the single allowed `apply` exception
documented in CLAUDE.md).

Output: tests/fixtures/real/<scenario>.json  (output of `show -json <planfile>`)

Usage:
    python scripts/make_fixtures.py [--binary terraform|tofu|PATH] [SCENARIO ...]
With no SCENARIO names, all scenarios are generated.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "tests" / "fixtures" / "real"

Files = dict[str, str]  # relative path -> file content


def res(input_value: str = "a", trigger: str = "a", cbd: bool = False) -> str:
    lifecycle = "\n  lifecycle {\n    create_before_destroy = true\n  }\n" if cbd else ""
    return (
        'resource "terraform_data" "example" {\n'
        f'  input            = "{input_value}"\n'
        f'  triggers_replace = ["{trigger}"]\n'
        f"{lifecycle}"
        "}\n"
    )


def counted(n: int) -> str:
    return (
        'resource "terraform_data" "counted" {\n'
        f"  count = {n}\n"
        '  input = "item-${count.index}"\n'
        "}\n"
    )


def keyed(values: dict[str, str]) -> str:
    items = ", ".join(f'"{k}" = "{v}"' for k, v in values.items())
    return (
        'resource "terraform_data" "keyed" {\n'
        f"  for_each = {{ {items} }}\n"
        "  input    = each.value\n"
        "}\n"
    )


MODULE_CHILD = 'variable "value" {}\n\nresource "terraform_data" "inner" {\n  input = var.value\n}\n'


def module_call(value: str) -> str:
    return f'module "child" {{\n  source = "./modules/child"\n  value  = "{value}"\n}}\n'


FORGET = (
    "removed {\n"
    "  from = terraform_data.example\n"
    "  lifecycle {\n"
    "    destroy = false\n"
    "  }\n"
    "}\n"
)

# Producer: a separate root module whose local state file is read by
# terraform_remote_state in the read scenarios.
PRODUCER: Files = {
    "main.tf": 'resource "terraform_data" "p" {\n  input = "x"\n}\n\noutput "greeting" {\n  value = "hello"\n}\n'
}

REMOTE_STATE = (
    'data "terraform_remote_state" "src" {\n'
    '  backend = "local"\n'
    "  config = {\n"
    '    path = "${path.module}/producer/terraform.tfstate"\n'
    "  }\n"
    "{depends}"
    "}\n"
)

READ_DEFERRED = (
    'resource "terraform_data" "gate" {\n  input = "g"\n}\n\n'
    + REMOTE_STATE.replace("{depends}", "  depends_on = [terraform_data.gate]\n")
)
READ_IMMEDIATE = REMOTE_STATE.replace("{depends}", "")


@dataclass
class Scenario:
    planned: Files
    baseline: Files | None = None  # applied first if not None
    producer: Files | None = None  # applied in ./producer before anything else
    note: str = ""
    extra: Files = field(default_factory=dict)  # files present in both phases


SCENARIOS: dict[str, Scenario] = {
    "create": Scenario(planned={"main.tf": res()}),
    "update": Scenario(baseline={"main.tf": res(input_value="a")}, planned={"main.tf": res(input_value="b")}),
    "delete": Scenario(baseline={"main.tf": res()}, planned={"main.tf": ""}),
    "replace": Scenario(baseline={"main.tf": res(trigger="a")}, planned={"main.tf": res(trigger="b")}),
    "replace_cbd": Scenario(
        baseline={"main.tf": res(trigger="a", cbd=True)}, planned={"main.tf": res(trigger="b", cbd=True)}
    ),
    "noop": Scenario(baseline={"main.tf": res()}, planned={"main.tf": res()}),
    "forget": Scenario(baseline={"main.tf": res()}, planned={"main.tf": FORGET}),
    "read": Scenario(planned={"main.tf": READ_DEFERRED}, producer=PRODUCER),
    "read_immediate": Scenario(planned={"main.tf": READ_IMMEDIATE}, producer=PRODUCER),
    "empty": Scenario(planned={"main.tf": ""}),
    "count": Scenario(baseline={"main.tf": counted(3)}, planned={"main.tf": counted(2)}),
    "for_each": Scenario(
        baseline={"main.tf": keyed({"a": "1", "b": "1"})},
        planned={"main.tf": keyed({"a": "2", "c": "1"})},
    ),
    "module": Scenario(
        baseline={"main.tf": module_call("a")},
        planned={"main.tf": module_call("b")},
        extra={"modules/child/main.tf": MODULE_CHILD},
    ),
}


def find_binary(requested: str | None) -> str:
    candidates = [requested] if requested else ["terraform", "tofu"]
    for name in candidates:
        path = shutil.which(name) if name else None
        if path:
            return path
    sys.exit(f"error: no terraform/tofu binary found (tried {candidates})")


def run(binary: str, args: list[str], cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.run([binary, *args], cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(f"error: `{Path(binary).name} {' '.join(args)}` failed in {cwd}")
    return proc.stdout


def write_files(base: Path, files: Files) -> None:
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def init_apply(binary: str, work: Path, env: dict[str, str]) -> None:
    run(binary, ["init", "-input=false", "-no-color"], work, env)
    run(binary, ["apply", "-auto-approve", "-input=false", "-no-color"], work, env)


def make_scenario(binary: str, name: str, sc: Scenario) -> dict:
    env = dict(os.environ, CHECKPOINT_DISABLE="1", TF_IN_AUTOMATION="1")
    with tempfile.TemporaryDirectory(prefix=f"planguard-{name}-") as tmp:
        work = Path(tmp)
        if sc.producer is not None:
            producer = work / "producer"
            write_files(producer, sc.producer)
            init_apply(binary, producer, env)
        write_files(work, sc.extra)
        if sc.baseline is not None:
            write_files(work, sc.baseline)
            init_apply(binary, work, env)
        write_files(work, sc.planned)
        run(binary, ["init", "-input=false", "-no-color"], work, env)
        run(binary, ["plan", "-input=false", "-no-color", "-out=tfplan"], work, env)
        raw = run(binary, ["show", "-json", "tfplan"], work, env)
    # temp dir (state, plan, .terraform) is deleted on leaving the `with` block
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary", help="terraform or tofu executable (default: auto-detect)")
    ap.add_argument("scenarios", nargs="*", help=f"subset of: {', '.join(SCENARIOS)}")
    args = ap.parse_args(argv)
    unknown = set(args.scenarios) - set(SCENARIOS)
    if unknown:
        ap.error(f"unknown scenario(s): {', '.join(sorted(unknown))}")
    binary = find_binary(args.binary)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in args.scenarios or SCENARIOS:
        data = make_scenario(binary, name, SCENARIOS[name])
        out = OUT_DIR / f"{name}.json"
        out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        summary = [(rc.get("address"), rc["change"]["actions"]) for rc in data.get("resource_changes", [])]
        print(f"{name:15s} -> {out.relative_to(ROOT)}  {summary if 'resource_changes' in data else 'NO resource_changes KEY'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
