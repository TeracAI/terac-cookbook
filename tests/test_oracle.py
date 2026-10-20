import hashlib
import json
from importlib.resources import files
from uuid import uuid4

import pytest

from adapters.replay import ReplayAdapter
from recipes.oracle import Oracle, SimulationUnavailableError
from runtime.runner import CheckpointIncompatibleError

BRIEF = "Synthetic repair-shop appointment tool: buyer demand and pricing are unproven."


def simulation_observation(snapshot, revision, kind, **fields):
    return {
        "run_id": snapshot.run_id,
        "reference": snapshot.simulation_ref,
        "artifact_id": snapshot.artifact_id,
        "scope": list(snapshot.scope),
        "revision": revision,
        "kind": kind,
        **fields,
    }


def publish_simulation_observation(simulation_dir, snapshot, observation):
    reference_id = snapshot.simulation_ref.removeprefix("sim:")
    folder = simulation_dir / "runs" / reference_id / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{observation['revision']:020d}.json"
    assert not final.exists()
    temporary = final.with_suffix(".tmp")
    temporary.write_text(json.dumps(observation), encoding="utf-8")
    temporary.rename(final)
    return final


def simulation_review(snapshot):
    return {
        "artifact_id": snapshot.artifact_id,
        "scope": list(snapshot.scope),
        "recommendation": "inconclusive",
        "rationale": "Synthetic local mailbox review.",
        "findings": [],
        "comments": [],
        "limitations": ["Synthetic rehearsal only."],
        "next_steps": [],
        "provenance": "synthetic",
    }


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
    assert checkpoint["checkpoint_version"] == 1
    assert checkpoint["recipe_contract_version"] == 1
    assert checkpoint["replay_case"] == "inconclusive"
    assert checkpoint["replay_fixture_version"] == 1
    assert len(checkpoint["replay_fixture_digest"]) == 64
    assert not {"simulation_ref", "simulation_revision", "simulation_observation_digest"} & set(
        checkpoint
    )
    assert Oracle(tmp_path).inspect(started.run_id) == started


def test_simulation_requires_explicit_local_mailbox_before_creating_a_run(tmp_path):
    from recipes.oracle import SimulationUnavailableError

    with pytest.raises(SimulationUnavailableError) as error:
        Oracle(tmp_path / "state").start(BRIEF, mode="simulation")

    assert error.value.code == "simulation_unavailable"
    assert not (tmp_path / "state").exists()


