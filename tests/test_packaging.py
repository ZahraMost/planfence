"""Build the wheel and sdist, install the wheel into a FRESH venv outside the repo and use it.

Needs the `build` package (a dev tool: `pip install build`) and network access for the isolated
build environment (setuptools). Skipped with a reason if it cannot run; set PLANFENCE_SKIP_SLOW=1
to skip it on purpose (scripts/mutation_check.py does, it is about packaging, not source mutations).
"""

import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import venv
import zipfile
from pathlib import Path

import pytest

from tests.conftest import SRC, fixture_path

ROOT = Path(__file__).resolve().parent.parent
NETWORK_HINTS = ("Could not find a version", "No matching distribution", "Connection", "connection",
                 "Temporary failure", "Name or service not known", "nodename nor servname", "timed out",
                 "Network is unreachable", "SSLError", "Max retries")

pytestmark = pytest.mark.skipif(os.environ.get("PLANFENCE_SKIP_SLOW") == "1", reason="PLANFENCE_SKIP_SLOW=1")

try:
    import build  # noqa: F401
    HAVE_BUILD = True
except ImportError:
    HAVE_BUILD = False


def project_version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    if not HAVE_BUILD:
        pytest.skip("the 'build' package is not installed (dev tool: pip install build)")
    work = tmp_path_factory.mktemp("pkg")
    proj = work / "proj"
    proj.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(ROOT / name, proj / name)
    shutil.copytree(SRC / "planfence", proj / "src" / "planfence", ignore=shutil.ignore_patterns("__pycache__"))
    dist = work / "dist"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PLANFENCE_SRC")}
    proc = subprocess.run([sys.executable, "-m", "build", "--outdir", str(dist), str(proj)],
                          capture_output=True, text=True, env=env, timeout=600)
    if proc.returncode != 0:
        log = proc.stdout + proc.stderr
        if any(hint in log for hint in NETWORK_HINTS):
            pytest.skip(f"cannot build here (no network for the isolated build environment?): {log[-300:]!r}")
        pytest.fail(f"building the distributions failed:\n{log[-2000:]}")
    wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    assert len(wheels) == 1 and len(sdists) == 1, list(dist.iterdir())
    return wheels[0], sdists[0], work


def wheel_text(wheel, suffix):
    with zipfile.ZipFile(wheel) as z:
        (name,) = [n for n in z.namelist() if n.endswith(suffix)]
        return z.read(name).decode("utf-8")


# ---- the wheel's contents ---------------------------------------------------------------------------

PACKAGE_FILES = {f"planfence/{m}.py" for m in ("__init__", "__main__", "cli", "config", "parser", "rules")}
DIST_INFO_FILES = {"licenses/LICENSE", "METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"}


def test_wheel_contains_exactly_the_planfence_package_and_its_metadata(artifacts):
    wheel, _, _ = artifacts
    version = project_version()
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
    expected = PACKAGE_FILES | {f"planfence-{version}.dist-info/{n}" for n in DIST_INFO_FILES}
    assert names == expected, (sorted(names - expected), sorted(expected - names))
    assert wheel.name == f"planfence-{version}-py3-none-any.whl"


def test_no_tests_fixtures_scripts_or_examples_leaked_into_the_wheel(artifacts):
    wheel, _, _ = artifacts
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
    for bad in ("test", "fixture", "script", "example", ".github", "mutation", "conftest", ".json", ".tf", ".venv"):
        assert not [n for n in names if bad in n.lower()], bad


def test_wheel_declares_one_top_level_package(artifacts):
    wheel, _, _ = artifacts
    with zipfile.ZipFile(wheel) as z:
        assert [n for n in z.namelist() if n.endswith("top_level.txt")]
    assert wheel_text(wheel, ".dist-info/top_level.txt").split() == ["planfence"]


