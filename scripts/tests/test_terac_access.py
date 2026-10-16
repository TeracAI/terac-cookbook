"""Run the Windows-only PowerShell credential and probe seam tests."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell")


@pytest.mark.skipif(os.name != "nt" or POWERSHELL is None, reason="requires Windows PowerShell 5.1")
@pytest.mark.parametrize("module_paths", ["native", "incompatible"])
def test_terac_access_seams_offline(module_paths, tmp_path, monkeypatch):
    if module_paths == "incompatible":
        modules = tmp_path / "modules"
        security_module = modules / "Microsoft.PowerShell.Security"
        security_module.mkdir(parents=True)
        (security_module / "Microsoft.PowerShell.Security.psm1").write_text(
            "function ConvertTo-SecureString {\n"
            "    throw 'synthetic incompatible inherited module'\n"
            "}\n"
            "Export-ModuleMember -Function ConvertTo-SecureString\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PSModulePath", str(modules))

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
        # PowerShell 5.1 must rebuild native paths instead of loading PowerShell 7 modules.
        env={key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture-only-key" not in result.stdout + result.stderr
    assert "sensitive-provider-value" not in result.stdout + result.stderr
