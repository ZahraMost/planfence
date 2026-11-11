"""Deterministic differential fuzzing of the whole CLI against an independent oracle.

Seeds: every real and handmade fixture (with secrets planted in before/after/...),
plus randomly generated plans. Each iteration applies random mutations (structure,
types, huge / non-ASCII strings, duplicated or reordered entries, byte-level
truncation / garbage), runs `cli.main`, and checks invariants:

  * exit code is in {0, 1, 2} and nothing escapes cli.main (an "internal error"
    report on stderr also counts as a failure: it would hide a bug behind exit 2)
  * stdout/stderr never contain a planted attribute value, and are pure ASCII
  * exit code equals the reference oracle below (so exit 0 only when the plan
    parses AND the oracle finds no violation)

The oracle is deliberately simple and shares no code with planguard: it works on
raw bytes and plain dicts and uses a regex for wildcard matching.

Iterations: PLANGUARD_FUZZ_N (default 20000). Every iteration i is fully
determined by `random.Random(i)`; `gen_case(i)` reproduces it.
"""

import io
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from planguard import cli
from tests.conftest import SRC

N = int(os.environ.get("PLANGUARD_FUZZ_N", "20000"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRET = "PLANTED-SECRET-VALUE-7f3a9c"

# ---------------------------------------------------------------------------
# Configurations (python dict -> TOML text for planguard, same dict for the oracle)
# ---------------------------------------------------------------------------

CONFIGS = {
    "full": {
        "rules": ["no-delete", "no-replace", "scope", "stateful-protect", "budget"],
        "allow": ["terraform_data.*", "module.child.*", 'terraform_data.keyed["a"]', "?erraform_data.example", "aws_*.x"],
        "stateful": ["terraform_data"],
        "max_changes": 2,
        "creates": {"terraform_data": 1},
    },
    "delete-only": {"rules": ["no-delete"]},
    "star-budget0": {"rules": ["scope", "budget"], "allow": ["*"], "max_changes": 0},
    "replace-stateful": {
        "rules": ["no-replace", "stateful-protect", "scope"],
        "allow": ["terraform_data.example", "aws_db_instance.*"],
        "stateful": ["terraform_data", "aws_db_instance"],
    },
    "creates-only": {"rules": ["budget"], "creates": {"terraform_data": 0, "aws_instance": 2}},
    "tofu-full": {
        "rules": ["no-delete", "no-replace", "scope", "stateful-protect", "budget"],
        "plan_source": "opentofu",
        "allow": ["terraform_data.*", "module.child.*", 'terraform_data.keyed["a"]'],
        "stateful": ["terraform_data"],
        "max_changes": 2,
        "creates": {"terraform_data": 1},
    },
    "tofu-delete-only": {"rules": ["no-delete"], "plan_source": "opentofu"},
    "refuse-delete-only": {"rules": ["no-delete"], "state_only_plans": "refuse"},
    "refuse-full": {
        "rules": ["no-delete", "scope", "budget"],
        "state_only_plans": "refuse",
        "allow": ["terraform_data.*", "module.child.*", "module.m.*"],
        "max_changes": 3,
    },
    "tofu-refuse": {"rules": ["no-replace"], "plan_source": "opentofu", "state_only_plans": "refuse"},
    "allow-explicit": {"rules": ["no-delete"], "state_only_plans": "allow"},
}


def toml_of(cfg):
    lines = [f"enabled_rules = {json.dumps(cfg['rules'])}"]
    if "plan_source" in cfg:
        lines.append(f"plan_source = {json.dumps(cfg['plan_source'])}")
    if "state_only_plans" in cfg:
        lines.append(f"state_only_plans = {json.dumps(cfg['state_only_plans'])}")
    if "scope" in cfg["rules"]:
        lines += ["[scope]", f"allow = {json.dumps(cfg['allow'])}"]
    if "stateful-protect" in cfg["rules"]:
        lines += ["[stateful]", f"types = {json.dumps(cfg['stateful'])}"]
    if "budget" in cfg["rules"]:
        lines.append("[budget]")
        if "max_changes" in cfg:
            lines.append(f"max_changes = {cfg['max_changes']}")
        if "creates" in cfg:
            lines.append("[budget.max_creates_per_type]")
            lines += [f"{json.dumps(t)} = {n}" for t, n in cfg["creates"].items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Reference oracle (plain dicts, obviously-correct implementation of the contract)
# ---------------------------------------------------------------------------

KNOWN = [["no-op"], ["create"], ["read"], ["update"], ["delete"], ["forget"], ["delete", "create"], ["create", "delete"]]


class _Dup(ValueError):
    pass


def _nodups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _Dup(k)
        d[k] = v
    return d


def _good_str(v):
    return isinstance(v, str) and v != "" and len(v) <= 4096


def oracle_entries(doc, source="terraform"):
    """List of (address, type, actions, deposed) or None if the plan must be refused (exit 2)."""
    if not isinstance(doc, dict):
        return None
    fv = doc.get("format_version")
    if not (isinstance(fv, str) and fv.startswith("1.") and fv[2:].isascii() and fv[2:].isdigit()):
        return None
    if type(doc.get("errored")) is not bool:
        return None
    if source == "terraform" and type(doc.get("complete")) is not bool:
        return None
    if source == "opentofu" and "complete" in doc and type(doc["complete"]) is not bool:
        return None
    if doc["errored"] or ("complete" in doc and not doc["complete"]):
        return None
    for key in ("action_invocations", "deferred_changes"):
        if key in doc and not (isinstance(doc[key], list) and len(doc[key]) == 0):
            return None
    rcs = doc.get("resource_changes", [])
    if not isinstance(rcs, list):
        return None
    out = []
    for item in rcs:
        if not isinstance(item, dict):
            return None
        if not _good_str(item.get("address")) or not _good_str(item.get("type")):
            return None
        change = item.get("change")
        if not isinstance(change, dict):
            return None
        if change.get("importing") is not None:
            return None
        actions = change.get("actions")
        if not isinstance(actions, list) or actions not in KNOWN:
            return None
        deposed = None
        if "deposed" in item:
            if not _good_str(item["deposed"]):
                return None
            deposed = item["deposed"]
        previous = None
        if "previous_address" in item:
            if not _good_str(item["previous_address"]):
                return None
            if item["previous_address"] != item["address"]:  # equal addresses are not a move
                previous = item["previous_address"]
        out.append((item["address"], item["type"], actions, deposed, previous))
    return out


def wild(pattern, text):
    rx = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern)
    return re.fullmatch(rx, text, re.DOTALL) is not None


def oracle_violations(entries, cfg):
    rules = cfg["rules"]
    changed = [e for e in entries if e[2] not in (["no-op"], ["read"])]
    found = []
    for addr, typ, acts, _dep, _prev in entries:
        if "no-delete" in rules and acts == ["delete"]:
            found.append("no-delete")
        if "no-replace" in rules and "delete" in acts and "create" in acts:
            found.append("no-replace")
        if "stateful-protect" in rules and typ in cfg["stateful"]:
            if acts == ["delete"] or acts == ["forget"] or ("delete" in acts and "create" in acts):
                found.append("stateful")
    if "scope" in rules:
        # every change AND every move (even a no-op one); a move needs BOTH addresses allowed
        for addr, _t, acts, _d, prev in entries:
            if acts in (["no-op"], ["read"]) and prev is None:
                continue
            allowed = any(wild(p, addr) for p in cfg["allow"])
            if prev is not None:
                allowed = allowed and any(wild(p, prev) for p in cfg["allow"])
            if not allowed:
                found.append("scope")
    if "budget" in rules:
        if cfg.get("max_changes") is not None and len(changed) > cfg["max_changes"]:
            found.append("budget-total")
        for typ, limit in cfg.get("creates", {}).items():
            if sum(1 for e in changed if e[1] == typ and "create" in e[2]) > limit:
                found.append("budget-creates")
    return found


def oracle_state_only_refused(doc, entries, cfg):
    """state_only_plans = "refuse": zero changes (moves not counted) AND (a move, or an output_changes
    entry whose actions are not exactly ["no-op"], or a non-empty resource_drift). Malformed
    output_changes / resource_drift count as refused. Never consulted when something changes."""
    if cfg.get("state_only_plans", "allow") != "refuse":
        return False
    if any(e[2] not in (["no-op"], ["read"]) for e in entries):
        return False
    if any(e[4] is not None for e in entries):
        return True
    if "output_changes" in doc:
        oc = doc["output_changes"]
        if not isinstance(oc, dict):
            return True
        if any(not (isinstance(v, dict) and v.get("actions") == ["no-op"]) for v in oc.values()):
            return True
    if "resource_drift" in doc:
        rd = doc["resource_drift"]
        if not isinstance(rd, list) or len(rd) > 0:
            return True
    return False


def oracle(raw, cfg):
    try:
        doc = json.loads(raw, object_pairs_hook=_nodups)
    except (ValueError, RecursionError):
        return 2
    entries = oracle_entries(doc, cfg.get("plan_source", "terraform"))
    if entries is None or oracle_state_only_refused(doc, entries, cfg):
        return 2
    return 1 if oracle_violations(entries, cfg) else 0


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def plant(doc, variant=0):
    """Put the secret into every place attribute values live. `variant` varies output_changes /
    resource_drift so that every combination reaches the state_only_plans rule:
      0 outputs updated + drift non-empty, 1 all outputs no-op + empty drift, 2 {} / absent,
      3 keep whatever the real fixture has (secrets only inside resource_changes)."""
    for rc in doc.get("resource_changes") or []:
        ch = rc.get("change")
        if isinstance(ch, dict):
            ch["before"] = {"password": SECRET, "list": [SECRET]}
            ch["after"] = {"password": SECRET, "nested": {"k": SECRET}}
            ch["after_sensitive"] = {"password": SECRET}
    doc["planned_values"] = {"root_module": {"secret": SECRET}}
    if variant == 0:
        doc["output_changes"] = {"o": {"actions": ["update"], "before": SECRET, "after": SECRET}}
        doc["resource_drift"] = [{"address": "x.y", "type": "x", "change": {"actions": ["delete"], "before": SECRET}}]
    elif variant == 1:
        doc["output_changes"] = {"o": {"actions": ["no-op"], "before": SECRET, "after": SECRET}}
        doc["resource_drift"] = []
    elif variant == 2:
        doc["output_changes"] = {}
        doc.pop("resource_drift", None)
    return doc


def _load_seeds():
    seeds = []
    for sub_dir in ("real", "handmade", "opentofu", "partial/terraform", "partial/opentofu",
                    "moved/terraform", "moved/opentofu",
                    "outputs/terraform", "outputs/opentofu"):
        for path in sorted((FIXTURES / sub_dir).glob("*.json")):
            seeds.append((f"{sub_dir}/{path.stem}", plant(json.loads(path.read_text(encoding="utf-8")), len(seeds) % 4)))
    return seeds


SEEDS = _load_seeds()

ADDRESS_POOL = [
    "terraform_data.example", "module.child.terraform_data.inner", 'terraform_data.keyed["a"]',
    'terraform_data.keyed["b"]', "terraform_data.counted[0]", "aws_db_instance.db", "aws_db_instance.x",
    "aws_instance.x", "other.thing", "aws_s3.x",
]
TYPE_POOL = ["terraform_data", "aws_db_instance", "aws_instance", "other"]


def synth_plan(rng):
    changes = []
    for i in range(rng.randint(0, 12)):
        item = {
            "address": f"{rng.choice(ADDRESS_POOL)}{rng.choice(['', '', f'[{i}]'])}",
            "type": rng.choice(TYPE_POOL),
            "change": {"actions": list(rng.choice(KNOWN))},
        }
        if rng.random() < 0.1:
            item["deposed"] = f"{rng.randrange(16 ** 8):08x}"
        changes.append(item)
    doc = {"format_version": "1.2", "errored": False, "complete": True, "resource_changes": changes}
    if rng.random() < 0.15:
        del doc["resource_changes"]
    return plant(doc, rng.randrange(3))


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

NASTY = [
    "", "x", "é" * 10, "‮", "a\nb", "\x1b[31m", "\ud800", "😀", "\x00", "x" * 4095, "x" * 4096, "x" * 4097,
    "é" * 4096, "é" * 4097, "1.2", "1.99", "1.", "1.٣", "2.0", "0.9", "create", "delete", "no-op",
]
TOP_KEYS = ["format_version", "errored", "complete", "resource_changes", "action_invocations", "deferred_changes",
            "applyable", "prior_state", "output_changes", "resource_drift", "configuration", "timestamp"]
ITEM_KEYS = ["address", "type", "change", "deposed", "mode", "name", "index", "action_reason", "module_address",
             "previous_address"]
CHANGE_KEYS = ["actions", "importing", "before", "after", "after_sensitive", "replace_paths"]


def pool(rng):
    r = rng.random()
    if r < 0.30:
        return rng.choice([None, True, False, 0, 1, -1, 2 ** 70, 1.5, float("nan"), float("inf")])
    if r < 0.60:
        return rng.choice(NASTY)
    if r < 0.70:
        return rng.choice(ADDRESS_POOL)
    if r < 0.80:
        return list(rng.choice(KNOWN))
    if r < 0.90:
        return rng.choice([[], {}, [None], {"a": 1}, [[]], {"id": "x"}, ["x", 1]])
    return rng.choice(TYPE_POOL)


def descend(doc, rng):
    node, parent, key = doc, None, None
    while True:
        if isinstance(node, dict) and node:
            k = rng.choice(list(node))
        elif isinstance(node, list) and node:
            k = rng.randrange(len(node))
        else:
            break
        parent, key = node, k
        node = node[k]
        if rng.random() < 0.35:
            break
    return parent, key


def entries_of(doc):
    rcs = doc.get("resource_changes") if isinstance(doc, dict) else None
    return rcs if isinstance(rcs, list) and rcs else None


def pick_entry(doc, rng):
    rcs = entries_of(doc)
    if rcs is None:
        return None
    item = rcs[rng.randrange(len(rcs))]
    return item if isinstance(item, dict) else None


def m_delete(doc, rng):
    p, k = descend(doc, rng)
    if p is not None:
        del p[k]


def m_replace(doc, rng):
    p, k = descend(doc, rng)
    if p is not None:
        p[k] = pool(rng)


def m_wrap(doc, rng):
    p, k = descend(doc, rng)
    if p is not None:
        v = p[k]
        p[k] = rng.choice([[v], {"x": v}, str(v), [], [[v]], v is None, not v])


def m_add_key(doc, rng):
    p, k = descend(doc, rng)
    target = p[k] if p is not None and isinstance(p[k], dict) else doc
    keys = rng.choice([TOP_KEYS, ITEM_KEYS, CHANGE_KEYS])
    if isinstance(target, dict):
        target[rng.choice(keys)] = pool(rng)


def m_top(doc, rng):
    if isinstance(doc, dict):
        key = rng.choice(TOP_KEYS[:6])
        if rng.random() < 0.3:
            doc.pop(key, None)
        else:
            doc[key] = rng.choice([True, False, None, [], {}, "1.2", "1.0", "2.0", "x", 0, 1, [{"x": 1}], [], []])


def m_entry_field(doc, rng):
    item = pick_entry(doc, rng)
    if item is None:
        return
    which = rng.choice(["address", "type", "deposed", "actions", "importing", "actions", "address", "type",
                        "previous", "previous", "previous"])
    if which == "address":
        item["address"] = rng.choice(ADDRESS_POOL + NASTY)
    elif which == "type":
        item["type"] = rng.choice(TYPE_POOL + NASTY + [5, None])
    elif which == "previous":
        item["previous_address"] = rng.choice(ADDRESS_POOL + [item.get("address"), "", None, 5, "x" * 4097, "x" * 4096,
                                                              "terraform_data.old", "terraform_data.new", "module.child.x"])
    elif which == "deposed":
        item["deposed"] = rng.choice(["0001", "abcd", "", None, 5, "x" * 4097])
    elif which == "actions":
        ch = item.setdefault("change", {})
        if isinstance(ch, dict):
            ch["actions"] = rng.choice(KNOWN + KNOWN + [[], ["destroy"], ["create", "create"], ["update", "delete"],
                                                          ["no-op", "create"], "create", None, [1], [["create"]], ["Create"]])
    else:
        ch = item.setdefault("change", {})
        if isinstance(ch, dict):
            ch["importing"] = rng.choice([None, {"id": "x"}, {}, "x", 0, False])


def m_dup_entry(doc, rng):
    rcs = entries_of(doc)
    if rcs is not None:
        item = rcs[rng.randrange(len(rcs))]
        clone = json.loads(json.dumps(item))
        if rng.random() < 0.5 and isinstance(clone, dict) and isinstance(clone.get("change"), dict):
            clone["change"]["actions"] = list(rng.choice(KNOWN))
        rcs.insert(rng.randrange(len(rcs) + 1), clone)


def m_shuffle(doc, rng):
    rcs = entries_of(doc)
    if rcs is not None:
        rng.shuffle(rcs)


def m_drop_entry(doc, rng):
    rcs = entries_of(doc)
    if rcs is not None:
        del rcs[rng.randrange(len(rcs))]


def m_huge(doc, rng):
    item = pick_entry(doc, rng)
    if item is not None:
        field = rng.choice(["address", "type", "deposed"])
        item[field] = rng.choice(["x" * 4097, "x" * 70000, "é" * 5000, "🙂" * 4097])


def m_state(doc, rng):
    """Mutate output_changes / resource_drift (the sections state_only_plans = "refuse" looks at)."""
    if not isinstance(doc, dict):
        return
    which = rng.choice(["output_changes", "resource_drift"])
    r = rng.random()
    if r < 0.2:
        doc.pop(which, None)
    elif which == "output_changes":
        doc[which] = rng.choice([
            {}, {"o": {"actions": ["no-op"]}}, {"o": {"actions": ["update"]}}, {"o": {"actions": ["create"]}},
            {"o": {"actions": ["no-op"]}, "p": {"actions": ["delete"]}}, {"o": {}}, {"o": None}, {"o": {"actions": "no-op"}},
            {"o": {"actions": []}}, None, [], "x", 0, {"o": {"actions": ["no-op", "update"]}}, {"o": {"actions": ["read"]}},
        ])
    else:
        doc[which] = rng.choice([[], [{"address": "a.b"}], [None], None, {}, "x", 0, [{}]])


MUTATIONS = [m_state, m_state, m_delete, m_replace, m_wrap, m_add_key, m_top, m_entry_field, m_entry_field, m_dup_entry, m_shuffle,
             m_drop_entry, m_huge]


def mutate_bytes(raw, rng):
    kind = rng.choice(["truncate", "insert", "flip", "dupkey", "deep", "bom", "whole", "garbage-utf8", "empty"])
    if kind == "truncate" and raw:
        return raw[: rng.randrange(len(raw))]
    if kind == "insert" and raw:
        at = rng.randrange(len(raw) + 1)
        return raw[:at] + rng.choice([b"\xff", b"\x00", b",", b"}", b"\xc3\xa9", "😀".encode(), b'"', b"\\", b"/*", b" "]) + raw[at:]
    if kind == "flip" and raw:
        at = rng.randrange(len(raw))
        return raw[:at] + bytes([raw[at] ^ (1 << rng.randrange(8))]) + raw[at + 1:]
    if kind == "dupkey" and raw[:1] == b"{":
        extra = rng.choice([b'"errored":true,', b'"complete":false,', b'"format_version":"1.2",', b'"resource_changes":[],'])
        return b"{" + extra + raw[1:]
    if kind == "deep":
        depth = rng.choice([10, 500, 5000, 100000])
        return b'{"a":' * depth + b"1" + b"}" * depth
    if kind == "bom":
        return b"\xef\xbb\xbf" + raw
    if kind == "whole":
        return rng.choice([b"[]", b"null", b"{}", b"1", b'"x"', b"true", b"{\"format_version\":\"1.2\"}"])
    if kind == "garbage-utf8":
        return bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64)))
    return b""


