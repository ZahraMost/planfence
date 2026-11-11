"""The project was renamed (the old name collides with another PyPI project and its command).

The old name must not survive anywhere: no alias, no shim, no stray mention. It is built from pieces so
that this file does not contain it.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OLD = "plan" + "guard"
NEW = "plan" + "fence"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is required to list tracked files")


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    paths = [p for p in out.decode("utf-8").split("\0") if p]
    if not paths or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    return paths


def text_of(rel):
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ---- the new name is used everywhere it must be ----------------------------------------------------------

def test_distribution_package_and_console_script_names():
    assert PYPROJECT["project"]["name"] == NEW
    assert PYPROJECT["project"]["scripts"] == {NEW: f"{NEW}.__main__:run"}
    assert (ROOT / "src" / NEW / "__main__.py").is_file()
    assert not (ROOT / "src" / OLD).exists()


def test_the_package_imports_under_the_new_name_only():
    assert importlib.util.find_spec(NEW) is not None
    assert importlib.util.find_spec(OLD) is None  # no alias, no compatibility shim


def test_no_old_command_in_this_environment():
    scripts = Path(sys.executable).parent
    assert not (scripts / OLD).exists() and not (scripts / f"{OLD}.exe").exists()


def test_cli_program_name_prefix_and_default_config_name(capsys, tmp_path, monkeypatch):
    from planfence import cli

    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith(f"{NEW} ")
    cli.main(["--help"])
    assert f"usage: {NEW}" in capsys.readouterr().out
    monkeypatch.chdir(tmp_path)
    assert cli.main([str(ROOT / "tests" / "fixtures" / "real" / "update.json")]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"{NEW}: error:")


def test_default_config_file_is_named_after_the_new_project(capsys, tmp_path, monkeypatch):
    from planfence import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / f"{NEW}.toml").write_text('enabled_rules = ["no-delete"]\n', encoding="utf-8")
    assert cli.main([str(ROOT / "tests" / "fixtures" / "real" / "update.json")]) == 0
    assert capsys.readouterr().out.startswith(f"{NEW}: OK")
    (tmp_path / f"{NEW}.toml").rename(tmp_path / f"{OLD}.toml")  # the old file name is not picked up
    assert cli.main([str(ROOT / "tests" / "fixtures" / "real" / "update.json")]) == 2


def test_environment_variables_use_the_new_prefix():
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert f"{NEW.upper()}_SRC" in conftest and f"{OLD.upper()}_" not in conftest


# ---- the old name appears nowhere (except one explanatory CHANGELOG line) ---------------------------------

@needs_git
def test_no_tracked_path_contains_the_old_name():
    assert [p for p in tracked() if OLD in p.lower()] == []


@needs_git
def test_the_old_name_appears_only_in_one_changelog_line():
    hits = []
    for rel in tracked():
        text = text_of(rel)
        for n, line in enumerate((text or "").splitlines(), 1):
            if OLD in line.lower():
                hits.append((rel, n, line.strip()))
    assert [(rel, line) for rel, _, line in hits if rel != "CHANGELOG.md"] == []
    changelog = [line for rel, _, line in hits if rel == "CHANGELOG.md"]
    assert len(changelog) == 1 and "formerly" in changelog[0].lower() and "no alias" in changelog[0].lower()


@needs_git
def test_repository_urls_point_to_the_renamed_repository():
    urls = set()
    for rel in tracked():
        urls.update(re.findall(r"github\.com/ZahraMost/([\w.-]+?)(?:\.git|[/@\s\"')]|$)", text_of(rel) or ""))
    assert urls == {NEW}, urls


@needs_git
def test_new_name_is_used_in_the_main_documents():
    for rel in ("README.md", "CLAUDE.md", "SECURITY.md", "CHANGELOG.md", "examples/CODEOWNERS"):
        assert NEW in (text_of(rel) or ""), rel
    assert (ROOT / "examples" / f"{NEW}.toml").is_file()
    assert (ROOT / "examples" / "github-actions" / f"{NEW}-example.yml").is_file()
    assert text_of("README.md").lstrip("> ").startswith("**Status")
    assert f"# {NEW}\n" in text_of("README.md")
