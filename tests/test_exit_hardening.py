"""Any failure anywhere (printing, startup, import) must end in exit code 2, never 0 or 1."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planfence import __main__ as entry
from planfence import cli
from tests.conftest import SRC, fixture_path, make_plan


CFG = 'enabled_rules = ["no-delete"]\n'


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "planfence.toml"
    p.write_text(CFG, encoding="utf-8")
    return p


def plan_file(tmp_path, *changes):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(make_plan(*changes)), encoding="utf-8")
    return p


class FailingStream(io.StringIO):
    """stdout whose write and/or flush raise."""

    def __init__(self, write_exc=None, flush_exc=None):
        super().__init__()
        self.write_exc, self.flush_exc = write_exc, flush_exc

    def write(self, s):
        if self.write_exc:
            raise self.write_exc
        return super().write(s)

    def flush(self):
        if self.flush_exc:
            raise self.flush_exc


ERRORS = [
    BrokenPipeError(32, "Broken pipe"),
    UnicodeEncodeError("ascii", "é", 0, 1, "ordinal not in range(128)"),
    OSError(28, "No space left on device"),
    ValueError("I/O operation on closed file"),
    RuntimeError("weird"),
]
IDS = [type(e).__name__ for e in ERRORS]


@pytest.mark.parametrize("exc", ERRORS, ids=IDS)
@pytest.mark.parametrize("where", ["write", "flush"])
@pytest.mark.parametrize("fmt", ["text", "json"])
@pytest.mark.parametrize("scenario", ["pass", "violation"])
def test_failing_stdout_exits_2(monkeypatch, capsys, cfg, tmp_path, exc, where, fmt, scenario):
    changes = [("a.b", "a", ["update" if scenario == "pass" else "delete"])]
    stream = FailingStream(**{f"{where}_exc": exc})
    monkeypatch.setattr(sys, "stdout", stream)
    code = cli.main([str(plan_file(tmp_path, *changes)), "--config", str(cfg), "--format", fmt])
    assert code == 2


def test_failing_stdout_on_error_path_exits_2(monkeypatch, cfg, tmp_path):
    monkeypatch.setattr(sys, "stdout", FailingStream(write_exc=BrokenPipeError()))
    code = cli.main([str(tmp_path / "missing.json"), "--config", str(cfg), "--format", "json"])
    assert code == 2


def test_failing_stderr_on_error_path_exits_2(monkeypatch, cfg, tmp_path):
    monkeypatch.setattr(sys, "stderr", FailingStream(write_exc=OSError("boom")))
    assert cli.main([str(tmp_path / "missing.json"), "--config", str(cfg)]) == 2


def test_healthy_stdout_still_works(monkeypatch, cfg, tmp_path):
    stream = FailingStream()
    monkeypatch.setattr(sys, "stdout", stream)
    assert cli.main([str(plan_file(tmp_path, ("a.b", "a", ["update"]))), "--config", str(cfg)]) == 0
    assert "OK" in stream.getvalue()


# ---- non-ASCII output never raises UnicodeEncodeError ------------------------

def ascii_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict", newline="\n")


NON_ASCII = "caf\u00e9-\u8d44\u6e90-\U0001f600-\ud800"  # includes a lone surrogate


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_non_ascii_address_with_strict_ascii_stdout(monkeypatch, cfg, tmp_path, fmt):
    out = ascii_stream()
    monkeypatch.setattr(sys, "stdout", out)
    p = plan_file(tmp_path, (f"a.{NON_ASCII}", f"t{NON_ASCII}", ["delete"], NON_ASCII))
    code = cli.main([str(p), "--config", str(cfg), "--format", fmt])
    assert code == 1  # a real violation, not an output failure
    out.flush()
    raw = out.buffer.getvalue()
    raw.decode("ascii")  # must be pure ASCII
    assert b"\\xe9" in raw or b"\\u00e9" in raw


def test_non_ascii_in_config_error_message_does_not_crash(monkeypatch, tmp_path):
    out, err = ascii_stream(), ascii_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    c = tmp_path / "c.toml"
    c.write_text('enabled_rules = ["nö-such-rule"]\n', encoding="utf-8")
    code = cli.main([str(fixture_path("update")), "--config", str(c), "--format", "json"])
    assert code == 2
    out.flush()
    err.flush()
    out.buffer.getvalue().decode("ascii")
    err.buffer.getvalue().decode("ascii")


def test_safe_escapes_non_ascii_and_controls():
    s = cli._safe("aé资\U0001f600\x1b\n\x00z")
    s.encode("ascii")
    assert s.startswith("a") and s.endswith("z") and "\x1b" not in s and "\n" not in s


# ---- guard around main(): run() ----------------------------------------------

def test_run_passes_through_normal_codes(monkeypatch):
    for code in (0, 1, 2):
        monkeypatch.setattr(cli, "main", lambda argv=None, c=code: c)
        assert entry.run([]) == code


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("x"), KeyboardInterrupt(), SystemExit(1), SystemExit(0), SystemExit(None), MemoryError(), BrokenPipeError()],
    ids=lambda e: type(e).__name__ + str(getattr(e, "code", "")),
)
def test_run_maps_any_escape_to_2(monkeypatch, exc):
    def boom(argv=None):
        raise exc

    monkeypatch.setattr(cli, "main", boom)
    assert entry.run([]) == 2


@pytest.mark.parametrize("bad", [None, 3, -1, 130, True, "0", 1.0])
def test_run_maps_unexpected_return_values_to_2(monkeypatch, bad):
    monkeypatch.setattr(cli, "main", lambda argv=None: bad)
    assert entry.run([]) == 2


def test_run_final_flush_failure_exits_2(monkeypatch):
    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    monkeypatch.setattr(sys, "stdout", FailingStream(flush_exc=BrokenPipeError()))
    assert entry.run([]) == 2


def test_help_exits_0_and_usage_errors_2(capsys):
    assert entry.run(["--help"]) == 0
    assert "usage" in capsys.readouterr().out
    assert entry.run([]) == 2
    assert entry.run(["x.json", "--format", "xml"]) == 2


# ---- real processes ----------------------------------------------------------

def sub(args, env_extra=None, stdout=subprocess.PIPE, pythonpath=SRC):
    env = dict(os.environ, PYTHONPATH=str(pythonpath), **(env_extra or {}))
    return subprocess.run(
        [sys.executable, "-m", "planfence", *map(str, args)],
        stdout=stdout, stderr=subprocess.PIPE, text=True, env=env,
    )


@pytest.mark.parametrize("scenario, fixture", [("pass", "update"), ("violation", "delete")])
@pytest.mark.parametrize("fmt", ["text", "json"])
def test_closed_stdout_pipe_exits_2(cfg, scenario, fixture, fmt):
    r, w = os.pipe()
    os.close(r)  # nobody reads: writes raise BrokenPipeError
    try:
        proc = sub([fixture_path(fixture), "--config", cfg, "--format", fmt], stdout=w)
    finally:
        os.close(w)
    assert proc.returncode == 2, proc.stderr


def test_ascii_only_terminal_real_process(cfg, tmp_path):
    p = plan_file(tmp_path, (f"a.{NON_ASCII}", "t", ["delete"]))
    proc = sub([p, "--config", cfg], env_extra={"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"})
    assert proc.returncode == 1, proc.stderr
    proc.stdout.encode("ascii")
    assert "FAIL" in proc.stdout


def test_import_failure_outside_main_exits_2(tmp_path):
    broken = tmp_path / "pkgroot"
    shutil.copytree(SRC / "planfence", broken / "planfence", ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "planfence" / "cli.py").write_text("raise RuntimeError('import-time failure')\n", encoding="utf-8")
    proc = sub([fixture_path("update")], pythonpath=broken)
    assert proc.returncode == 2
    assert "OK" not in proc.stdout


def test_syntax_error_in_module_exits_2(tmp_path):
    broken = tmp_path / "pkgroot"
    shutil.copytree(SRC / "planfence", broken / "planfence", ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "planfence" / "rules.py").write_text("def (:\n", encoding="utf-8")
    proc = sub([fixture_path("update")], pythonpath=broken)
    assert proc.returncode == 2