def gen_case(i):
    """Deterministic case for iteration i: (raw bytes, config name, output format, description)."""
    rng = random.Random(i)
    if rng.random() < 0.5:
        name, seed = SEEDS[rng.randrange(len(SEEDS))]
        doc = json.loads(json.dumps(seed))
    else:
        name, doc = "synthetic", synth_plan(rng)
    n_mut = rng.choice([0, 0, 1, 1, 2, 3, 4])
    for _ in range(n_mut):
        rng.choice(MUTATIONS)(doc, rng)
    raw = json.dumps(doc).encode("utf-8")
    if rng.random() < 0.10:
        raw = mutate_bytes(raw, rng)
    cfg_name = rng.choice(list(CONFIGS))
    fmt = rng.choice(["text", "json"])
    return raw, cfg_name, fmt, f"seed={name} mutations={n_mut}"


# ---------------------------------------------------------------------------
# Running and checking
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cfg_paths(tmp_path_factory):
    d = tmp_path_factory.mktemp("fuzzcfg")
    paths = {}
    for name, cfg in CONFIGS.items():
        p = d / f"{name}.toml"
        p.write_text(toml_of(cfg), encoding="utf-8")
        paths[name] = p
    return paths


def run_cli(raw, cfg_path, fmt, plan_path):
    plan_path.write_bytes(raw)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main([str(plan_path), "--config", str(cfg_path), "--format", fmt])
    return code, out.getvalue(), err.getvalue()