def test_simulation_v2_binds_opaque_reference_without_replay_pins(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    checkpoint = state_dir / f"{started.run_id}.json"
    stored = json.loads(checkpoint.read_text(encoding="utf-8"))

    assert started.mode == "simulation"
    assert started.simulation_ref.startswith("sim:")
    assert started.simulation_revision == 0
    assert started.status == "working"
    assert stored["checkpoint_version"] == 2
    assert stored["mode"] == "simulation"
    assert stored["simulation_ref"] == started.simulation_ref
    assert stored["simulation_revision"] == 0
    assert stored["simulation_observation_digest"] is None
    assert not {"replay_case", "replay_fixture_version", "replay_fixture_digest", "cursor"} & set(
        stored
    )
    assert Oracle(state_dir, simulation_dir=simulation_dir).inspect(started.run_id) == started
    assert "replay_case" not in started.model_dump(mode="json")
    checkpoint_bytes = checkpoint.read_bytes()
    assert Oracle(state_dir).inspect(started.run_id) == started
    with pytest.raises(SimulationUnavailableError):
        Oracle(state_dir).resume(started.run_id)
    assert checkpoint.read_bytes() == checkpoint_bytes


def test_simulation_waits_without_mutation_until_next_observation(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    checkpoint = state_dir / f"{started.run_id}.json"
    original = checkpoint.read_bytes()

    assert oracle.inspect(started.run_id) == started
    assert oracle.resume(started.run_id) == started
    assert checkpoint.read_bytes() == original

    folder = simulation_dir / "runs" / started.simulation_ref.removeprefix("sim:") / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f"{1:020d}.json.tmp"
    temporary.write_text("partial", encoding="utf-8")
    assert oracle.resume(started.run_id) == started
    assert checkpoint.read_bytes() == original
    published = publish_simulation_observation(
        simulation_dir,
        started,
        simulation_observation(started, 1, "waiting"),
    )
    waiting = oracle.resume(started.run_id)

    assert waiting.status == "working"
    assert waiting.simulation_revision == 1
    stored = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert (
        stored["simulation_observation_digest"]
        == hashlib.sha256(published.read_bytes()).hexdigest()
    )
    accepted_bytes = checkpoint.read_bytes()
    assert oracle.resume(started.run_id) == waiting
    assert checkpoint.read_bytes() == accepted_bytes


def test_simulation_does_not_scan_later_revision_when_next_file_is_missing(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    later = simulation_observation(started, 2, "waiting")
    folder = simulation_dir / "runs" / started.simulation_ref.removeprefix("sim:") / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{2:020d}.json").write_text(json.dumps(later), encoding="utf-8")
    checkpoint = state_dir / f"{started.run_id}.json"
    original = checkpoint.read_bytes()

    assert oracle.resume(started.run_id) == started
    assert checkpoint.read_bytes() == original


def test_simulation_observation_at_exact_size_limit_is_accepted(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    payload = simulation_observation(started, 1, "clarification", question="x")
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload["question"] = "x" * (65_536 - len(raw) + 1)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    assert len(raw) == 65_536
    folder = simulation_dir / "runs" / started.simulation_ref.removeprefix("sim:") / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{1:020d}.json").write_bytes(raw)

    accepted = oracle.resume(started.run_id)

    assert accepted.status == "needs_input"
    assert accepted.simulation_revision == 1
    assert len(accepted.question) == len(payload["question"])


def test_simulation_mailbox_supports_unbounded_out_of_band_clarification_cycles(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")

    publish_simulation_observation(
        simulation_dir, started, simulation_observation(started, 1, "waiting")
    )
    first_wait = oracle.resume(started.run_id)
    publish_simulation_observation(
        simulation_dir,
        started,
        simulation_observation(started, 2, "clarification", question="Who is the buyer?"),
    )
    first_question = oracle.resume(started.run_id)
    assert first_question.status == "needs_input"
    answered = oracle.respond(started.run_id, "Independent repair shops")
    assert answered.simulation_revision == 2
    assert answered.status == "working"

    publish_simulation_observation(
        simulation_dir, started, simulation_observation(started, 3, "waiting")
    )
    second_wait = oracle.resume(started.run_id)
    publish_simulation_observation(
        simulation_dir,
        started,
        simulation_observation(started, 4, "clarification", question="Which region?"),
    )
    second_question = oracle.resume(started.run_id)
    assert second_question.status == "needs_input"
    oracle.respond(started.run_id, "Madrid")

    publish_simulation_observation(
        simulation_dir,
        started,
        simulation_observation(started, 5, "review", feedback=simulation_review(started)),
    )
    delivered = oracle.resume(started.run_id)

    assert first_wait.simulation_revision == 1
    assert second_wait.simulation_revision == 3
    assert delivered.simulation_revision == 5
    assert delivered.status == "succeeded"
    assert delivered.feedback.provenance == "synthetic"
    assert delivered.feedback.artifact_id == started.artifact_id
    assert delivered.feedback.scope == started.scope
    assert Oracle(state_dir, simulation_dir=simulation_dir).inspect(started.run_id) == delivered

    checkpoint = state_dir / f"{started.run_id}.json"
    accepted_bytes = checkpoint.read_bytes()
    terminal_file = publish_simulation_observation(
        simulation_dir, started, simulation_observation(started, 6, "waiting")
    )
    assert oracle.resume(started.run_id) == delivered
    assert checkpoint.read_bytes() == accepted_bytes
    assert terminal_file.exists()


@pytest.mark.parametrize(
    "invalid_observation",
    [
        "malformed_json",
        "unknown_field",
        "wrong_run",
        "wrong_reference",
        "wrong_artifact",
        "wrong_scope",
        "stale_revision",
        "revision_gap",
        "invalid_review",
        "conflicting_payload",
        "null_waiting_question",
        "null_waiting_feedback",
        "null_clarification_feedback",
        "null_review_question",
        "oversized",
    ],
)
def test_invalid_simulation_observations_preserve_checkpoint_bytes(tmp_path, invalid_observation):
    from recipes.oracle import SimulationObservationError

    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    checkpoint = state_dir / f"{started.run_id}.json"
    original = checkpoint.read_bytes()
    payload = simulation_observation(started, 1, "waiting")
    if invalid_observation == "malformed_json":
        raw = b'{"private-canary":'
    else:
        if invalid_observation == "unknown_field":
            payload["unexpected"] = "private-canary"
        elif invalid_observation == "wrong_run":
            payload["run_id"] = str(uuid4())
        elif invalid_observation == "wrong_reference":
            payload["reference"] = "sim:" + str(uuid4())
        elif invalid_observation == "wrong_artifact":
            payload["artifact_id"] = "0" * 64
        elif invalid_observation == "wrong_scope":
            payload["scope"] = ["other"]
        elif invalid_observation == "stale_revision":
            payload["revision"] = 0
        elif invalid_observation == "revision_gap":
            payload["revision"] = 2
        elif invalid_observation == "invalid_review":
            payload["kind"] = "review"
            payload["feedback"] = {"rationale": "private-canary"}
        elif invalid_observation == "conflicting_payload":
            payload["question"] = "Conflicts with waiting kind."
        elif invalid_observation == "null_waiting_question":
            payload["question"] = None
        elif invalid_observation == "null_waiting_feedback":
            payload["feedback"] = None
        elif invalid_observation == "null_clarification_feedback":
            payload = simulation_observation(started, 1, "clarification", question="Buyer?")
            payload["feedback"] = None
        elif invalid_observation == "null_review_question":
            payload = simulation_observation(
                started, 1, "review", feedback=simulation_review(started)
            )
            payload["question"] = None
        elif invalid_observation == "oversized":
            payload["padding"] = "x" * 66_000
        raw = json.dumps(payload).encode("utf-8")
    folder = simulation_dir / "runs" / started.simulation_ref.removeprefix("sim:") / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{1:020d}.json").write_bytes(raw)

    with pytest.raises(SimulationObservationError) as error:
        oracle.resume(started.run_id)

    assert error.value.code == "simulation_observation_invalid"
    assert "private-canary" not in str(error.value)
    assert checkpoint.read_bytes() == original


@pytest.mark.parametrize("question", ["private-canary\ud800", "private-canary\udfff"])
@pytest.mark.parametrize("location", ["question", "scope", "key", "feedback", "nested_key"])
def test_simulation_rejects_lone_surrogates_without_rewriting_checkpoint(
    tmp_path, question, location
):
    from recipes.oracle import SimulationObservationError

    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    checkpoint = state_dir / f"{started.run_id}.json"
    original = checkpoint.read_bytes()
    payload = simulation_observation(started, 1, "clarification", question=question)
    if location == "scope":
        payload["question"] = "Buyer?"
        payload["scope"] = [question]
    elif location == "key":
        payload[question] = payload.pop("question")
    elif location in {"feedback", "nested_key"}:
        feedback = simulation_review(started)
        if location == "feedback":
            feedback["comments"] = [question]
        else:
            feedback["comments"] = [{question: ["Synthetic"]}]
        payload = simulation_observation(started, 1, "review", feedback=feedback)
    publish_simulation_observation(simulation_dir, started, payload)

    with pytest.raises(SimulationObservationError) as error:
        oracle.resume(started.run_id)

    assert error.value.code == "simulation_observation_invalid"
    assert str(error.value) == "Local simulation observation is invalid"
    assert checkpoint.read_bytes() == original
    assert oracle.inspect(started.run_id) == started


@pytest.mark.parametrize("text", ["Caf\u00e9 \U0001f642", "x\u0000y"])
@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("kind", ["clarification", "review"])
def test_simulation_preserves_valid_unicode_through_resume_and_restart(
    tmp_path, text, escaped, kind
):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    if kind == "clarification":
        payload = simulation_observation(started, 1, kind, question=text)
    else:
        feedback = simulation_review(started)
        feedback["comments"] = [text]
        payload = simulation_observation(started, 1, kind, feedback=feedback)
    path = publish_simulation_observation(simulation_dir, started, payload)
    path.write_bytes(json.dumps(payload, ensure_ascii=escaped).encode("utf-8"))

    accepted = oracle.resume(started.run_id)

    assert accepted.simulation_revision == 1
    if kind == "clarification":
        assert accepted.question == text
    else:
        assert accepted.feedback.comments == (text,)
    assert Oracle(state_dir, simulation_dir=simulation_dir).inspect(started.run_id) == accepted


def test_simulation_observation_read_is_bounded_to_limit_plus_one(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    oracle = Oracle(state_dir, simulation_dir=simulation_dir)
    started = oracle.start(BRIEF, mode="simulation")
    folder = simulation_dir / "runs" / started.simulation_ref.removeprefix("sim:") / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{1:020d}.json"
    path.write_bytes(b" " * 65_537)
    original_open = type(path).open
    read_sizes = []

    class ReadProbe:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, size=-1):
            read_sizes.append(size)
            return self.wrapped.read(size)

    def probed_open(target, *args, **kwargs):
        handle = original_open(target, *args, **kwargs)
        if target == path and args and args[0] == "rb":
            return ReadProbe(handle)
        return handle

    monkeypatch.setattr(type(path), "open", probed_open)
    from recipes.oracle import SimulationObservationError

    with pytest.raises(SimulationObservationError):
        oracle.resume(started.run_id)

    assert read_sizes == [65_537]


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
    ("field", "bad_value"),
    [
        ("checkpoint_version", 2),
        ("checkpoint_version", True),
        ("recipe_contract_version", 1.0),
        ("replay_fixture_version", "1"),
        ("replay_case", "unknown"),
        ("replay_fixture_digest", "A" * 64),
        ("replay_fixture_digest", "0" * 64),
    ],
)
def test_checkpoint_pin_mismatch_is_stable_and_does_not_rewrite_bytes(tmp_path, field, bad_value):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state[field] = bad_value
    checkpoint.write_text(json.dumps(state))
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError) as error:
        Oracle(tmp_path).inspect(started.run_id)

    assert error.value.code == "checkpoint_incompatible"
    assert checkpoint.read_bytes() == original_bytes


def test_legacy_checkpoint_is_incompatible_without_migration(tmp_path):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    for field in (
        "checkpoint_version",
        "recipe_contract_version",
        "replay_case",
        "replay_fixture_version",
        "replay_fixture_digest",
    ):
        state.pop(field)
    checkpoint.write_text(json.dumps(state))
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        Oracle(tmp_path).inspect(started.run_id)

    assert checkpoint.read_bytes() == original_bytes


def test_linked_runs_reload_with_each_ancestors_own_case(tmp_path):
    oracle = Oracle(tmp_path)
    parent = oracle.start(BRIEF)
    oracle.resume(parent.run_id)
    oracle.respond(parent.run_id, "Independent repair shops")
    delivered_parent = oracle.resume(parent.run_id)
    child = oracle.start(
        "Synthetic follow-up brief.",
        parent_id=parent.run_id,
        intent="follow_up",
        replay_case="fail",
    )

    restarted = Oracle(tmp_path)
    assert restarted.inspect(child.run_id) == child
    delivered_child = restarted.resume(child.run_id)

    assert delivered_parent.replay_case == "inconclusive"
    assert child.replay_case == "fail"
    assert delivered_child.feedback.recommendation == "fail"
    assert restarted.inspect(parent.run_id) == delivered_parent


def test_injected_replay_is_semantically_pinned_and_requires_the_same_fixture(tmp_path):
    events = json.loads(files("recipes.oracle").joinpath("replay.json").read_text())["cases"][
        "inconclusive"
    ]["events"]
    started = Oracle(tmp_path, replay=ReplayAdapter(events)).start(BRIEF, replay_case="custom")
    checkpoint = tmp_path / f"{started.run_id}.json"
    original_bytes = checkpoint.read_bytes()

    reordered_events = json.loads(json.dumps(events, sort_keys=True))
    equivalent = Oracle(tmp_path, replay=ReplayAdapter(reordered_events))
    assert equivalent.inspect(started.run_id) == started

    changed_events = json.loads(json.dumps(events))
    changed_events[1]["feedback"]["rationale"] += " Changed fixture meaning."
    with pytest.raises(CheckpointIncompatibleError):
        Oracle(tmp_path, replay=ReplayAdapter(changed_events)).inspect(started.run_id)
    with pytest.raises(CheckpointIncompatibleError):
        Oracle(tmp_path).inspect(started.run_id)

    assert checkpoint.read_bytes() == original_bytes


def test_injected_replay_rejects_nonfinite_json_values(tmp_path):
    with pytest.raises(ValueError, match="finite JSON"):
        Oracle(tmp_path, replay=ReplayAdapter([{"value": float("nan")}]))

    assert list(tmp_path.iterdir()) == []


def test_linked_start_rejects_an_incompatible_parent_without_writing(tmp_path):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["replay_fixture_digest"] = "0" * 64
    checkpoint.write_text(json.dumps(state))
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        Oracle(tmp_path).start(
            "Synthetic linked brief.",
            parent_id=started.run_id,
            intent="follow_up",
            replay_case="pass_with_comments",
        )

    assert list(tmp_path.glob("*.json")) == [checkpoint]
    assert checkpoint.read_bytes() == original_bytes


def test_resume_exposes_clarification_and_inspect_cannot_release_another_event(tmp_path):
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)
    pending = oracle.resume(started.run_id)

    assert pending.status == "needs_input"
    assert pending.question == "Which launch segment should the synthetic review prioritize?"
    assert pending.feedback is None
    assert oracle.inspect(started.run_id) == pending
    assert oracle.resume(started.run_id) == pending


def test_corrupt_checkpoint_cannot_claim_completion_without_feedback(tmp_path):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["status"] = "succeeded"
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        Oracle(tmp_path).inspect(started.run_id)


@pytest.mark.parametrize("scope", [[], ["unrequested"]])
@pytest.mark.parametrize("action", ["inspect", "resume"])
def test_reload_rejects_unrequested_oracle_scope_before_progression(tmp_path, scope, action):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["scope"] = scope
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        getattr(Oracle(tmp_path), action)(started.run_id)


def test_reload_rejects_followup_without_a_parent(tmp_path):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["intent"] = "follow_up"
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        Oracle(tmp_path).inspect(started.run_id)


@pytest.mark.parametrize("cursor", [1, True, "1"])
@pytest.mark.parametrize("answer", [None, "   "])
def test_saved_progress_cannot_skip_an_unanswered_clarification(tmp_path, cursor, answer):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["cursor"] = cursor
    state["answer"] = answer
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        Oracle(tmp_path).resume(started.run_id)


def test_saved_progress_requires_an_explicit_cursor(tmp_path):
    oracle = Oracle(tmp_path)
    started = oracle.start(BRIEF)
    oracle.resume(started.run_id)
    oracle.respond(started.run_id, "Independent repair shops")
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    del state["cursor"]
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        Oracle(tmp_path).inspect(started.run_id)


@pytest.mark.parametrize("parent", ["self", "not-a-run-identity"])
def test_reload_rejects_invalid_parent_lineage(tmp_path, parent):
    started = Oracle(tmp_path).start(BRIEF)
    checkpoint = tmp_path / f"{started.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["intent"] = "follow_up"
    state["parent_id"] = started.run_id if parent == "self" else parent
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        Oracle(tmp_path).inspect(started.run_id)


@pytest.mark.parametrize("run_id", [None, True, "../escape", "not-a-run"])
def test_invalid_run_identity_is_rejected_at_the_public_interface(tmp_path, run_id):
    with pytest.raises(ValueError):
        Oracle(tmp_path).inspect(run_id)


def test_linked_intake_and_reload_reject_a_parent_with_mismatched_feedback(tmp_path):
    oracle = Oracle(tmp_path)
    first = oracle.start(BRIEF)
    oracle.resume(first.run_id)
    oracle.respond(first.run_id, "Independent repair shops")
    oracle.resume(first.run_id)
    child = oracle.start("Synthetic follow-up.", parent_id=first.run_id, intent="follow_up")
    checkpoint = tmp_path / f"{first.run_id}.json"
    state = json.loads(checkpoint.read_text())
    state["feedback"]["artifact_id"] = "different-artifact"
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        oracle.start("Another synthetic query.", parent_id=first.run_id, intent="new_query")
    with pytest.raises(ValueError):
        Oracle(tmp_path).resume(child.run_id)


def test_restarted_runner_answers_clarification_then_delivers_inconclusive_feedback(tmp_path):
    started = Oracle(tmp_path).start(BRIEF)
    Oracle(tmp_path).resume(started.run_id)
    answered = Oracle(tmp_path).respond(started.run_id, "Independent repair shops")

    assert answered.status == "working"
    assert answered.feedback is None
    assert answered.artifact_id == started.artifact_id
    completed = Oracle(tmp_path).resume(started.run_id)
    assert completed.status == "succeeded"
    assert completed.feedback.recommendation == "inconclusive"
    assert completed.feedback.provenance == "synthetic"
    assert completed.feedback.artifact_id == started.artifact_id
    assert completed.brief == BRIEF
    assert Oracle(tmp_path).inspect(started.run_id) == completed
    assert Oracle(tmp_path).resume(started.run_id) == completed


def test_linked_start_requires_explicit_followup_or_new_query_intent(tmp_path):
    oracle = Oracle(tmp_path)
    first = oracle.start(BRIEF)
    with pytest.raises(ValueError, match="Linked start"):
        oracle.start(BRIEF, parent_id=first.run_id)


@pytest.mark.parametrize("intent", ["follow_up", "new_query"])
def test_linked_work_keeps_the_original_feedback_and_does_not_inherit_it(tmp_path, intent):
    oracle = Oracle(tmp_path)
    first = oracle.start(BRIEF)
    oracle.resume(first.run_id)
    oracle.respond(first.run_id, "Independent repair shops")
    original = oracle.resume(first.run_id)
    linked = oracle.start(
        BRIEF + " Revised channel hypothesis.", parent_id=first.run_id, intent=intent
    )

    assert linked.run_id != original.run_id
    assert linked.parent_id == original.run_id
    assert linked.intent == intent
    assert linked.artifact_id != original.artifact_id
    assert linked.feedback is None
    assert oracle.inspect(original.run_id) == original


@pytest.mark.parametrize("recommendation", ["fail", "inconclusive"])
def test_negative_expert_feedback_is_successful_delivery(tmp_path, recommendation):
    events = json.loads(files("recipes.oracle").joinpath("replay.json").read_text())["cases"][
        "inconclusive"
    ]["events"]
    events[1]["feedback"]["recommendation"] = recommendation
    oracle = Oracle(tmp_path, replay=ReplayAdapter(events))
    started = oracle.start(BRIEF)
    oracle.resume(started.run_id)
    oracle.respond(started.run_id, "Independent repair shops")
    delivered = oracle.resume(started.run_id)

    assert delivered.status == "succeeded"
    assert delivered.feedback.recommendation == recommendation


@pytest.mark.parametrize(
    "invalid_field,value",
    [
        ("artifact_id", "different-brief"),
        ("scope", ["unrequested_scope"]),
        ("recommendation", "pass"),
        ("rationale", "   "),
    ],
)
def test_invalid_review_is_rejected_without_delivering_feedback(tmp_path, invalid_field, value):
    events = json.loads(files("recipes.oracle").joinpath("replay.json").read_text())["cases"][
        "inconclusive"
    ]["events"]
    events[1]["feedback"][invalid_field] = value
    oracle = Oracle(tmp_path, replay=ReplayAdapter(events))
    started = oracle.start(BRIEF)
    oracle.resume(started.run_id)
    oracle.respond(started.run_id, "Independent repair shops")
    rejected = oracle.resume(started.run_id)

    assert rejected.status == "failed"
    assert rejected.error == "invalid_provider_event"
    assert rejected.feedback is None


def test_live_is_unavailable_and_does_not_create_a_run(tmp_path):
    with pytest.raises(ValueError, match="unavailable"):
        Oracle(tmp_path).start(BRIEF, mode="live")
    assert list(tmp_path.iterdir()) == []


def test_malformed_replay_review_is_rejected_as_an_event(tmp_path):
    events = [{"kind": "review", "feedback": None}]
    oracle = Oracle(tmp_path, replay=ReplayAdapter(events))
    started = oracle.start(BRIEF)
    rejected = oracle.resume(started.run_id)
    assert rejected.status == "failed"
    assert rejected.feedback is None


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


def test_cli_simulation_uses_the_configured_local_mailbox(tmp_path):
    import subprocess
    import sys
    from types import SimpleNamespace

    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    command = [
        sys.executable,
        "-m",
        "recipes.oracle",
        "--state-dir",
        str(state_dir),
        "--simulation-dir",
        str(simulation_dir),
    ]
    started_result = subprocess.run(
        command + ["start", "--brief", BRIEF, "--mode", "simulation"],
        capture_output=True,
        text=True,
    )
    assert started_result.returncode == 0, started_result.stderr
    started = json.loads(started_result.stdout)
    publish_simulation_observation(
        simulation_dir,
        SimpleNamespace(**started),
        simulation_observation(SimpleNamespace(**started), 1, "clarification", question="Buyer?"),
    )

    resumed = subprocess.run(
        command + ["resume", started["run_id"]], capture_output=True, text=True
    )

    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["status"] == "needs_input"
    assert json.loads(resumed.stdout)["simulation_revision"] == 1


def test_cli_reports_invalid_configured_simulation_request_as_invalid(tmp_path):
    import subprocess
    import sys

    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    command = [
        sys.executable,
        "-m",
        "recipes.oracle",
        "--state-dir",
        str(state_dir),
        "--simulation-dir",
        str(simulation_dir),
        "start",
        "--brief",
        BRIEF,
        "--mode",
        "simulation",
        "--replay-case",
        "fail",
    ]

    invalid = subprocess.run(command, capture_output=True, text=True)

    assert invalid.returncode == 2
    assert json.loads(invalid.stdout) == {"error": "invalid_request_or_checkpoint"}
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
    assert json.loads(invalid.stdout) == {"error": "invalid_replay_case"}


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
    assert checkpoint["answer"] == answer
