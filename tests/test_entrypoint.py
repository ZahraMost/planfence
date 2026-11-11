import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def test_python_dash_m_runs_and_fails_closed():
    """`python -m planguard` must work without installing, and must never exit 0 unimplemented."""
    proc = subprocess.run(
        [sys.executable, "-m", "planguard"],
        cwd=SRC,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stderr
