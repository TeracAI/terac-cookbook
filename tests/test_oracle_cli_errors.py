import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNKNOWN_RUN_ID = "00000000-0000-4000-8000-000000000000"


def _run(state_dir, *args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, "-m", "recipes.oracle", "--state-dir", str(state_dir), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _error(result):
    assert result.returncode == 2
    assert result.stdout == ""
    return json.loads(result.stderr)


def _replay_run_after_clarification(state_dir):
    started = _run(state_dir, "start", "--brief", "Synthetic brief.")
    run_id = json.loads(started.stdout)["run_id"]
    _run(state_dir, "resume", run_id)
    answered = _run(state_dir, "respond", run_id, "--answer", "Synthetic answer.")
    assert answered.returncode == 0
    return run_id


@pytest.mark.parametrize("brief", ["", "   "])
def test_cli_reports_empty_brief(tmp_path, brief):
    error = _error(_run(tmp_path, "start", "--brief", brief))

    assert error == {"error": "invalid_brief", "category": "invalid_request_or_checkpoint"}
    assert not list(tmp_path.glob("*.json"))


def test_cli_reports_follow_up_without_parent(tmp_path):
    error = _error(_run(tmp_path, "start", "--brief", "b", "--intent", "follow_up"))

    assert error == {"error": "parent_required", "category": "invalid_request_or_checkpoint"}


@pytest.mark.parametrize("run_id", ["not-a-uuid", "../escape"])
def test_cli_reports_malformed_run_id(tmp_path, run_id):
    error = _error(_run(tmp_path, "inspect", run_id))

    assert error == {"error": "invalid_run_id", "category": "invalid_request_or_checkpoint"}


def test_cli_reports_response_without_pending_question(tmp_path):
    run_id = _replay_run_after_clarification(tmp_path)

    error = _error(_run(tmp_path, "respond", run_id, "--answer", "Again."))

    assert error == {
        "error": "no_pending_clarification",
        "category": "invalid_request_or_checkpoint",
    }


def test_cli_reports_missing_checkpoint(tmp_path):
    error = _error(_run(tmp_path, "inspect", UNKNOWN_RUN_ID))

    assert error == {"error": "checkpoint_not_found", "category": "checkpoint_unavailable"}


def test_cli_reports_state_dir_that_is_a_file(tmp_path):
    state_file = tmp_path / "state"
    state_file.write_text("")

    error = _error(_run(state_file, "start", "--brief", "b"))

    assert error == {"error": "state_dir_not_directory", "category": "checkpoint_unavailable"}
    assert state_file.read_text() == ""
