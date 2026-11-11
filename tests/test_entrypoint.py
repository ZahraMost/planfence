import subprocess
import sys
from pathlib import Path

from tests.conftest import SRC



def test_python_dash_m_runs_and_fails_closed():
    """`python -m planfence` must work without installing, and must never exit 0 unimplemented."""
    proc = subprocess.run(
        [sys.executable, "-m", "planfence"],
        cwd=SRC,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stderr
