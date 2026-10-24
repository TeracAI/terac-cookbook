import json
import subprocess
import sys

import pytest

from recipes.catalog import RecipeCatalog
from recipes.oracle import Oracle
from recipes.oracle.binding import OracleRecipeService
from recipes.owner_verification import (
    ConsentRequiredError,
    InvalidReplayCaseError,
    LiveUnavailableError,
    NoClarificationError,
    OwnerVerification,
    UnredactedInputError,
    artifact_id_for,
)
from recipes.owner_verification.binding import OwnerVerificationRecipeService
from runtime.runner import CheckpointIncompatibleError

SUMMARY = (
    "Synthetic call: the owner of a fictional 12-person landscaping business describes "
    "crews, equipment and a two-year succession plan."
)


@pytest.mark.parametrize(
    ("case", "verdict"),
    [(None, "verified"), ("verified", "verified"), ("concerns", "concerns"), ("fail", "fail")],
)
def test_replay_delivers_bound_verdict_and_inspection_is_pure(tmp_path, case, verdict):
    recipe = OwnerVerification(tmp_path)
    started = recipe.start(SUMMARY, consent_recorded=True, replay_case=case)
    assert started.status == "working" and started.result is None
    assert started.artifact_id == artifact_id_for(SUMMARY)
    checkpoint = tmp_path / f"{started.run_id}.json"
    before = checkpoint.read_bytes()
    assert recipe.inspect(started.run_id) == started
    assert checkpoint.read_bytes() == before

    delivered = recipe.resume(started.run_id)
    assert delivered.status == "succeeded" and delivered.phase == "verdict_available"
    assert delivered.result.verdict == verdict
    assert delivered.result.artifact_id == started.artifact_id
    assert delivered.result.provenance == "synthetic"
    assert delivered.result.reasons
    if verdict == "verified":
        assert delivered.result.red_flags == ()
    if verdict == "fail":
        assert delivered.result.red_flags

    restarted = OwnerVerification(tmp_path)
    assert restarted.inspect(started.run_id) == delivered
    assert restarted.resume(started.run_id) == delivered


def test_invalid_input_is_rejected_without_saving(tmp_path):
    recipe = OwnerVerification(tmp_path)
    with pytest.raises(ConsentRequiredError):
        recipe.start(SUMMARY, consent_recorded=False)
    with pytest.raises(UnredactedInputError):
        recipe.start("Reach the owner at owner@example.com", consent_recorded=True)
    with pytest.raises(UnredactedInputError):
        recipe.start("Call back on +1 (555) 010-0199", consent_recorded=True)
    with pytest.raises(InvalidReplayCaseError):
        recipe.start(SUMMARY, consent_recorded=True, replay_case="pass")
    with pytest.raises(LiveUnavailableError):
        recipe.start(SUMMARY, consent_recorded=True, mode="live")
    with pytest.raises(ValueError):
        recipe.start("   ", consent_recorded=True)
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_no_clarification_and_unchanged_state(tmp_path):
    recipe = OwnerVerification(tmp_path)
    run = recipe.start(SUMMARY, consent_recorded=True)
    with pytest.raises(NoClarificationError):
        recipe.respond(run.run_id, "anything")
    assert recipe.inspect(run.run_id) == run


def test_tampered_summary_or_foreign_checkpoint_is_incompatible(tmp_path):
    recipe = OwnerVerification(tmp_path)
    run = recipe.start(SUMMARY, consent_recorded=True)
    path = tmp_path / f"{run.run_id}.json"
    data = json.loads(path.read_text())
    data["recipe_payload"]["summary"] = "A different summary"
    path.write_text(json.dumps(data))
    with pytest.raises(CheckpointIncompatibleError):
        recipe.inspect(run.run_id)

    oracle = Oracle(tmp_path)
    oracle_run = oracle.start("Synthetic brief")
    with pytest.raises(CheckpointIncompatibleError):
        recipe.inspect(oracle_run.run_id)
    with pytest.raises(CheckpointIncompatibleError):
        oracle.inspect(OwnerVerification(tmp_path).start(SUMMARY, consent_recorded=True).run_id)


def test_catalog_composes_with_oracle(tmp_path):
    catalog = RecipeCatalog(
        (
            OracleRecipeService(Oracle(tmp_path)),
            OwnerVerificationRecipeService(OwnerVerification(tmp_path)),
        )
    )
    entries = {entry["id"]: entry for entry in catalog.list_recipes()}
    assert entries["owner_verification"]["modes"] == ["replay"]
    assert entries["owner_verification"]["default_replay_case"] == "verified"
    service = catalog.get("owner_verification")
    run = service.start({"summary": SUMMARY, "consent_recorded": True, "replay_case": "fail"})
    assert service.resume(run.run_id).result.verdict == "fail"
    with pytest.raises(ValueError):
        service.start({"summary": SUMMARY, "consent_recorded": True, "brief": "x"})


def test_cli_replay(tmp_path):
    def call(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "recipes.owner_verification",
                "--state-dir",
                str(tmp_path),
                *args,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode, json.loads(result.stdout)

    code, started = call("start", "--consent-recorded", "--summary", SUMMARY)
    assert code == 0 and started["status"] == "working"
    code, delivered = call("resume", started["run_id"])
    assert code == 0 and delivered["result"]["verdict"] == "verified"
    code, error = call("start", "--summary", SUMMARY)
    assert code == 2 and error == {"error": "consent_required"}