def test_license_is_in_the_wheel_and_declared_mit(artifacts):
    wheel, _, _ = artifacts
    meta = wheel_text(wheel, ".dist-info/METADATA")
    assert "License-Expression: MIT" in meta
    assert "License-File: LICENSE" in meta
    text = wheel_text(wheel, ".dist-info/licenses/LICENSE")
    assert text == (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text and "ZahraMost" in text


def test_metadata_has_no_runtime_dependencies_and_requires_python_311(artifacts):
    wheel, _, _ = artifacts
    meta = wheel_text(wheel, ".dist-info/METADATA")
    assert "Requires-Python: >=3.11" in meta
    runtime = [ln for ln in meta.splitlines() if ln.startswith("Requires-Dist:") and "extra ==" not in ln]
    assert runtime == []
    assert f"Version: {project_version()}" in meta


def test_console_script_entry_point(artifacts):
    wheel, _, _ = artifacts
    entry_points = wheel_text(wheel, ".dist-info/entry_points.txt")
    assert "[console_scripts]" in entry_points and "planfence = planfence.__main__:run" in entry_points


def test_sdist_has_license_readme_and_sources(artifacts):
    _, sdist, _ = artifacts
    with tarfile.open(sdist) as t:
        names = t.getnames()
    root = f"planfence-{project_version()}"
    for expected in ("LICENSE", "README.md", "pyproject.toml", "src/planfence/cli.py", "src/planfence/rules.py"):
        assert f"{root}/{expected}" in names, expected
    assert not [n for n in names if "/.git/" in n or ".venv" in n or "__pycache__" in n]


# ---- install into a fresh venv outside the repo and use it ---------------------------------------------

@pytest.fixture(scope="module")
def installed(artifacts):
    wheel, _, work = artifacts
    env_dir = work / "fresh-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    bindir = env_dir / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    script = bindir / ("planfence.exe" if os.name == "nt" else "planfence")
    clean_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PLANFENCE_SRC", "VIRTUAL_ENV")}
    proc = subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check",
                           str(wheel)], capture_output=True, text=True, env=clean_env, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return python, script, clean_env, work


def test_fresh_venv_imports_the_installed_package_not_the_repo(installed):
    python, _, env, work = installed
    proc = subprocess.run([str(python), "-c", "import planfence; print(planfence.__file__)"],
                          capture_output=True, text=True, env=env, cwd=work)
    assert proc.returncode == 0, proc.stderr
    location = Path(proc.stdout.strip()).resolve()
    assert "fresh-venv" in str(location) and not str(location).startswith(str(ROOT))


def test_console_script_reports_the_version(installed):
    _, script, env, work = installed
    proc = subprocess.run([str(script), "--version"], capture_output=True, text=True, env=env, cwd=work)
    assert proc.returncode == 0 and proc.stderr == ""
    assert proc.stdout == f"planfence {project_version()}\n"


def test_console_script_checks_a_real_fixture(installed):
    _, script, env, work = installed
    cfg = work / "planfence.toml"
    cfg.write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    bad = subprocess.run([str(script), str(fixture_path("delete")), "--config", str(cfg)],
                         capture_output=True, text=True, env=env, cwd=work)
    assert bad.returncode == 1 and "[error] no-delete terraform_data.example" in bad.stdout
    good = subprocess.run([str(script), str(fixture_path("update")), "--config", str(cfg), "--format", "json"],
                          capture_output=True, text=True, env=env, cwd=work)
    assert good.returncode == 0 and '"result": "pass"' in good.stdout
    broken = subprocess.run([str(script), str(work / "missing.json"), "--config", str(cfg)],
                            capture_output=True, text=True, env=env, cwd=work)
    assert broken.returncode == 2 and "OK" not in broken.stdout


def test_python_dash_m_works_from_the_installed_package(installed):
    python, _, env, work = installed
    proc = subprocess.run([str(python), "-m", "planfence", "--version"], capture_output=True, text=True, env=env, cwd=work)
    assert proc.returncode == 0 and proc.stdout.startswith("planfence ")