def check(i, raw, cfg_name, fmt, desc, code, out, err):
    where = f"iteration={i} config={cfg_name} format={fmt} {desc}\nraw[:2000]={raw[:2000]!r}"
    assert code in (0, 1, 2), f"exit code {code!r}\n{where}"
    assert "internal error" not in err and "internal error" not in out, f"hidden crash\nstderr={err!r}\n{where}"
    assert SECRET not in out and SECRET not in err, f"attribute value leaked\n{where}"
    assert out.isascii() and err.isascii(), f"non-ASCII output\n{where}"
    expected = oracle(raw, CONFIGS[cfg_name])
    assert code == expected, f"exit {code}, oracle says {expected}\nstdout={out[:500]!r}\nstderr={err[:500]!r}\n{where}"
    if code in (0, 1):
        assert err == "", f"stderr not empty on exit {code}: {err!r}\n{where}"
    if code == 0:
        assert ("OK" in out) if fmt == "text" else ('"pass"' in out), where
    if code == 1:
        assert ("FAIL" in out) if fmt == "text" else ('"fail"' in out), where
    if code == 2 and fmt == "text":
        assert "OK" not in out, where


def test_fuzz_differential(cfg_paths, tmp_path):
    plan_path = tmp_path / "plan.json"
    outcomes = Counter()
    start = time.perf_counter()
    for i in range(N):
        raw, cfg_name, fmt, desc = gen_case(i)
        try:
            code, out, err = run_cli(raw, cfg_paths[cfg_name], fmt, plan_path)
        except BaseException as exc:  # nothing may escape cli.main
            pytest.fail(f"uncaught {exc!r}\niteration={i} config={cfg_name} format={fmt} {desc}\nraw[:2000]={raw[:2000]!r}")
        check(i, raw, cfg_name, fmt, desc, code, out, err)
        outcomes[code] += 1
    elapsed = time.perf_counter() - start
    assert elapsed < 30, f"{N} mutations took {elapsed:.1f}s"
    if N >= 2000:  # the test must not be vacuous: every outcome is well represented
        scale = N / 20000
        for code in (0, 1, 2):
            assert outcomes[code] >= 200 * scale, f"too few exit-{code} cases: {dict(outcomes)}"


