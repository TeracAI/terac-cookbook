import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIEF = "Synthetic Oracle CLI attachment for bounded status observation."
BRIEF_SHA256 = "67c35c5791087e5e7f6fa2a05f141b68c81b8415b5eb33389aca88f61204e815"


def _cli_environment() -> dict[str, str]:
    return {
        name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ
    } | {"PYTHONPATH": str(ROOT)}


def _run_cli(state_dir: Path, binding_path: Path | None, *arguments: str):
    command = [sys.executable, "-m", "recipes.oracle", "--state-dir", str(state_dir)]
    if binding_path is not None:
        command.extend(["--oracle-live-binding", str(binding_path)])
    command.extend(arguments)
    return subprocess.run(
        command,
        cwd=ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _write_binding(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "template_version": 1,
                "read_only": True,
                "organization_id": "synthetic-org",
                "project_id": "synthetic-project",
                "opportunity_id": "synthetic-study",
                "brief_sha256": BRIEF_SHA256,
                "definition_sha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )


def test_cli_attaches_and_inspects_configured_live_run_without_credentials(tmp_path):
    binding_path = tmp_path / "reviewed-binding.json"
    _write_binding(binding_path)
    state_dir = tmp_path / "state"

    started = _run_cli(
        state_dir,
        binding_path,
        "start",
        "--brief",
        BRIEF,
        "--mode",
        "live",
    )

    assert started.returncode == 0, started.stderr
    attached = json.loads(started.stdout)
    assert attached["mode"] == "live"
    assert attached["status"] == "working"
    assert attached["phase"] == "awaiting_provider_refresh"
    assert attached["provider_observation"] is None
    assert "synthetic-study" not in started.stdout

    checkpoint = state_dir / f"{attached['run_id']}.json"
    original_bytes = checkpoint.read_bytes()
    inspected = _run_cli(state_dir, binding_path, "inspect", attached["run_id"])

    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == attached
    assert checkpoint.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "invalid_arguments",
    [
        ("--replay-case", "fail"),
        ("--intent", "follow_up"),
    ],
)
def test_cli_configured_live_invalid_start_reports_request_error(tmp_path, invalid_arguments):
    binding_path = tmp_path / "reviewed-binding.json"
    _write_binding(binding_path)
    state_dir = tmp_path / "state"

    result = _run_cli(
        state_dir,
        binding_path,
        "start",
        "--brief",
        BRIEF,
        "--mode",
        "live",
        *invalid_arguments,
    )

    assert result.returncode == 2
    error = json.loads(result.stderr)
    assert error.get("category", error["error"]) == "invalid_request_or_checkpoint"
    assert result.stdout == ""
    assert not list(state_dir.glob("*.json"))


def test_cli_live_start_without_binding_reports_unavailable(tmp_path):
    state_dir = tmp_path / "state"

    result = _run_cli(state_dir, None, "start", "--brief", BRIEF, "--mode", "live")

    assert result.returncode == 2
    assert json.loads(result.stderr) == {"error": "live_unavailable"}
    assert result.stdout == ""
    assert not list(state_dir.glob("*.json"))


def test_cli_live_unavailable_precedes_malformed_parent_without_binding(tmp_path):
    state_dir = tmp_path / "state"

    result = _run_cli(
        state_dir,
        None,
        "start",
        "--brief",
        BRIEF,
        "--mode",
        "live",
        "--parent-id",
        "not-a-uuid",
        "--intent",
        "follow_up",
    )

    assert result.returncode == 2
    assert json.loads(result.stderr) == {"error": "live_unavailable"}
    assert result.stdout == ""
    assert not list(state_dir.glob("*.json"))


def test_cli_live_resume_without_credential_preserves_waiting_checkpoint(tmp_path):
    binding_path = tmp_path / "reviewed-binding.json"
    _write_binding(binding_path)
    state_dir = tmp_path / "state"
    started = _run_cli(
        state_dir,
        binding_path,
        "start",
        "--brief",
        BRIEF,
        "--mode",
        "live",
    )
    assert started.returncode == 0, started.stderr
    run = json.loads(started.stdout)
    checkpoint = state_dir / f"{run['run_id']}.json"
    original_bytes = checkpoint.read_bytes()

    resumed = _run_cli(state_dir, binding_path, "resume", run["run_id"])

    assert resumed.returncode == 2
    assert json.loads(resumed.stderr) == {"error": "credential_unavailable"}
    assert resumed.stdout == ""
    assert checkpoint.read_bytes() == original_bytes


@pytest.mark.parametrize(
    ("intent", "child_brief", "parent_mode"),
    [
        ("follow_up", BRIEF, "live"),
        ("new_query", "Changed synthetic task", "replay"),
    ],
)
def test_cli_rejects_linked_live_start_without_rewriting_parent(
    tmp_path, intent, child_brief, parent_mode
):
    binding_path = tmp_path / "reviewed-binding.json"
    _write_binding(binding_path)
    state_dir = tmp_path / "state"
    parent_result = _run_cli(
        state_dir,
        binding_path,
        "start",
        "--brief",
        BRIEF,
        "--mode",
        parent_mode,
    )
    assert parent_result.returncode == 0, parent_result.stderr
    parent = json.loads(parent_result.stdout)
    checkpoint = state_dir / f"{parent['run_id']}.json"
    original_bytes = checkpoint.read_bytes()

    linked = _run_cli(
        state_dir,
        binding_path,
        "start",
        "--brief",
        child_brief,
        "--mode",
        "live",
        "--parent-id",
        parent["run_id"],
        "--intent",
        intent,
    )

    assert linked.returncode == 2
    assert json.loads(linked.stderr) == {"error": "live_linked_work_unavailable"}
    assert linked.stdout == ""
    assert set(state_dir.glob("*.json")) == {checkpoint}
    assert checkpoint.read_bytes() == original_bytes
