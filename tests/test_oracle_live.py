import json

import pytest

from adapters.terac_study_contract import StudyReadError
from recipes.oracle import Oracle
from recipes.oracle.live_contract import OracleLiveBinding
from runtime.runner import CheckpointIncompatibleError
from tests.test_terac_study_read import DuplicateStructuredFieldTerac, FakeTerac
from tests.test_terac_study_read import binding as provider_binding

BRIEF = "Exact fictional task"


def _live_binding():
    provider = provider_binding()
    return OracleLiveBinding(
        organization_id=provider.organization_id,
        project_id=provider.project_id,
        opportunity_id=provider.opportunity_id,
        brief_sha256=provider.brief_sha256,
        definition_sha256=provider.definition_sha256,
    )


def test_live_attachment_reads_one_bound_status_without_delivering_feedback(tmp_path):
    binding = _live_binding()
    transport = FakeTerac()
    credential_calls = []

    def credential_provider():
        credential_calls.append("requested")
        return "synthetic-fixture-token"

    oracle = Oracle(
        tmp_path,
        live_binding=binding,
        credential_provider=credential_provider,
        live_transport_factory=lambda: transport,
    )
    started = oracle.start(BRIEF, mode="live")
    checkpoint = tmp_path / f"{started.run_id}.json"
    original_bytes = checkpoint.read_bytes()

    assert started.phase == "awaiting_provider_refresh"
    assert started.status == "working"
    assert started.provider_observation is None
    assert credential_calls == []
    assert "synthetic-study" not in started.model_dump_json()
    assert oracle.inspect(started.run_id) == started
    assert checkpoint.read_bytes() == original_bytes
    assert credential_calls == []

    refreshed = oracle.resume(started.run_id)

    assert refreshed.mode == "live"
    assert refreshed.status == "working"
    assert refreshed.phase == "awaiting_launch"
    assert refreshed.feedback is None
    assert refreshed.provider_observation == {
        "status": "draft",
        "num_participants": 1,
        "total_cost_cents": 1800,
        "cost_per_participant_cents": 1800,
        "currency": "usd",
    }
    assert credential_calls == ["requested"]
    assert [json.loads(request.content)["method"] for request in transport.requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "tools/call",
    ]
    assert "synthetic-study" not in refreshed.model_dump_json()

    restarted = Oracle(tmp_path, live_binding=binding)
    assert restarted.inspect(started.run_id) == refreshed


def test_live_status_without_pricing_is_inspectable(tmp_path):
    binding = _live_binding()
    transport = FakeTerac()
    transport.opportunity["pricing"] = None
    oracle = Oracle(
        tmp_path,
        live_binding=binding,
        credential_provider=lambda: "synthetic-fixture-token",
        live_transport_factory=lambda: transport,
    )

    started = oracle.start(BRIEF, mode="live")
    refreshed = oracle.resume(started.run_id)

    assert refreshed.provider_observation == {
        "status": "draft",
        "num_participants": 1,
        "total_cost_cents": None,
        "cost_per_participant_cents": None,
        "currency": None,
    }
    assert oracle.inspect(started.run_id) == refreshed


@pytest.mark.parametrize(
    ("total_cost_cents", "cost_per_participant_cents", "currency"),
    [
        (1800, 1800, None),
        (1800, None, "usd"),
        (None, 1800, "usd"),
        (None, None, "usd"),
        (1800, 1800, "eur"),
    ],
)
def test_inconsistent_live_pricing_checkpoint_is_rejected_without_rewrite(
    tmp_path, total_cost_cents, cost_per_participant_cents, currency
):
    binding = _live_binding()
    oracle = Oracle(
        tmp_path,
        live_binding=binding,
        credential_provider=lambda: "synthetic-fixture-token",
        live_transport_factory=FakeTerac,
    )
    started = oracle.start(BRIEF, mode="live")
    oracle.resume(started.run_id)
    checkpoint = tmp_path / f"{started.run_id}.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["adapter_payload"]["observation"].update(
        total_cost_cents=total_cost_cents,
        cost_per_participant_cents=cost_per_participant_cents,
        currency=currency,
    )
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    corrupted_bytes = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        oracle.inspect(started.run_id)

    assert checkpoint.read_bytes() == corrupted_bytes


def test_live_binding_must_match_brief_before_checkpoint_creation(tmp_path):
    oracle = Oracle(tmp_path, live_binding=_live_binding())

    with pytest.raises(StudyReadError) as failure:
        oracle.start("A different brief", mode="live")

    assert failure.value.code == "binding_mismatch"
    assert not list(tmp_path.glob("*.json"))


def test_failed_live_read_keeps_saved_run_waiting_and_inspectable(tmp_path):
    binding = _live_binding()
    transport = FakeTerac()
    transport.context["organizationId"] = "synthetic-other-org"
    oracle = Oracle(
        tmp_path,
        live_binding=binding,
        credential_provider=lambda: "synthetic-fixture-token",
        live_transport_factory=lambda: transport,
    )
    started = oracle.start(BRIEF, mode="live")
    checkpoint = tmp_path / f"{started.run_id}.json"
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(StudyReadError) as failure:
        oracle.resume(started.run_id)

    assert failure.value.code == "organization_mismatch"
    assert checkpoint.read_bytes() == original_bytes
    assert oracle.inspect(started.run_id) == started


@pytest.mark.parametrize("nonfinite_value", [None, "NaN"])
def test_ambiguous_raw_provider_identity_keeps_saved_run_waiting(tmp_path, nonfinite_value):
    binding = _live_binding()
    transport = DuplicateStructuredFieldTerac(
        "terac_get_opportunity",
        "id",
        "different-study",
        nonfinite_value=nonfinite_value,
    )
    oracle = Oracle(
        tmp_path,
        live_binding=binding,
        credential_provider=lambda: "synthetic-fixture-token",
        live_transport_factory=lambda: transport,
    )
    started = oracle.start(BRIEF, mode="live")
    checkpoint = tmp_path / f"{started.run_id}.json"
    original_bytes = checkpoint.read_bytes()

    with pytest.raises(StudyReadError) as failure:
        oracle.resume(started.run_id)

    assert failure.value.code == "invalid_response"
    assert checkpoint.read_bytes() == original_bytes
    assert oracle.inspect(started.run_id) == started


@pytest.mark.parametrize("intent", ["follow_up", "new_query"])
@pytest.mark.parametrize("brief", [BRIEF, "Changed fictional task"])
@pytest.mark.parametrize("parent_mode", ["live", "replay"])
def test_live_linked_start_rejects_without_child_or_provider_effects(
    tmp_path, intent, brief, parent_mode
):
    binding = _live_binding()
    transport = FakeTerac()
    credential_calls = []
    oracle = Oracle(
        tmp_path,
        live_binding=binding,
        credential_provider=lambda: (
            credential_calls.append("requested") or "synthetic-fixture-token"
        ),
        live_transport_factory=lambda: transport,
    )
    parent = oracle.start(BRIEF, mode=parent_mode)
    parent_checkpoint = tmp_path / f"{parent.run_id}.json"
    parent_bytes = parent_checkpoint.read_bytes()
    prior_checkpoints = set(tmp_path.glob("*.json"))

    with pytest.raises(ValueError, match="Live linked work is unavailable"):
        oracle.start(
            brief,
            mode="live",
            parent_id=parent.run_id,
            intent=intent,
        )

    assert set(tmp_path.glob("*.json")) == prior_checkpoints
    assert parent_checkpoint.read_bytes() == parent_bytes
    assert oracle.inspect(parent.run_id) == parent
    assert credential_calls == []
    assert transport.requests == []
