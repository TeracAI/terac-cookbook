import hashlib
import json
import sys
from dataclasses import replace

import pytest

from adapters.terac_studies import (
    FeasibilityObservation,
    OpportunityObservation,
    OpportunityPrice,
)
from adapters.terac_study_contract import TERAC_COUNTRY_FILTER_IDS, StudyReadError, StudyWriteError
from cookbook_app import create_default_server
from recipes.oracle import Oracle, Snapshot
from runtime.runner import CheckpointIncompatibleError


@pytest.fixture
def anyio_backend():
    return "asyncio"


def study_request():
    return {
        "title": "Synthetic product review",
        "project_id": "synthetic-project",
        "business_type": "b2b",
        "num_participants": 1,
        "customer_screening_review": "manual_review",
        "device_types": ["desktop"],
        "filters": [{"multi_select--country": {"$in": ["US"]}}],
        "tasks": [
            {
                "sequence": 1,
                "task_type": "file_upload",
                "review_type": "manual_review",
                "duration_minutes": 15,
                "instructions": "Upload one synthetic Markdown review.",
                "accepted_mime_types": ["text/markdown"],
                "min_files": 1,
                "max_files": 1,
                "max_file_size_bytes": 2_097_152,
            }
        ],
        "quote": {
            "task_description": "Review the synthetic product and upload one report.",
            "panel_description": "One English-speaking product professional in the US.",
            "submission_count": 1,
            "loi_minutes": 15,
        },
        "maximum_total_cost_cents": 10_000,
    }


class NoCallStudies:
    def __getattr__(self, name):
        raise AssertionError(f"provider call during local start/inspect: {name}")


class FakeStudies:
    def __init__(self):
        self.calls = []
        self.before_request = None
        self.feasibility_status = "RECEIVED"
        self.feasibility_price = "opaque quote"
        self.feasibility_id_override = None
        self.opportunity = None
        self.opportunity_id_override = None
        self.launch_id_override = None
        self.request_error = None
        self.launch_error = None
        self.launch_status = "active"
        self.before_launch = None
        self.price_override = None
        self.pricing_override = None
        self.feasibility_dashboard_url = "https://terac.com/feasibility/synthetic"
        self.draft_url_override = None

    def request_feasibility(self, request):
        self.calls.append(("request_feasibility", request))
        if self.before_request is not None:
            self.before_request()
        if self.request_error is not None:
            raise self.request_error
        return FeasibilityObservation(
            request_id="synthetic-feasibility",
            status=self.feasibility_status,
            task_description=request["taskDescription"],
            panel_description=request["panelDescription"],
            submission_count=request["submissionCount"],
            timeline_hours=request.get("timelineHours"),
            pay_rate_frequency=request.get("payRateFrequency"),
            loi_minutes=request.get("loiMinutes"),
            cost_per_participant=self.feasibility_price
            if self.feasibility_status == "RESPONDED"
            else None,
            dashboard_url=self.feasibility_dashboard_url,
        )

    def get_feasibility(self, request_id):
        self.calls.append(("get_feasibility", request_id))
        return FeasibilityObservation(
            request_id=self.feasibility_id_override or request_id,
            status=self.feasibility_status,
            task_description="Review the synthetic product and upload one report.",
            panel_description="One English-speaking product professional in the US.",
            submission_count=1,
            timeline_hours=None,
            pay_rate_frequency=None,
            loi_minutes=15,
            cost_per_participant=self.feasibility_price
            if self.feasibility_status == "RESPONDED"
            else None,
            dashboard_url=self.feasibility_dashboard_url,
        )

    def create_opportunity(self, definition):
        self.calls.append(("create_opportunity", definition))
        self.opportunity = self._opportunity(definition, "draft")
        return self.opportunity

    def get_opportunity(self, opportunity_id):
        self.calls.append(("get_opportunity", opportunity_id))
        assert self.opportunity is not None
        if self.opportunity_id_override is not None:
            self.opportunity = replace(
                self.opportunity, opportunity_id=self.opportunity_id_override
            )
        if self.price_override is not None:
            self.opportunity = replace(
                self.opportunity,
                pricing=OpportunityPrice(self.price_override, self.price_override, "usd"),
            )
        if self.pricing_override is not None:
            self.opportunity = replace(self.opportunity, pricing=self.pricing_override)
        if self.draft_url_override is not None:
            self.opportunity = replace(self.opportunity, draft_editor_url=self.draft_url_override)
        return self.opportunity

    def launch_opportunity(self, opportunity_id):
        self.calls.append(("launch_opportunity", opportunity_id))
        assert self.opportunity is not None
        if self.before_launch is not None:
            self.before_launch()
        if self.launch_error is not None:
            raise self.launch_error
        self.opportunity = replace(
            self.opportunity,
            opportunity_id=self.launch_id_override or self.opportunity.opportunity_id,
            status=self.launch_status,
        )
        return self.opportunity

    @staticmethod
    def _opportunity(definition, status):
        return OpportunityObservation(
            opportunity_id="synthetic-opportunity",
            title=definition["title"],
            description=definition.get("description"),
            status=status,
            project_id=definition["project_id"],
            num_participants=definition["num_participants"],
            business_type=definition["business_type"],
            customer_screening_review=definition.get("customer_screening_review"),
            device_types=tuple(definition["device_types"]),
            expected_days_to_complete=definition.get("expected_days_to_complete"),
            filters=tuple(definition["filters"]),
            tasks=tuple(definition["tasks"]),
            feasibility_request_id=definition["feasibility_request_id"],
            pricing=OpportunityPrice(1000, 1000, "usd"),
            draft_editor_url="https://terac.com/opportunities/synthetic/edit",
        )


