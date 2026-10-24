import json

import pytest

from recipes.oracle import REVIEW_SCOPE, Oracle
from runtime.runner import CheckpointIncompatibleError

BRIEF = "Synthetic repair-shop appointment tool: buyer demand and pricing are unproven."


def write_ownerless_checkpoint(state_dir, oracle, version=1):
    started = oracle.start(BRIEF)
    checkpoint = state_dir / f"{started.run_id}.json"
    current = json.loads(checkpoint.read_text(encoding="utf-8"))
    recipe = current["recipe_payload"]
    adapter = current["adapter_payload"]
    payload = {
        "run_id": current["run_id"],
        "parent_id": current["parent_id"],
        "intent": current["intent"],
        "artifact_id": recipe["artifact_id"],
        "brief": recipe["brief"],
        "scope": recipe["scope"],
        "mode": current["mode"],
        "checkpoint_version": version,
        "recipe_contract_version": current["recipe_contract_version"],
        "status": current["status"],
        "phase": current["phase"],
        "question": current["question"],
        "feedback": recipe["feedback"],
        "error": current["error"],
        "answer": recipe["answer"],
        "replay_case": adapter["replay_case"],
        "replay_fixture_version": adapter["replay_fixture_version"],
        "replay_fixture_digest": adapter["replay_fixture_digest"],
        "cursor": adapter["cursor"],
    }
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    return started.run_id, checkpoint


def test_default_start_is_replay_and_inspection_preserves_waiting(tmp_path, monkeypatch):
    monkeypatch.setenv("TERAC_API_KEY", "present-but-unused")
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)

    assert started.mode == "replay"
    assert started.status == "working"
    assert started.phase == "waiting_for_expert"
    assert started.feedback is None
    assert oracle.inspect(started.run_id) == started
    assert Oracle(tmp_path).inspect(started.run_id) == started


def test_default_start_pins_the_inconclusive_fixture_across_restart(tmp_path):
    started = Oracle(tmp_path).start(BRIEF)

    assert started.replay_case == "inconclusive"
    checkpoint = json.loads((tmp_path / f"{started.run_id}.json").read_text())
    assert checkpoint["checkpoint_version"] == 3
    assert checkpoint["recipe_id"] == "oracle"
    assert checkpoint["recipe_contract_version"] == 1
    assert checkpoint["recipe_payload"] == {
        "brief": BRIEF,
        "scope": list(REVIEW_SCOPE),
        "artifact_id": started.artifact_id,
        "feedback": None,
        "answer": None,
    }
    assert checkpoint["adapter_payload"]["replay_case"] == "inconclusive"
    assert checkpoint["adapter_payload"]["replay_fixture_version"] == 1
    assert len(checkpoint["adapter_payload"]["replay_fixture_digest"]) == 64
    assert not {"brief", "scope", "feedback", "replay_case"} & set(checkpoint)
    assert Oracle(tmp_path).inspect(started.run_id) == started


@pytest.mark.parametrize(
    ("case_id", "recommendation"),
    [("pass_with_comments", "pass"), ("fail", "fail")],
)
def test_selected_outcome_cases_deliver_their_distinct_reviews(tmp_path, case_id, recommendation):
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF, replay_case=case_id)
    delivered = oracle.resume(started.run_id)

    assert started.replay_case == case_id
    assert delivered.status == "succeeded"
    assert delivered.feedback.recommendation == recommendation
    assert delivered.feedback.provenance == "synthetic"
    if recommendation == "pass":
        assert delivered.feedback.findings == ()
        assert delivered.feedback.comments
    else:
        assert delivered.feedback.findings


@pytest.mark.parametrize("replay_case", ["unknown", "null", "", 3])
def test_unknown_replay_case_is_rejected_without_creating_a_run(tmp_path, replay_case):
    from recipes.oracle import InvalidReplayCaseError

    with pytest.raises(InvalidReplayCaseError) as error:
        Oracle(tmp_path).start(BRIEF, replay_case=replay_case)

    assert error.value.code == "invalid_replay_case"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("path", "bad_value"),
    [
        (("checkpoint_version",), 2),
        (("checkpoint_version",), True),
        (("recipe_contract_version",), 1.0),
        (("adapter_payload", "replay_fixture_version"), "1"),
        (("adapter_payload", "replay_case"), "unknown"),
        (("adapter_payload", "replay_fixture_digest"), "A" * 64),
        (("adapter_payload", "replay_fixture_digest"), "0" * 64),
    ],
)
def test_checkpoint_pin_mismatch_is_stable_and_does_not_rewrite_bytes(tmp_path, path, bad_value):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    target = state
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = bad_value
    checkpoint.write_text(json.dumps(state))
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError) as error:
        Oracle(tmp_path).inspect(started.run_id)

    assert error.value.code == "checkpoint_incompatible"
    assert checkpoint.read_bytes() == original_bytes


@pytest.mark.parametrize("version", [1, 2])
def test_ownerless_checkpoint_versions_are_rejected_without_rewriting_bytes(tmp_path, version):
    oracle = Oracle(tmp_path)
    run_id, checkpoint = write_ownerless_checkpoint(tmp_path, oracle, version)
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        oracle.inspect(run_id)
    assert checkpoint.read_bytes() == original_bytes

    with pytest.raises(CheckpointIncompatibleError):
        oracle.resume(run_id)
    assert checkpoint.read_bytes() == original_bytes


def test_duplicate_checkpoint_keys_are_rejected_without_rewriting_bytes(tmp_path):
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    original_bytes = checkpoint.read_bytes()
    duplicated = original_bytes.replace(
        b'"status": "working"',
        b'"status": "working",\n  "status": "working"',
        1,
    )
    assert duplicated != original_bytes
    checkpoint.write_bytes(duplicated)

    with pytest.raises(CheckpointIncompatibleError):
        oracle.inspect(started.run_id)
    assert checkpoint.read_bytes() == duplicated


