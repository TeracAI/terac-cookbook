"""Run the Windows-only PowerShell credential and probe seam tests."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell")


@pytest.mark.skipif(os.name != "nt" or POWERSHELL is None, reason="requires Windows PowerShell 5.1")
def test_terac_access_seams_offline():
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "tests" / "terac_access_tests.ps1"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture-only-key" not in result.stdout + result.stderr
    assert "sensitive-provider-value" not in result.stdout + result.stderr