def test_live_study_start_persists_explicit_scope_and_inspect_stays_local(tmp_path):
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    checkpoint = tmp_path / f"{started.run_id}.json"
    checkpoint_bytes = checkpoint.read_bytes()

    assert started.mode == "live"
    assert started.status == "working"
    assert started.phase == "study_prepared"
    assert started.provider_observation["workflow"] == "oracle_study_creation"
    assert started.provider_observation["maximum_total_cost_cents"] == 10_000
    assert oracle.inspect(started.run_id) == started
    assert checkpoint.read_bytes() == checkpoint_bytes
    assert json.loads(checkpoint_bytes)["adapter_payload"]["workflow"] == "oracle_study_creation_v1"


def test_live_study_start_rejects_implicit_location_and_inferred_audience(tmp_path):
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())
    request = study_request()
    request["filters"] = [{"multi_select--language": {"$eq": "en-US"}}]

    with pytest.raises(ValueError, match="explicit country filter"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "country_filter",
    [
        {"multi_select--country": {"$eq": " "}},
        {"multi_select--country": {"$in": [" "]}},
    ],
)
def test_live_study_start_rejects_whitespace_country_selection(tmp_path, country_filter):
    request = study_request()
    request["filters"] = [country_filter]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError, match="explicit country filter"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "country_condition",
    [
        {"$eq": "ZZ"},
        {"$in": ["US", "ZZ", "CA"]},
    ],
)
def test_live_study_start_rejects_unknown_country_id_before_checkpoint(tmp_path, country_condition):
    request = study_request()
    request["filters"] = [{"multi_select--country": country_condition}]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError, match="explicit country filter"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("count", [100, 101])
@pytest.mark.parametrize("language_first", [True, False])
def test_live_study_start_preserves_long_country_selections(tmp_path, count, language_first):
    country_ids = ["US", "CA"] * (count // 2) + (["US"] if count % 2 else [])
    country_filter = {"multi_select--country": {"$in": country_ids}}
    language_filter = {"multi_select--language": {"$eq": "en-US"}}
    filters = (
        [language_filter, country_filter] if language_first else [country_filter, language_filter]
    )
    request = study_request()
    request["filters"] = filters
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    started = oracle.start("Synthetic brief", mode="live", study_request=request)

    assert started.provider_observation["request"]["filters"] == filters
    country_index = 1 if language_first else 0
    assert (
        started.provider_observation["request"]["filters"][country_index]["multi_select--country"][
            "$in"
        ]
        == country_ids
    )


def test_live_study_start_accepts_exact_country_snapshot_and_preserves_order(tmp_path):
    country_ids = sorted(TERAC_COUNTRY_FILTER_IDS, reverse=True)
    catalog_digest = hashlib.sha256("\n".join(sorted(country_ids)).encode()).hexdigest()
    assert len(country_ids) == 242
    assert catalog_digest == "b89860570553322b00fee8cebeb3ea578ff03ee02be9c7abcf594a39bc2c4804"
    assert "XK" in country_ids

    filters = [{"multi_select--country": {"$in": country_ids}}]
    request = study_request()
    request["filters"] = filters
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    started = oracle.start("Synthetic brief", mode="live", study_request=request)

    assert started.provider_observation["request"]["filters"] == filters


@pytest.mark.parametrize("country_values", [[], ["US", None], ["US", 7]])
def test_live_study_start_rejects_empty_or_malformed_country_lists(tmp_path, country_values):
    request = study_request()
    request["filters"] = [{"multi_select--country": {"$in": country_values}}]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "filters",
    [
        [
            {"multi_select--country": {"$eq": "US"}},
            {"multi_select--country": {"$eq": " "}},
        ],
        [
            {"multi_select--country": {"$eq": " "}},
            {"multi_select--country": {"$eq": "US"}},
        ],
        [
            {"multi_select--country": {"$eq": "US"}},
            {"multi_select--country": {"$in": [" "]}},
        ],
        [
            {"multi_select--country": {"$in": [" "]}},
            {"multi_select--country": {"$eq": "US"}},
        ],
        [
            {"multi_select--country": {"$eq": "US"}},
            {"multi_select--country": {"$eq": "GB"}},
        ],
    ],
)
def test_live_study_start_rejects_duplicate_country_selectors(tmp_path, filters):
    request = study_request()
    request["filters"] = filters
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError, match="explicit country filter"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


def test_oracle_file_upload_requires_manual_review_before_checkpoint_creation(tmp_path):
    request = study_request()
    request["tasks"][0]["review_type"] = "self_report"
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError, match="manual_review"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "task_url",
    [None, "", " ", "http://example.com/callback", "https://[::1", 7, ["https://example.com"]],
)
def test_live_study_start_rejects_auto_approval_with_invalid_url_before_checkpoint(
    tmp_path, task_url
):
    request = study_request()
    request["tasks"] = [
        {
            "sequence": 1,
            "task_type": "activity",
            "review_type": "auto_approve",
            "task_url": task_url,
            "duration_minutes": 15,
        }
    ]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError, match="automatic approval requires"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


def test_live_study_start_rejects_auto_approval_without_url_before_checkpoint(tmp_path):
    request = study_request()
    request["tasks"] = [
        {
            "sequence": 1,
            "task_type": "activity",
            "review_type": "auto_approve",
            "duration_minutes": 15,
        }
    ]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError, match="automatic approval requires"):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


def test_live_study_start_accepts_auto_approval_with_https_callback_url(tmp_path):
    request = study_request()
    request["tasks"] = [
        {
            "sequence": 1,
            "task_type": "activity",
            "review_type": "auto_approve",
            "task_url": "https://example.com/callback",
            "duration_minutes": 15,
        }
    ]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    started = oracle.start("Synthetic brief", mode="live", study_request=request)

    assert started.phase == "study_prepared"
    assert started.provider_observation["request"]["tasks"][0]["task_url"] == (
        "https://example.com/callback"
    )


@pytest.mark.parametrize("review_type", ["manual_review", "self_report"])
def test_activity_review_modes_without_auto_approval_still_allow_no_url(tmp_path, review_type):
    request = study_request()
    request["tasks"] = [
        {
            "sequence": 1,
            "task_type": "activity",
            "review_type": review_type,
            "duration_minutes": 15,
        }
    ]
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    started = oracle.start("Synthetic brief", mode="live", study_request=request)

    assert started.phase == "study_prepared"