def sub(args, stdin_env=None):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run([sys.executable, "-m", "planguard", *map(str, args)], capture_output=True, text=True, env=env)


def test_fuzz_sample_through_real_process(cfg_paths, tmp_path):
    plan_path = tmp_path / "plan.json"
    checked = Counter()
    for i in range(5, max(N, 2000), max(N, 2000) // 30):
        raw, cfg_name, fmt, desc = gen_case(i)
        plan_path.write_bytes(raw)
        proc = sub([plan_path, "--config", cfg_paths[cfg_name], "--format", fmt])
        where = f"iteration={i} config={cfg_name} format={fmt} {desc}\nraw[:2000]={raw[:2000]!r}"
        assert proc.returncode in (0, 1, 2), where
        assert "Traceback" not in proc.stderr and "internal error" not in proc.stderr, f"{proc.stderr}\n{where}"
        assert SECRET not in proc.stdout and SECRET not in proc.stderr, where
        assert proc.returncode == oracle(raw, CONFIGS[cfg_name]), f"{proc.stdout[:300]!r} {proc.stderr[:300]!r}\n{where}"
        checked[proc.returncode] += 1
    assert sum(checked.values()) >= 25


# ---------------------------------------------------------------------------
# The oracle itself must be right (sanity checks on known cases)
# ---------------------------------------------------------------------------

def test_oracle_on_unmutated_real_fixtures():
    cfg = CONFIGS["full"]
    expect = {"create": 0, "update": 0, "noop": 0, "empty": 0, "read": 0, "delete": 1, "replace": 1, "forget": 1,
              "import": 2, "import_noop": 2}
    for name, code in expect.items():
        raw = (FIXTURES / "real" / f"{name}.json").read_bytes()
        assert oracle(raw, cfg) == code, name
    assert oracle((FIXTURES / "handmade" / "deferred_changes.json").read_bytes(), cfg) == 2


def test_oracle_plan_source_modes():
    tf_cfg, tofu_cfg = CONFIGS["full"], CONFIGS["tofu-full"]
    expect = {"create": 0, "update": 0, "noop": 0, "delete": 1, "replace": 1, "import": 2}
    for name, code in expect.items():
        tofu_raw = (FIXTURES / "opentofu" / f"{name}.json").read_bytes()
        assert oracle(tofu_raw, tofu_cfg) == code, name       # accepted (or judged) in opentofu mode
        assert oracle(tofu_raw, tf_cfg) == 2, name            # strict mode refuses: no `complete`
        tf_raw = (FIXTURES / "real" / f"{name}.json").read_bytes()
        assert oracle(tf_raw, tofu_cfg) == oracle(tf_raw, tf_cfg), name
    partial = (FIXTURES / "partial" / "terraform" / "two_target.json").read_bytes()
    assert oracle(partial, tofu_cfg) == 2 and oracle(partial, tf_cfg) == 2  # complete=false


def test_wild_oracle_semantics():
    assert wild("a.*", "a.b.c") and wild("a?c", "abc") and not wild("a?c", "ac")
    assert wild("a.b[0]", "a.b[0]") and not wild("a.b[0]", "a.b0") and not wild("A", "a")
    assert wild("*", "") and wild("a*", "a") and not wild("a*", "b")


def test_generator_is_deterministic():
    assert gen_case(123) == gen_case(123)
    assert gen_case(123) != gen_case(124)


def test_oracle_state_only_rule_on_real_fixtures():
    refuse = CONFIGS["refuse-delete-only"]
    allow = CONFIGS["allow-explicit"]
    expect_refused = {("outputs", "output_added_only"), ("modes", "refresh_only_output_drift"),
                      ("moved", "moved_only"), ("moved", "moved_into_module")}
    cases = [("outputs", "output_unchanged"), ("outputs", "output_changes_with_resource"),
             ("outputs", "output_added_only"), ("modes", "refresh_only"), ("modes", "refresh_only_output_drift"),
             ("moved", "moved_only"), ("moved", "moved_update"), ("moved", "moved_into_module")]
    for directory, name in cases:
        raw = (FIXTURES / directory / "terraform" / f"{name}.json").read_bytes()
        assert oracle(raw, allow) == 0, name
        assert oracle(raw, refuse) == (2 if (directory, name) in expect_refused else 0), name
    drift = (FIXTURES / "handmade" / "resource_drift.json").read_bytes()
    assert oracle(drift, allow) == 0 and oracle(drift, refuse) == 2