@pytest.mark.parametrize("field", ["error", "question"])
def test_empty_checkpoint_lifecycle_text_is_rejected_by_python_and_cli_without_rewriting_bytes(
    tmp_path, field
):
    import subprocess
    import sys

    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload[field] = ""
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    malformed = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        oracle.inspect(started.run_id)
    assert checkpoint.read_bytes() == malformed

    inspected = subprocess.run(
        [
            sys.executable,
            "-m",
            "recipes.oracle",
            "--state-dir",
            str(tmp_path),
            "inspect",
            started.run_id,
        ],
        capture_output=True,
        text=True,
    )
    assert inspected.returncode == 2
    assert json.loads(inspected.stderr) == {"error": "checkpoint_incompatible"}
    assert inspected.stdout == ""
    assert "Traceback" not in inspected.stderr
    assert checkpoint.read_bytes() == malformed


def test_oracle_domain_fields_are_rejected_in_runner_envelope(tmp_path):
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["brief"] = BRIEF
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        oracle.inspect(started.run_id)
    assert checkpoint.read_bytes() == original_bytes


def test_respond_requires_a_pending_question_and_nonempty_answer(tmp_path):
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)
    with pytest.raises(ValueError, match="pending"):
        oracle.respond(started.run_id, "Answer before any question")
    pending = oracle.resume(started.run_id)
    with pytest.raises(ValueError, match="nonempty"):
        oracle.respond(started.run_id, "  ")
    assert oracle.inspect(started.run_id) == pending


def test_cli_exposes_the_same_persisted_start_and_inspect(tmp_path):
    import subprocess
    import sys

    command = [sys.executable, "-m", "recipes.oracle", "--state-dir", str(tmp_path)]
    started = subprocess.run(command + ["start", "--brief", BRIEF], capture_output=True, text=True)
    assert started.returncode == 0, started.stderr
    snapshot = json.loads(started.stdout)
    inspected = subprocess.run(
        command + ["inspect", snapshot["run_id"]], capture_output=True, text=True
    )
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == snapshot


def test_cli_sanitizes_malformed_v3_scope_as_incompatible(tmp_path):
    import subprocess
    import sys

    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    payload = json.loads(checkpoint.read_text())
    payload["recipe_payload"]["scope"] = None
    checkpoint.write_text(json.dumps(payload))
    malformed = checkpoint.read_bytes()
    command = [sys.executable, "-m", "recipes.oracle", "--state-dir", str(tmp_path)]

    inspected = subprocess.run(
        command + ["inspect", started.run_id], capture_output=True, text=True
    )

    assert inspected.returncode == 2
    assert json.loads(inspected.stderr) == {"error": "checkpoint_incompatible"}
    assert inspected.stdout == ""
    assert "Traceback" not in inspected.stderr
    assert checkpoint.read_bytes() == malformed


def test_cli_rejects_live_and_unknown_options_without_writing(tmp_path):
    import subprocess
    import sys

    state_dir = tmp_path / "state"
    base = [sys.executable, "-m", "recipes.oracle", "--state-dir", str(state_dir)]
    for arguments in (
        ["start", "--brief", BRIEF, "--mode", "live"],
        ["--unsupported-option", "start", "--brief", BRIEF],
    ):
        result = subprocess.run(base + arguments, capture_output=True, text=True)

        assert result.returncode == 2
        assert not state_dir.exists()


def test_cli_selects_a_replay_case_and_reports_selector_errors(tmp_path):
    import subprocess
    import sys

    command = [sys.executable, "-m", "recipes.oracle", "--state-dir", str(tmp_path)]
    started = subprocess.run(
        command + ["start", "--brief", BRIEF, "--replay-case", "fail"],
        capture_output=True,
        text=True,
    )
    assert started.returncode == 0, started.stderr
    snapshot = json.loads(started.stdout)
    assert snapshot["replay_case"] == "fail"

    delivered = subprocess.run(
        command + ["resume", snapshot["run_id"]], capture_output=True, text=True
    )
    assert delivered.returncode == 0
    assert json.loads(delivered.stdout)["feedback"]["recommendation"] == "fail"

    invalid = subprocess.run(
        command + ["start", "--brief", BRIEF, "--replay-case", "null"],
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert json.loads(invalid.stderr) == {"error": "invalid_replay_case"}
    assert invalid.stdout == ""


def test_captured_cli_preserves_unicode_through_all_actions(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    brief = "Synthetic caf\u00e9 \U0001f469\U0001f3fd\u200d\U0001f527 launch"
    answer = "Repair shops in \u6771\u4eac"
    command = [sys.executable, "-m", "recipes.oracle", "--state-dir", str(tmp_path)]

    def call(*arguments):
        result = subprocess.run(command + list(arguments), capture_output=True)
        assert result.returncode == 0, result.stderr.decode("ascii", errors="backslashreplace")
        snapshot = json.loads(result.stdout.decode("ascii"))
        assert snapshot["brief"] == brief
        return snapshot

    started = call("start", "--brief", brief)
    run_id = started["run_id"]
    assert call("inspect", run_id) == started
    assert call("resume", run_id)["status"] == "needs_input"
    assert call("respond", run_id, "--answer", answer)["status"] == "working"
    delivered = call("resume", run_id)
    assert delivered["status"] == "succeeded"
    assert call("inspect", run_id) == delivered
    checkpoint = json.loads((tmp_path / f"{run_id}.json").read_text(encoding="utf-8"))
    assert checkpoint["recipe_payload"]["answer"] == answer