def test_quote_length_limits_are_enforced_before_checkpoint_creation(tmp_path):
    request = study_request()
    request["quote"]["task_description"] = "x" * 10_001
    oracle = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(ValueError):
        oracle.start("Synthetic brief", mode="live", study_request=request)

    assert not list(tmp_path.glob("*.json"))


def test_feasibility_read_cannot_replace_the_bound_provider_identity(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    studies.feasibility_id_override = "different-feasibility"
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request["feasibility_request_id"] = "synthetic-feasibility"
    started = oracle.start("Synthetic brief", mode="live", study_request=request)

    stopped = oracle.resume(started.run_id)

    assert stopped.phase == "pricing_stopped"
    assert stopped.provider_observation["feasibility_request_id"] == "synthetic-feasibility"
    assert stopped.provider_observation["last_error"] == "feasibility_identity_mismatch"
    assert not any(call[0] == "create_opportunity" for call in studies.calls)


def test_empty_feasibility_price_is_stopped_before_draft_creation(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    studies.feasibility_price = " "
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())

    stopped = oracle.resume(started.run_id)

    assert stopped.phase == "pricing_stopped"
    assert stopped.provider_observation["last_error"] == "feasibility_not_priceable"
    assert not any(call[0] == "create_opportunity" for call in studies.calls)


def test_resume_saves_feasibility_intent_before_submitting_one_request(tmp_path):
    studies = FakeStudies()
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    checkpoint = tmp_path / f"{started.run_id}.json"
    studies.before_request = lambda: assert_checkpoint_intent(checkpoint)

    resumed = oracle.resume(started.run_id)

    assert resumed.phase == "pricing_pending"
    assert resumed.provider_observation["feasibility_request_id"] == "synthetic-feasibility"
    assert resumed.provider_observation["feasibility"]["cost_per_participant"] is None
    assert [call[0] for call in studies.calls] == ["request_feasibility"]
    assert studies.calls[0][1] == {
        "taskDescription": "Review the synthetic product and upload one report.",
        "panelDescription": "One English-speaking product professional in the US.",
        "submissionCount": 1,
        "loiMinutes": 15,
    }


def test_matching_quote_and_draft_review_each_use_a_separate_resume(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )

    quote_ready = oracle.resume(started.run_id)
    assert quote_ready.phase == "quote_ready"
    assert quote_ready.provider_observation["feasibility"]["cost_per_participant"] == "opaque quote"
    assert [call[0] for call in studies.calls] == ["request_feasibility"]

    draft_created = oracle.resume(started.run_id)
    assert draft_created.phase == "draft_needs_readback"
    assert [call[0] for call in studies.calls] == ["request_feasibility", "create_opportunity"]
    assert studies.calls[-1][1]["feasibility_request_id"] == "synthetic-feasibility"

    draft_review = oracle.resume(started.run_id)
    assert draft_review.phase == "awaiting_launch"
    assert draft_review.provider_observation["draft"]["pricing"]["total_cost_cents"] == 1000
    assert draft_review.provider_observation["confirmation_phrase"].startswith("LAUNCH ")
    assert [call[0] for call in studies.calls] == [
        "request_feasibility",
        "create_opportunity",
        "get_opportunity",
    ]


def test_responded_quote_without_dashboard_link_stops_before_draft_creation(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    studies.feasibility_dashboard_url = None
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())

    stopped = oracle.resume(started.run_id)

    assert stopped.phase == "pricing_stopped"
    assert stopped.provider_observation["last_error"] == "feasibility_review_unavailable"
    assert stopped.provider_observation["feasibility"]["dashboard_url"] is None
    assert [call[0] for call in studies.calls] == ["request_feasibility"]
    assert "confirmation_phrase" not in stopped.provider_observation
    assert oracle.inspect(started.run_id) == stopped


@pytest.mark.parametrize(
    ("phase", "operation", "outcome", "omit_draft"),
    [
        ("launching", "launch_opportunity", "pending", True),
        ("launching", "launch_opportunity", "pending", False),
        ("launch_outcome_unknown", "launch_opportunity", "unknown", True),
        ("launched", "create_opportunity", "succeeded", False),
    ],
)
def test_inspect_rejects_impossible_launch_checkpoint_combinations(
    tmp_path, phase, operation, outcome, omit_draft
):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    assert ready.phase == "awaiting_launch"

    checkpoint = tmp_path / f"{started.run_id}.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    saved["phase"] = phase
    saved["adapter_payload"]["write_intent"] = {
        "operation": operation,
        "outcome": outcome,
    }
    if omit_draft:
        saved["adapter_payload"]["draft"] = None
    elif phase == "launching":
        saved["adapter_payload"]["draft"]["status"] = "active"
    tampered = json.dumps(saved).encode("utf-8")
    checkpoint.write_bytes(tampered)
    studies.calls.clear()
    restarted = Oracle(tmp_path, study_adapter=studies)

    with pytest.raises(CheckpointIncompatibleError):
        restarted.inspect(started.run_id)

    assert checkpoint.read_bytes() == tampered
    assert studies.calls == []


def test_invalid_draft_link_from_readback_is_sanitized_and_blocks_confirmation(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    created = oracle.resume(started.run_id)
    assert created.phase == "draft_needs_readback"
    studies.draft_url_override = "https://exa mple.com/draft"

    blocked = oracle.resume(started.run_id)

    checkpoint = tmp_path / f"{started.run_id}.json"
    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_review_unavailable"
    assert blocked.provider_observation["draft"]["draft_editor_url"] is None
    assert "confirmation_phrase" not in blocked.provider_observation
    assert "exa mple.com" not in checkpoint.read_text(encoding="utf-8")
    assert oracle.inspect(started.run_id) == blocked


def test_invalid_draft_read_response_becomes_inspectable_blocked_state(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)

    def invalid_read(_opportunity_id):
        studies.calls.append(("get_opportunity", _opportunity_id))
        raise StudyReadError("invalid_response")

    studies.get_opportunity = invalid_read
    blocked = oracle.resume(started.run_id)

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_review_unavailable"
    assert oracle.inspect(started.run_id) == blocked


def test_draft_readback_rejects_a_changed_explicit_task_duration(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    draft_created = oracle.resume(started.run_id)
    assert draft_created.phase == "draft_needs_readback"
    studies.opportunity = replace(
        studies.opportunity,
        tasks=tuple({**task, "duration_minutes": 14} for task in studies.opportunity.tasks),
    )

    blocked = oracle.resume(started.run_id)

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_scope_mismatch"
    assert [call[0] for call in studies.calls] == [
        "request_feasibility",
        "create_opportunity",
        "get_opportunity",
    ]


@pytest.mark.parametrize("provider_field", ["tasks", "filters"])
def test_draft_scope_mismatch_does_not_persist_unexpected_provider_fields(
    tmp_path, caplog, provider_field
):
    marker = "PRIVATE_REPORT_MARKER_7d13"
    identity = "person+private@example.test"
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    draft_created = oracle.resume(started.run_id)
    draft_tasks = study_request()["tasks"]
    if provider_field == "tasks":
        provider_task = {
            **studies.opportunity.tasks[0],
            "expert_email": identity,
            "report_payload": {"confidential_text": marker},
        }
        studies.opportunity = replace(studies.opportunity, tasks=(provider_task,))
    else:
        provider_filter = {
            "multi_select--country": {
                **studies.opportunity.filters[0]["multi_select--country"],
                "report_payload": {"confidential_text": marker, "expert_email": identity},
            }
        }
        studies.opportunity = replace(studies.opportunity, filters=(provider_filter,))

    blocked = oracle.resume(started.run_id)

    checkpoint = tmp_path / f"{started.run_id}.json"
    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_scope_mismatch"
    assert blocked.provider_observation["draft"]["tasks"] == draft_tasks
    assert marker not in json.dumps(blocked.provider_observation)
    assert identity not in json.dumps(blocked.provider_observation)
    assert identity not in checkpoint.read_text(encoding="utf-8")
    assert marker not in checkpoint.read_text(encoding="utf-8")
    reloaded = Oracle(tmp_path, study_adapter=studies).inspect(started.run_id)
    assert reloaded.provider_observation["draft"]["tasks"] == draft_tasks
    assert marker not in json.dumps(reloaded.provider_observation)
    assert identity not in json.dumps(reloaded.provider_observation)
    assert marker not in caplog.text
    assert identity not in caplog.text
    assert draft_created.phase == "draft_needs_readback"


def test_checkpoint_reload_rejects_a_quote_for_a_different_task(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    quote_ready = oracle.resume(started.run_id)
    assert quote_ready.phase == "quote_ready"
    checkpoint = tmp_path / f"{started.run_id}.json"
    saved = json.loads(checkpoint.read_text())
    saved["adapter_payload"]["feasibility"]["task_description"] = "Different task"
    checkpoint.write_text(json.dumps(saved))
    tampered_bytes = checkpoint.read_bytes()
    studies.calls.clear()
    restarted = Oracle(tmp_path, study_adapter=studies)

    with pytest.raises(CheckpointIncompatibleError):
        restarted.inspect(started.run_id)

    assert checkpoint.read_bytes() == tampered_bytes
    assert studies.calls == []


def test_inspect_rejects_awaiting_launch_checkpoint_without_review_link(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    assert ready.phase == "awaiting_launch"

    checkpoint = tmp_path / f"{started.run_id}.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    saved["adapter_payload"]["draft"]["draft_editor_url"] = None
    checkpoint.write_text(json.dumps(saved), encoding="utf-8")
    tampered_bytes = checkpoint.read_bytes()
    studies.calls.clear()
    restarted = Oracle(tmp_path, study_adapter=studies)

    with pytest.raises(CheckpointIncompatibleError):
        restarted.inspect(started.run_id)

    assert checkpoint.read_bytes() == tampered_bytes
    assert studies.calls == []


@pytest.mark.parametrize("corruption", ["missing", "pending", "missing_price", "empty_price"])
def test_checkpoint_reload_rejects_launch_without_confirmed_quote(tmp_path, corruption):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    assert ready.phase == "awaiting_launch"

    checkpoint = tmp_path / f"{started.run_id}.json"
    saved = json.loads(checkpoint.read_text())
    feasibility = saved["adapter_payload"]["feasibility"]
    if corruption == "missing":
        saved["adapter_payload"]["feasibility"] = None
    elif corruption == "pending":
        feasibility["status"] = "RECEIVED"
    elif corruption == "empty_price":
        feasibility["cost_per_participant"] = " "
    else:
        feasibility["cost_per_participant"] = None
    checkpoint.write_text(json.dumps(saved))
    tampered_bytes = checkpoint.read_bytes()
    studies.calls.clear()
    restarted = Oracle(tmp_path, study_adapter=studies)

    with pytest.raises(CheckpointIncompatibleError):
        restarted.inspect(started.run_id)

    assert checkpoint.read_bytes() == tampered_bytes
    assert studies.calls == []


def test_launch_requires_fresh_exact_scope_price_cap_and_phrase(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    review = ready.provider_observation
    args = {
        "expected_total_cost_cents": 1000,
        "maximum_total_cost_cents": 10_000,
        "scope_sha256": review["scope_sha256"],
        "confirmation": review["confirmation_phrase"],
    }

    with pytest.raises(ValueError):
        oracle.launch(started.run_id, **{**args, "confirmation": "yes"})
    assert [call[0] for call in studies.calls] == [
        "request_feasibility",
        "create_opportunity",
        "get_opportunity",
    ]

    launched = oracle.launch(started.run_id, **args)

    assert launched.phase == "launched"
    assert launched.provider_observation["draft"]["status"] == "active"
    assert [call[0] for call in studies.calls] == [
        "request_feasibility",
        "create_opportunity",
        "get_opportunity",
        "get_opportunity",
        "launch_opportunity",
    ]
    assert oracle.inspect(started.run_id) == launched


def test_non_launch_provider_status_does_not_claim_success_or_offer_confirmation(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.opportunity = replace(studies.opportunity, status="cancelled")

    observed = oracle.resume(started.run_id)

    assert observed.phase == "launch_blocked"
    assert observed.provider_observation["last_error"] == "draft_not_launchable"
    assert "confirmation_phrase" not in observed.provider_observation
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_non_launch_status_at_launch_preflight_blocks_without_claiming_success(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    studies.opportunity = replace(studies.opportunity, status="cancelled")
    review = ready.provider_observation

    blocked = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=review["scope_sha256"],
        confirmation=review["confirmation_phrase"],
    )

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_not_launchable"
    assert blocked.provider_observation["draft"]["id"] == "synthetic-opportunity"
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_failed_launch_response_stays_unknown_and_is_never_retried(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    studies.launch_status = "failed"
    review = ready.provider_observation

    uncertain = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=review["scope_sha256"],
        confirmation=review["confirmation_phrase"],
    )

    assert uncertain.phase == "launch_outcome_unknown"
    assert uncertain.provider_observation["last_error"] == "launch_response_unconfirmed"
    still_uncertain = oracle.resume(started.run_id)
    assert still_uncertain.phase == "launch_outcome_unknown"
    assert still_uncertain.provider_observation["last_error"] == "launch_status_unconfirmed"
    assert [call[0] for call in studies.calls].count("launch_opportunity") == 1


def test_blocked_draft_can_be_refreshed_and_reassessed(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.pricing_override = OpportunityPrice(None, None, None)
    missing_price = oracle.resume(started.run_id)
    assert missing_price.phase == "launch_blocked"
    assert missing_price.provider_observation["last_error"] == "draft_review_unavailable"
    studies.pricing_override = OpportunityPrice(1000, 1000, "usd")

    refreshed = oracle.resume(started.run_id)

    assert refreshed.phase == "awaiting_launch"
    assert refreshed.provider_observation["last_error"] == "price_changed_review_required"
    assert refreshed.provider_observation["draft"]["pricing"]["total_cost_cents"] == 1000
    assert [call[0] for call in studies.calls].count("get_opportunity") == 2


def test_draft_readback_cannot_replace_the_bound_provider_identity(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.opportunity_id_override = "different-opportunity"

    blocked = oracle.resume(started.run_id)

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_identity_mismatch"
    assert blocked.provider_observation["draft"]["id"] == "synthetic-opportunity"
    assert "confirmation_phrase" not in blocked.provider_observation


def test_launch_preflight_cannot_replace_the_bound_draft_identity(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    studies.opportunity_id_override = "different-opportunity"
    review = ready.provider_observation

    blocked = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=review["scope_sha256"],
        confirmation=review["confirmation_phrase"],
    )

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_identity_mismatch"
    assert blocked.provider_observation["draft"]["id"] == "synthetic-opportunity"
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_launch_response_cannot_replace_the_bound_draft_identity(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    studies.launch_id_override = "different-opportunity"
    review = ready.provider_observation

    uncertain = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=review["scope_sha256"],
        confirmation=review["confirmation_phrase"],
    )

    assert uncertain.phase == "launch_outcome_unknown"
    assert uncertain.provider_observation["draft"]["id"] == "synthetic-opportunity"
    assert uncertain.provider_observation["last_error"] == "launch_response_unconfirmed"


def test_launch_reconciliation_cannot_replace_the_bound_draft_identity(tmp_path):
    first_provider = FakeStudies()
    first_provider.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=first_provider)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    confirmation = ready.provider_observation["confirmation_phrase"]

    def stop_after_launch_intent():
        raise SystemExit("simulated process exit")

    first_provider.before_launch = stop_after_launch_intent
    with pytest.raises(SystemExit):
        oracle.launch(
            started.run_id,
            expected_total_cost_cents=1000,
            maximum_total_cost_cents=10_000,
            scope_sha256=ready.provider_observation["scope_sha256"],
            confirmation=confirmation,
        )

    second_provider = FakeStudies()
    second_provider.opportunity = replace(
        first_provider.opportunity,
        status="active",
    )
    second_provider.opportunity_id_override = "different-opportunity"
    restarted = Oracle(tmp_path, study_adapter=second_provider)
    uncertain = restarted.resume(started.run_id)

    assert uncertain.phase == "launch_outcome_unknown"
    assert uncertain.provider_observation["last_error"] == "draft_identity_mismatch"
    assert uncertain.provider_observation["draft"]["id"] == "synthetic-opportunity"


def test_uncertain_feasibility_write_is_inspectable_and_never_retried(tmp_path):
    studies = FakeStudies()
    studies.request_error = StudyWriteError("unknown_write_outcome", "unknown")
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )

    uncertain = oracle.resume(started.run_id)
    checkpoint = json.loads((tmp_path / f"{started.run_id}.json").read_text())
    assert uncertain.phase == "unknown_write_outcome"
    assert checkpoint["adapter_payload"]["write_intent"] == {
        "operation": "request_feasibility",
        "outcome": "unknown",
    }

    assert oracle.inspect(started.run_id) == uncertain
    assert oracle.resume(started.run_id) == uncertain
    assert len(studies.calls) == 1


def test_interrupted_feasibility_write_is_not_replayed_after_restart(tmp_path):
    first_provider = FakeStudies()
    oracle = Oracle(tmp_path, study_adapter=first_provider)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    checkpoint = tmp_path / f"{started.run_id}.json"

    def stop_after_intent():
        assert_checkpoint_intent(checkpoint)
        raise SystemExit("simulated process exit")

    first_provider.before_request = stop_after_intent
    with pytest.raises(SystemExit):
        oracle.resume(started.run_id)

    after_restart_provider = FakeStudies()
    restarted = Oracle(tmp_path, study_adapter=after_restart_provider)
    recovered = restarted.resume(started.run_id)
    assert recovered.phase == "unknown_write_outcome"
    assert after_restart_provider.calls == []


def test_launch_price_change_requires_reconfirmation_after_fresh_read(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    old_observation = ready.provider_observation
    old_confirmation = old_observation["confirmation_phrase"]
    studies.price_override = 2000

    changed = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=old_observation["scope_sha256"],
        confirmation=old_confirmation,
    )

    assert changed.phase == "awaiting_launch"
    assert changed.provider_observation["last_error"] == "price_changed_review_required"
    assert changed.provider_observation["draft"]["pricing"]["total_cost_cents"] == 2000
    new_confirmation = changed.provider_observation["confirmation_phrase"]
    assert new_confirmation != old_confirmation
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)

    launched = oracle.launch(
        started.run_id,
        expected_total_cost_cents=2000,
        maximum_total_cost_cents=10_000,
        scope_sha256=old_observation["scope_sha256"],
        confirmation=new_confirmation,
    )
    assert launched.phase == "launched"


def test_launch_url_change_blocks_with_truthful_reason_until_explicit_resume(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    old_observation = ready.provider_observation
    old_confirmation = old_observation["confirmation_phrase"]
    changed_url = "https://terac.com/opportunities/changed/edit"
    studies.draft_url_override = changed_url

    blocked = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=old_observation["scope_sha256"],
        confirmation=old_confirmation,
    )

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_url_changed_review_required"
    assert blocked.provider_observation["draft"]["draft_editor_url"] == changed_url
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)

    refreshed = oracle.resume(started.run_id)

    assert refreshed.phase == "awaiting_launch"
    assert refreshed.provider_observation["confirmation_phrase"] != old_confirmation
    assert refreshed.provider_observation["draft"]["draft_editor_url"] == changed_url
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


@pytest.mark.parametrize(
    ("price_override", "expected_phase", "expected_error"),
    [
        (2000, "awaiting_launch", "price_changed_review_required"),
        (20_000, "launch_blocked", "price_over_cap"),
    ],
)
def test_launch_price_reason_precedes_changed_url_reason(
    tmp_path, price_override, expected_phase, expected_error
):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    studies.price_override = price_override
    studies.draft_url_override = "https://terac.com/opportunities/changed/edit"

    changed = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=ready.provider_observation["scope_sha256"],
        confirmation=ready.provider_observation["confirmation_phrase"],
    )

    assert changed.phase == expected_phase
    assert changed.provider_observation["last_error"] == expected_error
    assert changed.provider_observation["draft"]["draft_editor_url"] == (studies.draft_url_override)
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_draft_url_change_requires_reconfirmation_with_truthful_reason(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    old_observation = ready.provider_observation
    old_confirmation = old_observation["confirmation_phrase"]
    studies.draft_url_override = "https://terac.com/opportunities/synthetic/review"

    changed = oracle.resume(started.run_id)

    observation = changed.provider_observation
    assert changed.phase == "awaiting_launch"
    assert observation["last_error"] == "draft_url_changed_review_required"
    assert observation["draft"]["draft_editor_url"] == studies.draft_url_override
    assert observation["confirmation_phrase"] != old_confirmation
    assert [call[0] for call in studies.calls].count("launch_opportunity") == 0


def test_launch_confirmation_is_bound_to_the_displayed_feasibility_quote(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    old_observation = ready.provider_observation
    old_confirmation = old_observation["confirmation_phrase"]

    checkpoint_path = tmp_path / f"{started.run_id}.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["adapter_payload"]["feasibility"]["cost_per_participant"] = "changed quote"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    after_restart_provider = FakeStudies()
    after_restart_provider.opportunity = studies.opportunity
    restarted = Oracle(tmp_path, study_adapter=after_restart_provider)
    inspected = restarted.inspect(started.run_id)
    new_observation = inspected.provider_observation
    new_confirmation = new_observation["confirmation_phrase"]

    assert new_observation["feasibility"]["cost_per_participant"] == "changed quote"
    assert new_confirmation != old_confirmation
    with pytest.raises(ValueError, match="launch confirmation"):
        restarted.launch(
            started.run_id,
            expected_total_cost_cents=1000,
            maximum_total_cost_cents=10_000,
            scope_sha256=old_observation["scope_sha256"],
            confirmation=old_confirmation,
        )
    assert after_restart_provider.calls == []

    launched = restarted.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=new_observation["scope_sha256"],
        confirmation=new_confirmation,
    )
    assert launched.phase == "launched"


def test_launch_preflight_blocks_price_that_has_crossed_the_cap(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    ready = oracle.resume(started.run_id)
    studies.price_override = 10_001

    blocked = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=ready.provider_observation["scope_sha256"],
        confirmation=ready.provider_observation["confirmation_phrase"],
    )

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "price_over_cap"
    assert blocked.provider_observation["draft"]["pricing"]["total_cost_cents"] == 10_001
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_documented_study_request_can_omit_optional_customer_screening_review(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    create_opportunity = studies.create_opportunity

    def create_with_provider_default(definition):
        return replace(
            create_opportunity(definition),
            customer_screening_review="auto_invite",
        )

    studies.create_opportunity = create_with_provider_default
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request.pop("customer_screening_review")

    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=request,
    )
    oracle.resume(started.run_id)
    created = oracle.resume(started.run_id)

    assert created.phase == "draft_needs_readback"
    assert "customer_screening_review" not in studies.calls[-1][1]
    assert created.provider_observation["draft"]["customer_screening_review"] is None
    studies.opportunity = replace(
        studies.opportunity,
        customer_screening_review="manual_review",
    )

    reviewed = oracle.resume(started.run_id)

    assert reviewed.phase == "awaiting_launch"
    assert reviewed.provider_observation["draft"]["customer_screening_review"] == "manual_review"
    assert (
        reviewed.provider_observation["scope_sha256"]
        == started.provider_observation["scope_sha256"]
    )
    assert "SCREENING manual_review" in reviewed.provider_observation["confirmation_phrase"]


def test_incomplete_create_response_keeps_exact_readback_eligible_without_screening(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    create_opportunity = studies.create_opportunity

    def create_with_incomplete_response(definition):
        response = create_opportunity(definition)
        studies.opportunity = replace(
            studies.opportunity,
            customer_screening_review="manual_review",
        )
        return replace(
            response,
            customer_screening_review=None,
            pricing=OpportunityPrice(None, None, None),
        )

    studies.create_opportunity = create_with_incomplete_response
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request.pop("customer_screening_review")
    started = oracle.start("Synthetic brief", mode="live", study_request=request)
    oracle.resume(started.run_id)

    created = oracle.resume(started.run_id)
    assert created.phase == "draft_needs_readback"
    assert "confirmation_phrase" not in created.provider_observation

    reviewed = oracle.resume(started.run_id)

    assert reviewed.phase == "awaiting_launch"
    assert reviewed.provider_observation["last_error"] == "price_changed_review_required"
    assert reviewed.provider_observation["draft"]["customer_screening_review"] == "manual_review"
    assert reviewed.provider_observation["draft"]["pricing"]["total_cost_cents"] == 1000
    assert [call[0] for call in studies.calls].count("create_opportunity") == 1
    assert [call[0] for call in studies.calls].count("get_opportunity") == 1


@pytest.mark.parametrize("nested_field", ["tasks", "filters"])
def test_inspect_rejects_legacy_checkpoint_with_unexpected_provider_payload(tmp_path, nested_field):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start(
        "Synthetic brief with no real participant data.",
        mode="live",
        study_request=study_request(),
    )
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)

    checkpoint = tmp_path / f"{started.run_id}.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    marker = "PRIVATE_LEGACY_PROVIDER_MARKER_61c7"
    draft = saved["adapter_payload"]["draft"]
    if nested_field == "tasks":
        draft["tasks"][0]["expert_email"] = "legacy-private@example.test"
        draft["tasks"][0]["report_payload"] = {"confidential_text": marker}
    else:
        draft["filters"][0]["multi_select--country"]["report_payload"] = marker
    checkpoint.write_text(json.dumps(saved), encoding="utf-8")
    restarted = Oracle(tmp_path, study_adapter=NoCallStudies())

    with pytest.raises(CheckpointIncompatibleError) as error:
        restarted.inspect(started.run_id)

    assert marker not in str(error.value)
    assert "legacy-private@example.test" not in str(error.value)


@pytest.mark.parametrize("resolved_mode", ["auto_invite", "manual_review"])
def test_optional_screening_mode_stays_bound_through_launch(tmp_path, resolved_mode):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request.pop("customer_screening_review")
    started = oracle.start("Synthetic brief", mode="live", study_request=request)
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.opportunity = replace(
        studies.opportunity,
        customer_screening_review=resolved_mode,
    )

    reviewed = oracle.resume(started.run_id)
    launched = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=reviewed.provider_observation["scope_sha256"],
        confirmation=reviewed.provider_observation["confirmation_phrase"],
    )

    assert reviewed.provider_observation["draft"]["customer_screening_review"] == resolved_mode
    assert launched.phase == "launched"
    assert launched.provider_observation["draft"]["customer_screening_review"] == resolved_mode


@pytest.mark.parametrize(
    ("resolved_mode", "changed_mode"),
    [("auto_invite", "manual_review"), ("manual_review", "auto_invite")],
)
def test_optional_screening_mode_change_blocks_launch_without_effect(
    tmp_path, resolved_mode, changed_mode
):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request.pop("customer_screening_review")
    started = oracle.start("Synthetic brief", mode="live", study_request=request)
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.opportunity = replace(
        studies.opportunity,
        customer_screening_review=resolved_mode,
    )
    reviewed = oracle.resume(started.run_id)
    studies.opportunity = replace(
        studies.opportunity,
        customer_screening_review=changed_mode,
    )

    blocked = oracle.launch(
        started.run_id,
        expected_total_cost_cents=1000,
        maximum_total_cost_cents=10_000,
        scope_sha256=reviewed.provider_observation["scope_sha256"],
        confirmation=reviewed.provider_observation["confirmation_phrase"],
    )

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_scope_mismatch"
    assert blocked.provider_observation["draft"]["customer_screening_review"] == resolved_mode
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_missing_screening_resolution_cannot_offer_launch_confirmation(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request.pop("customer_screening_review")
    started = oracle.start("Synthetic brief", mode="live", study_request=request)
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)

    blocked = oracle.resume(started.run_id)

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_review_unavailable"
    assert "confirmation_phrase" not in blocked.provider_observation
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


def test_null_screening_checkpoint_cannot_be_migrated_into_launch_permission(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    request = study_request()
    request.pop("customer_screening_review")
    started = oracle.start("Synthetic brief", mode="live", study_request=request)
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.opportunity = replace(
        studies.opportunity,
        customer_screening_review="auto_invite",
    )
    reviewed = oracle.resume(started.run_id)
    checkpoint = tmp_path / f"{started.run_id}.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    saved["adapter_payload"]["draft"]["customer_screening_review"] = None
    checkpoint.write_text(json.dumps(saved), encoding="utf-8")
    after_restart = FakeStudies()
    restarted = Oracle(tmp_path, study_adapter=after_restart)

    inspected = restarted.inspect(started.run_id)
    assert "confirmation_phrase" not in inspected.provider_observation
    with pytest.raises(ValueError):
        restarted.launch(
            started.run_id,
            expected_total_cost_cents=1000,
            maximum_total_cost_cents=10_000,
            scope_sha256=reviewed.provider_observation["scope_sha256"],
            confirmation=reviewed.provider_observation["confirmation_phrase"],
        )
    assert after_restart.calls == []


def test_explicit_screening_mode_mismatch_blocks_draft_readback(tmp_path):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    oracle = Oracle(tmp_path, study_adapter=studies)
    started = oracle.start("Synthetic brief", mode="live", study_request=study_request())
    oracle.resume(started.run_id)
    oracle.resume(started.run_id)
    studies.opportunity = replace(
        studies.opportunity,
        customer_screening_review="auto_invite",
    )

    blocked = oracle.resume(started.run_id)

    assert blocked.phase == "launch_blocked"
    assert blocked.provider_observation["last_error"] == "draft_scope_mismatch"
    assert not any(call[0] == "launch_opportunity" for call in studies.calls)


@pytest.mark.anyio
async def test_mcp_registers_oracle_study_workflow_and_launches_only_with_bound_confirmation(
    tmp_path,
):
    studies = FakeStudies()
    studies.feasibility_status = "RESPONDED"
    server = create_default_server(tmp_path, study_adapter=studies)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert "oracle_launch" in tools
    resume_description = " ".join(tools["oracle_resume"].description.split())
    assert "may submit a feasibility request or create a draft" in resume_description.lower()
    assert "it never launches" in resume_description.lower()
    launch_description = " ".join(tools["oracle_launch"].description.split())
    assert "show the user the exact review summary" in launch_description.lower()
    assert "audience, task, screening mode" in launch_description.lower()
    assert "feasibility quote and dashboard link" in launch_description.lower()
    assert "explicit fresh confirmation" in launch_description.lower()
    assert "pre-launch price check" in launch_description.lower()
    assert "no atomic price/cap condition" in launch_description.lower()
    assert "pricing and publication remain under terac's control" in launch_description.lower()

    started_result = await server.call_tool(
        "oracle_start",
        {
            "brief": "Synthetic brief with no real participant data.",
            "mode": "live",
            "study_request": study_request(),
        },
    )
    started = started_result.structuredContent
    assert started_result.isError is False
    assert [call[0] for call in studies.calls] == []

    for _ in range(3):
        resumed_result = await server.call_tool("oracle_resume", {"run_id": started["run_id"]})
        assert resumed_result.isError is False
    ready = resumed_result.structuredContent
    observation = ready["provider_observation"]
    launched_result = await server.call_tool(
        "oracle_launch",
        {
            "run_id": started["run_id"],
            "expected_total_cost_cents": 1000,
            "maximum_total_cost_cents": 10_000,
            "scope_sha256": observation["scope_sha256"],
            "confirmation": observation["confirmation_phrase"],
        },
    )
    assert launched_result.isError is False
    assert launched_result.structuredContent["phase"] == "launched"

    discovery_result = await server.call_tool("cookbook_list_recipes", {})
    discovery = json.loads(discovery_result[0].text)
    assert discovery["recipes"][0]["study_creation_available"] is True
    assert discovery["recipes"][0]["live_available"] is True


def assert_checkpoint_intent(checkpoint):
    saved = json.loads(checkpoint.read_text())
    assert saved["adapter_payload"]["write_intent"] == {
        "operation": "request_feasibility",
        "outcome": "pending",
    }


def _snapshot():
    return Snapshot(
        run_id="synthetic-run",
        parent_id=None,
        intent="review",
        artifact_id="a" * 64,
        brief="Synthetic brief",
        scope=("buyer_pain", "willingness_to_pay", "channel_fit"),
        mode="live",
        status="working",
        phase="awaiting_launch",
        question=None,
        feedback=None,
        error=None,
        provider_observation={"workflow": "oracle_study_creation"},
    )


def test_cli_loads_a_private_explicit_study_request_without_calling_terac(
    tmp_path, monkeypatch, capsys
):
    import recipes.oracle.__main__ as cli

    request_path = tmp_path / "study-request.json"
    request_path.write_text(json.dumps(study_request()), encoding="utf-8")
    captured = {}

    class FakeOracle:
        def __init__(self, _state_dir, **kwargs):
            captured.update(kwargs)

        def start(self, brief, **kwargs):
            captured.update({"brief": brief, **kwargs})
            return _snapshot()

    monkeypatch.setattr(cli, "Oracle", FakeOracle)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "terac-oracle",
            "--state-dir",
            str(tmp_path / "runs"),
            "--terac-study-creation",
            "start",
            "--brief",
            "Synthetic CLI brief",
            "--mode",
            "live",
            "--study-request-file",
            str(request_path),
        ],
    )

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "awaiting_launch"
    assert captured["brief"] == "Synthetic CLI brief"
    assert captured["study_request"] == study_request()
    assert captured["study_adapter"] is not None


def test_cli_launch_passes_the_fresh_confirmation_fields_to_oracle(tmp_path, monkeypatch, capsys):
    import recipes.oracle.__main__ as cli

    captured = {}

    class FakeOracle:
        def __init__(self, _state_dir, **_kwargs):
            pass

        def launch(self, run_id, **kwargs):
            captured.update({"run_id": run_id, **kwargs})
            return _snapshot()

    monkeypatch.setattr(cli, "Oracle", FakeOracle)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "terac-oracle",
            "--state-dir",
            str(tmp_path / "runs"),
            "launch",
            "synthetic-run",
            "--expected-total-cost-cents",
            "1000",
            "--maximum-total-cost-cents",
            "10000",
            "--scope-sha256",
            "b" * 64,
            "--confirmation",
            "synthetic exact confirmation",
        ],
    )

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "awaiting_launch"
    assert captured == {
        "run_id": "synthetic-run",
        "expected_total_cost_cents": 1000,
        "maximum_total_cost_cents": 10_000,
        "scope_sha256": "b" * 64,
        "confirmation": "synthetic exact confirmation",
    }


def test_cli_help_describes_replay_default_and_opt_in_terac_studies(monkeypatch, capsys):
    import recipes.oracle.__main__ as cli

    monkeypatch.setattr(sys, "argv", ["terac-oracle", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.lower().split())
    assert "replay is the default" in help_text
    assert "terac study creation uses --terac-study-creation with --mode live" in help_text
    assert "launch requires fresh confirmation" in help_text
