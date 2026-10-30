import json
import subprocess
import sys
from pathlib import Path

import pytest

from recipes.oracle import __main__ as oracle_cli

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


@pytest.mark.parametrize("intent", ["follow_up", "new_query"])
def test_cli_reports_linked_intent_without_parent(tmp_path, intent):
    error = _error(_run(tmp_path, "start", "--brief", "b", "--intent", intent))

    assert error == {"error": "parent_required", "category": "invalid_request_or_checkpoint"}


def test_cli_keeps_generic_request_error_for_unclassified_value_error(tmp_path):
    error = _error(
        _run(
            tmp_path,
            "start",
            "--brief",
            "Synthetic brief.",
            "--study-request-file",
            "relative-request.json",
        )
    )

    assert error == {"error": "invalid_request_or_checkpoint"}
    assert not list(tmp_path.glob("*.json"))


def test_cli_does_not_mask_study_request_error_with_malformed_parent(tmp_path):
    error = _error(
        _run(
            tmp_path,
            "start",
            "--brief",
            "Synthetic brief.",
            "--parent-id",
            "not-a-uuid",
            "--intent",
            "follow_up",
            "--study-request-file",
            "relative-request.json",
        )
    )

    assert error == {"error": "invalid_request_or_checkpoint"}
    assert not list(tmp_path.glob("*.json"))


def test_cli_reports_default_review_with_parent_as_invalid_linked_intent(tmp_path):
    started = _run(tmp_path, "start", "--brief", "Synthetic parent brief.")
    assert started.returncode == 0, started.stderr
    parent = json.loads(started.stdout)
    parent_checkpoint = tmp_path / f"{parent['run_id']}.json"
    original_parent = parent_checkpoint.read_bytes()

    error = _error(
        _run(
            tmp_path,
            "start",
            "--brief",
            "Synthetic child brief.",
            "--parent-id",
            parent["run_id"],
        )
    )

    assert error == {
        "error": "invalid_linked_intent",
        "category": "invalid_request_or_checkpoint",
    }
    assert list(tmp_path.glob("*.json")) == [parent_checkpoint]
    assert parent_checkpoint.read_bytes() == original_parent


def test_cli_reports_missing_canonical_parent_as_invalid_linked_intent(tmp_path):
    error = _error(
        _run(
            tmp_path,
            "start",
            "--brief",
            "Synthetic child brief.",
            "--parent-id",
            UNKNOWN_RUN_ID,
        )
    )

    assert error == {
        "error": "invalid_linked_intent",
        "category": "invalid_request_or_checkpoint",
    }
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.parametrize("parent_id", ["not-a-uuid", "../escape"])
@pytest.mark.parametrize("intent", ["review", "follow_up", "new_query"])
def test_cli_reports_malformed_parent_id_before_intent_validation(tmp_path, parent_id, intent):
    arguments = ["--parent-id", parent_id]
    if intent != "review":
        arguments.extend(["--intent", intent])

    error = _error(
        _run(
            tmp_path,
            "start",
            "--brief",
            "Synthetic child brief.",
            *arguments,
        )
    )

    assert error == {"error": "invalid_run_id", "category": "invalid_request_or_checkpoint"}
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "arguments",
    [
        ("inspect", "not-a-uuid"),
        ("resume", "../escape"),
        ("respond", "not-a-uuid", "--answer", "Synthetic answer."),
        (
            "launch",
            "not-a-uuid",
            "--expected-total-cost-cents",
            "0",
            "--maximum-total-cost-cents",
            "0",
            "--scope-sha256",
            "0" * 64,
            "--confirmation",
            "launch",
        ),
    ],
)
def test_cli_reports_malformed_run_id(tmp_path, arguments):
    error = _error(_run(tmp_path, *arguments))

    assert error == {"error": "invalid_run_id", "category": "invalid_request_or_checkpoint"}
    assert list(tmp_path.glob("*.json")) == []


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


@pytest.mark.parametrize(
    "arguments",
    [
        ("start", "--brief", "b"),
        ("inspect", UNKNOWN_RUN_ID),
        ("resume", UNKNOWN_RUN_ID),
        ("respond", UNKNOWN_RUN_ID, "--answer", "Synthetic answer."),
        (
            "launch",
            UNKNOWN_RUN_ID,
            "--expected-total-cost-cents",
            "0",
            "--maximum-total-cost-cents",
            "0",
            "--scope-sha256",
            "0" * 64,
            "--confirmation",
            "launch",
        ),
    ],
)
def test_cli_reports_file_valued_state_dir_across_actions(tmp_path, arguments):
    state_file = tmp_path / "state"
    state_file.write_text("unchanged")
    original = state_file.read_bytes()

    error = _error(_run(state_file, *arguments))

    assert error == {"error": "state_dir_not_directory", "category": "checkpoint_unavailable"}
    assert state_file.read_bytes() == original


def test_cli_storage_fallback_survives_state_dir_stat_error(monkeypatch, tmp_path, capsys):
    state_dir = tmp_path / "state"

    class FailingOracle:
        def inspect(self, _run_id):
            raise PermissionError("synthetic checkpoint failure")

    monkeypatch.setattr(oracle_cli, "Oracle", lambda *args, **kwargs: FailingOracle())
    monkeypatch.setattr(
        sys,
        "argv",
        ["terac-oracle", "--state-dir", str(state_dir), "inspect", UNKNOWN_RUN_ID],
    )
    original_is_file = Path.is_file

    def fail_state_dir_stat(path):
        if path == state_dir:
            raise PermissionError("synthetic metadata failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", fail_state_dir_stat)

    assert oracle_cli.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "checkpoint_unavailable"}
