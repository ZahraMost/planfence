"""Structural contract of .github/workflows/publish.yml (read as plain text, no YAML dependency)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLISH = ROOT / ".github" / "workflows" / "publish.yml"
SHA_USE = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\s+#\s*v\d+(?:\.\d+)*\s*$")
KNOWN_SHAS = {  # release tag -> commit SHA, looked up with `gh api` (annotated tags dereferenced)
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-python": ("6d4fd22b20465ccc0f7354476588e8df7c9ca0e0", "v7.1.0"),
    "actions/upload-artifact": ("cf430e030ddbb5b0abf93d22962f4752f3646cd9", "v7.0.2"),
    "actions/download-artifact": ("9000827ccba6bdab643e8b6fd33ac0654aef8333", "v8.0.2"),
    "pypa/gh-action-pypi-publish": ("7245906fb61fa33a13554f9d5fb856fe61758086", "v1.14.3"),
}


def code(text):
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))


def jobs(text):
    """{job name: text of the job} (jobs sit at 2-space indent below `jobs:`)."""
    body = text[text.index("\njobs:\n") + 6:]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def run_blocks(job):
    """Text of every `run:` command (the line and the lines indented deeper than it)."""
    out, lines, i = [], job.splitlines(), 0
    while i < len(lines):
        stripped = lines[i].lstrip(" -")
        if stripped.startswith("run:"):
            indent = len(lines[i]) - len(lines[i].lstrip(" -"))
            out.append(lines[i])
            i += 1
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                out.append(lines[i])
                i += 1
        else:
            i += 1
    return "\n".join(out)


def problems(text):
    out = []
    c = code(text)
    on = c[c.index("\non:\n"):c.index("\njobs:")]
    if re.findall(r"^  (\w+):", on, re.M) != ["workflow_dispatch"]:
        out.append("the only trigger must be workflow_dispatch")
    if "target:" not in on or "type: choice" not in on or not re.search(r"-\s*testpypi\n\s*-\s*pypi\n", on):
        out.append("input target must be a choice of testpypi, pypi")
    if not re.search(r"tag:\n(?:\s+.*\n)*?\s+default: v0\.1\.1\n", on):
        out.append("input tag must default to v0.1.1")
    if "secrets." in c or re.search(r"(?<![\w-])(password|token|api[-_]?key)\s*:", c, re.I):
        out.append("no secrets, tokens or passwords")
    for n, line in enumerate(text.splitlines(), 1):
        if "uses:" in line and not line.strip().startswith("#") and not SHA_USE.match(line):
            out.append(f"line {n}: action not pinned by 40-hex SHA with a version comment")
    j = jobs(text)
    if set(j) != {"build", "publish"}:
        return [*out, f"jobs must be build and publish, found {sorted(j)}"]
    ids = [name for name, body in j.items() if "id-token:" in code(body)]
    if ids != ["publish"] or "id-token: write" not in j["publish"]:
        out.append("id-token: write only in the publish job")
    for name, body in j.items():
        perms = re.search(r"permissions:\n((?:\s{6,}[\w-]+: \w+\n)+)", body)
        want = {"build": ["contents: read"], "publish": ["id-token: write"]}[name]
        if not perms or [ln.strip() for ln in perms.group(1).splitlines()] != want:
            out.append(f"{name}: permissions must be exactly {want}")
        if "timeout-minutes:" not in body:
            out.append(f"{name}: timeout-minutes missing")
    if "environment: ${{ inputs.target }}" not in j["publish"]:
        out.append("publish environment must come from inputs.target")
    if "needs: build" not in j["publish"] or "environment:" in j["build"]:
        out.append("publish needs build; build has no environment")
    if "ref: refs/tags/${{ inputs.tag }}" not in j["build"]:
        out.append("build must check out refs/tags/${{ inputs.tag }}")
    if "run: python -m build" not in j["build"] or "twine check dist/*" not in j["build"]:
        out.append("build must run python -m build and twine check")
    if "tomllib" not in j["build"] or '"v" + version' not in j["build"]:
        out.append("build must compare the pyproject version with the tag")
    if "${{ inputs." in run_blocks(j["build"]):
        out.append("inputs must not be interpolated into shell code (use env)")
    if "repository-url: https://test.pypi.org/legacy/" not in j["publish"] or c.count("repository-url:") != 1:
        out.append("exactly one repository-url, the TestPyPI one")
    if "inputs.target == 'testpypi'" not in j["publish"] or "inputs.target == 'pypi'" not in j["publish"]:
        out.append("one publish step per target")
    return out


def test_publish_workflow_is_sound():
    assert problems(PUBLISH.read_text(encoding="utf-8")) == []


def test_every_action_is_pinned_to_the_verified_commit_sha_with_its_version_comment():
    text = PUBLISH.read_text(encoding="utf-8")
    used = re.findall(r"uses:\s*(\S+)@([0-9a-f]{40})\s+# (v[\d.]+)", text)
    assert {a for a, _, _ in used} == set(KNOWN_SHAS)
    for action, sha, comment in used:
        assert KNOWN_SHAS[action] == (sha, comment), action
    assert len(re.findall(r"uses:", text)) == len(used)


def test_publishing_is_manual_only_and_uses_no_credentials():
    text = PUBLISH.read_text(encoding="utf-8")
    assert "secrets." not in text
    for forbidden in ("push:", "pull_request", "schedule:", "release:", "password", "api-token", "PYPI_TOKEN"):
        assert forbidden not in text, forbidden


def _mut(old, new):
    text = PUBLISH.read_text(encoding="utf-8")
    assert old in text, old
    return text.replace(old, new, 1)


NEGATIVE = {
    "extra trigger": _mut("on:\n  workflow_dispatch:", "on:\n  push:\n  workflow_dispatch:"),
    "unpinned action": _mut("actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0", "actions/setup-python@v7"),
    "short sha": _mut("download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333", "download-artifact@9000827"),
    "no version comment": _mut(" # v8.0.2", ""),
    "id-token in build": _mut("      contents: read\n", "      contents: read\n      id-token: write\n"),
    "build id-token instead of contents": _mut("      contents: read\n", "      id-token: write\n"),
    "contents in publish": _mut("      id-token: write\n    steps:", "      id-token: write\n      contents: read\n    steps:"),
    "secret reference": _mut("name: dist\n          path: dist/\n          if-no",
                             "name: dist\n          path: dist/\n          x: ${{ secrets.T }}\n          if-no"),
    "fixed environment": _mut("environment: ${{ inputs.target }}", "environment: pypi"),
    "no environment": _mut("    environment: ${{ inputs.target }}\n", ""),
    "wrong checkout ref": _mut("ref: refs/tags/${{ inputs.tag }}", "ref: main"),
    "no version check": _mut('"v" + version', '"v" + "0"'),
    "no twine check": _mut("twine check dist/*", "true"),
    "tag in shell": _mut("tag = os.environ[\"TAG\"]", "tag = '${{ inputs.tag }}'"),
    "no timeout": _mut("    timeout-minutes: 10\n    environment", "    environment"),
    "password": _mut("          repository-url: https://test.pypi.org/legacy/\n",
                     "          repository-url: https://test.pypi.org/legacy/\n          password: x\n"),
    "testpypi url missing": _mut("repository-url: https://test.pypi.org/legacy/", "verbose: true"),
}


@pytest.mark.parametrize("name", sorted(NEGATIVE))
def test_contract_detects_broken_publish_workflows(name):
    assert problems(NEGATIVE[name]), f"not detected: {name}"
