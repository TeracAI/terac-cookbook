from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from recipes.catalog import (
    DuplicateRecipeError,
    RecipeBindingError,
    RecipeCatalog,
    UnknownRecipeError,
)
from recipes.oracle import Oracle
from recipes.oracle.binding import OracleRecipeService
from runtime.runner import CheckpointIncompatibleError, InvalidRunIDError, Runner, RunState


@dataclass(frozen=True)
class SumSnapshot:
    run_id: str
    status: str
    phase: str
    result: dict[str, int] | None


class SumInputError(ValueError):
    code = "sum_invalid_values"


class SumAdapter:
    def observe(self, state: RunState) -> dict[str, Any] | None:
        if state.status != "working":
            return None
        values = state.recipe_payload["values"]
        return {"kind": "sum_result", "result": {"count": len(values), "sum": sum(values)}}

    def validate_progress(self, state: RunState) -> None:
        if state.adapter_payload != {"cursor": 0 if state.status == "working" else 1}:
            raise ValueError("Sum cursor is incompatible")


class SumRecipe:
    recipe_id = "sum"
    name = "Integer sum"
    modes = ("replay",)

    def __init__(self, state_dir: Path) -> None:
        self._adapter = SumAdapter()
        self._runner = Runner(
            state_dir,
            recipe_id=self.recipe_id,
            adapter_for_state=lambda state: self._adapter,
            transition_for_event=self._transition,
            validate_run=self._validate,
        )

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "id": self.recipe_id,
            "name": self.name,
            "modes": list(self.modes),
            "live_available": False,
            "default_replay_case": None,
            "replay_cases": [],
        }

    def start(self, request: object) -> SumSnapshot:
        if not isinstance(request, dict) or set(request) != {"values"}:
            raise ValueError("Expected an integer values list")
        values = request["values"]
        if (
            not isinstance(values, list)
            or not values
            or any(type(value) is not int for value in values)
        ):
            raise SumInputError("Expected an integer values list")
        artifact_id = hashlib.sha256(
            __import__("json").dumps(values, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        state = self._runner.start(
            recipe_payload={"values": values, "artifact_id": artifact_id, "result": None},
            adapter_payload={"cursor": 0},
            intent="sum",
            mode="replay",
            recipe_contract_version=1,
            phase="awaiting_measurements",
        )
        return self._snapshot(state)

    def inspect(self, run_id: str) -> SumSnapshot:
        return self._snapshot(self._runner.inspect(run_id))

    def respond(self, run_id: str, response: object) -> SumSnapshot:
        self._runner.inspect(run_id)
        raise ValueError("Integer sum has no clarification")

    def resume(self, run_id: str) -> SumSnapshot:
        return self._snapshot(self._runner.resume(run_id))

    @staticmethod
    def _transition(event: object, state: RunState) -> dict[str, Any]:
        if (
            not isinstance(event, dict)
            or set(event) != {"kind", "result"}
            or event["kind"] != "sum_result"
            or event["result"]
            != {
                "count": len(state.recipe_payload["values"]),
                "sum": sum(state.recipe_payload["values"]),
            }
        ):
            raise ValueError("Invalid integer sum event")
        return {
            "status": "succeeded",
            "phase": "aggregate_ready",
            "recipe_payload": {**state.recipe_payload, "result": event["result"]},
            "adapter_payload": {"cursor": 1},
        }

    @staticmethod
    def _validate(state: RunState) -> None:
        values = state.recipe_payload.get("values")
        if (
            not isinstance(values, list)
            or not values
            or any(type(value) is not int for value in values)
        ):
            raise ValueError("Invalid integer sum checkpoint")
        expected_digest = hashlib.sha256(
            __import__("json").dumps(values, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        if state.recipe_payload.get("artifact_id") != expected_digest:
            raise ValueError("Integer sum input binding is incompatible")
        expected_phase = {
            "working": "awaiting_measurements",
            "succeeded": "aggregate_ready",
        }.get(state.status)
        if expected_phase is not None and state.phase != expected_phase:
            raise ValueError("Integer sum phase is incompatible")
        result = state.recipe_payload.get("result")
        if state.status == "succeeded":
            if result != {"count": len(values), "sum": sum(values)}:
                raise ValueError("Integer sum result is incompatible")
        elif result is not None:
            raise ValueError("Working integer sum has a result")

    @staticmethod
    def _snapshot(state: RunState) -> SumSnapshot:
        return SumSnapshot(
            run_id=state.run_id,
            status=state.status,
            phase=state.phase,
            result=state.recipe_payload.get("result"),
        )


def test_python_catalog_runs_distinct_recipe_through_shared_lifecycle_and_restart(tmp_path):
    oracle = OracleRecipeService(Oracle(tmp_path))
    total = SumRecipe(tmp_path)
    catalog = RecipeCatalog((oracle, total))

    assert [entry["id"] for entry in catalog.list_recipes()] == ["oracle", "sum"]
    assert catalog.get("oracle").catalog_entry()["id"] == "oracle"

    service = catalog.get("sum")
    started = service.start({"values": [4, 7, 11]})
    assert started.status == "working"
    assert started.phase == "awaiting_measurements"
    assert started.result is None
    assert service.inspect(started.run_id) == started

    checkpoint = tmp_path / f"{started.run_id}.json"
    stored = __import__("json").loads(checkpoint.read_text(encoding="utf-8"))
    assert stored["recipe_id"] == "sum"
    assert stored["recipe_payload"] == {
        "values": [4, 7, 11],
        "artifact_id": hashlib.sha256(b"[4,7,11]").hexdigest(),
        "result": None,
    }
    assert "artifact_id" not in stored
    assert not {"brief", "scope", "feedback", "recommendation"} & set(stored)

    delivered = service.resume(started.run_id)
    assert delivered.status == "succeeded"
    assert delivered.phase == "aggregate_ready"
    assert delivered.result == {"count": 3, "sum": 22}
    assert "recommendation" not in delivered.result

    restarted = RecipeCatalog((OracleRecipeService(Oracle(tmp_path)), SumRecipe(tmp_path)))
    assert restarted.get("sum").inspect(started.run_id) == delivered


def test_recipe_catalog_rejects_duplicate_ids_and_unknown_ids(tmp_path):
    service = SumRecipe(tmp_path)

    with pytest.raises(DuplicateRecipeError):
        RecipeCatalog((service, service))

    with pytest.raises(UnknownRecipeError):
        RecipeCatalog((service,)).get("missing")


@pytest.mark.parametrize("operation", ["inspect", "respond", "resume"])
def test_recipe_owner_mismatch_rejects_before_mutating_checkpoint(tmp_path, operation):
    service = SumRecipe(tmp_path)
    started = service.start({"values": [2, 3]})
    checkpoint = tmp_path / f"{started.run_id}.json"
    stored = __import__("json").loads(checkpoint.read_text(encoding="utf-8"))
    stored["recipe_id"] = "oracle"
    checkpoint.write_text(__import__("json").dumps(stored), encoding="utf-8")
    original = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        getattr(service, operation)(
            started.run_id, "answer"
        ) if operation == "respond" else getattr(service, operation)(started.run_id)

    assert checkpoint.read_bytes() == original


def test_cross_recipe_parent_is_rejected_before_creating_child(tmp_path):
    oracle = OracleRecipeService(Oracle(tmp_path))
    parent = oracle.start({"brief": "Synthetic GTM brief."})
    sum_recipe = SumRecipe(tmp_path)
    existing = {path.name: path.read_bytes() for path in tmp_path.glob("*.json")}
    values = [5, 8]

    with pytest.raises(CheckpointIncompatibleError):
        sum_recipe._runner.start(
            recipe_payload={
                "values": values,
                "artifact_id": hashlib.sha256(
                    __import__("json").dumps(values, separators=(",", ":")).encode()
                ).hexdigest(),
                "result": None,
            },
            adapter_payload={"cursor": 0},
            intent="sum",
            mode="replay",
            parent_id=parent.run_id,
            recipe_contract_version=1,
            phase="waiting_for_expert",
        )

    assert {path.name: path.read_bytes() for path in tmp_path.glob("*.json")} == existing


def test_non_oracle_recipe_rejects_ownerless_checkpoint_without_rewriting(tmp_path):
    run_id = "12345678-1234-5678-1234-567812345678"
    checkpoint = tmp_path / f"{run_id}.json"
    checkpoint.write_text('{"run_id":"' + run_id + '","checkpoint_version":1}', encoding="utf-8")
    original = checkpoint.read_bytes()

    with pytest.raises(CheckpointIncompatibleError):
        SumRecipe(tmp_path).inspect(run_id)

    assert checkpoint.read_bytes() == original


def test_runner_without_recipe_owner_cannot_write_unreadable_state(tmp_path):
    oracle = Oracle(tmp_path)

    with pytest.raises(TypeError):
        Runner(
            tmp_path,
            oracle._adapter_for_state,
            oracle._transition_for_event,
            oracle._validate_run,
        )

    assert list(tmp_path.glob("*.json")) == []


def test_transition_cannot_persist_cross_recipe_parent(tmp_path):
    oracle = OracleRecipeService(Oracle(tmp_path))
    parent = oracle.start({"brief": "Synthetic GTM brief."})
    sum_recipe = SumRecipe(tmp_path)
    started = sum_recipe.start({"values": [3, 5]})
    checkpoint = tmp_path / f"{started.run_id}.json"
    original = checkpoint.read_bytes()
    transition = sum_recipe._runner.transition_for_event

    def cross_recipe_transition(event, state):
        return {**transition(event, state), "parent_id": parent.run_id}

    sum_recipe._runner.transition_for_event = cross_recipe_transition

    with pytest.raises(CheckpointIncompatibleError):
        sum_recipe.resume(started.run_id)

    assert checkpoint.read_bytes() == original


def test_transition_cannot_retarget_a_saved_run_identity(tmp_path):
    oracle = OracleRecipeService(Oracle(tmp_path))
    parent = oracle.start({"brief": "Synthetic GTM brief."})
    sum_recipe = SumRecipe(tmp_path)
    started = sum_recipe.start({"values": [3, 5]})
    checkpoint = tmp_path / f"{started.run_id}.json"
    original = checkpoint.read_bytes()
    parent_path = tmp_path / f"{parent.run_id}.json"
    parent_original = parent_path.read_bytes()
    transition = sum_recipe._runner.transition_for_event

    def retarget_transition(event, state):
        return {**transition(event, state), "run_id": parent.run_id}

    sum_recipe._runner.transition_for_event = retarget_transition

    with pytest.raises(CheckpointIncompatibleError):
        sum_recipe.resume(started.run_id)

    assert checkpoint.read_bytes() == original
    assert parent_path.read_bytes() == parent_original


def test_recipe_composition_rejects_missing_or_duplicate_bindings(tmp_path):
    pytest.importorskip("mcp")
    from adapters.cookbook_mcp import create_server
    from tests.recipe_mcp_fixture import SumMCPBinding

    service = SumRecipe(tmp_path)
    catalog = RecipeCatalog((service,))
    binding = SumMCPBinding()

    with pytest.raises(RecipeBindingError, match="(?i)every recipe"):
        create_server(catalog=catalog, bindings=())

    with pytest.raises(RecipeBindingError, match="(?i)duplicate binding"):
        create_server(catalog=catalog, bindings=(binding, binding))

    class UnknownBinding:
        recipe_id = "unknown"
        tool_names = ("unknown_start",)

        def bind(self, server, recipe_service):
            raise AssertionError("unknown bindings must fail before registration")

    with pytest.raises(RecipeBindingError, match="Unknown recipe binding"):
        create_server(catalog=catalog, bindings=(UnknownBinding(),))


def test_cookbook_mcp_rejects_duplicate_tool_names(tmp_path):
    pytest.importorskip("mcp")
    from adapters.cookbook_mcp import CookbookMCP

    server = CookbookMCP("test")

    async def first_tool(value: str) -> str:
        return value

    async def second_tool(value: str) -> str:
        return value

    server.add_tool(first_tool, name="same_tool")
    with pytest.raises(RecipeBindingError, match="(?i)duplicate tool"):
        server.add_tool(second_tool, name="same_tool")


@pytest.mark.parametrize(
    "run_id",
    [None, "not-a-uuid", "../escape", "ABCDEFAB-CDEF-ABCD-EFAB-CDEFABCDEFAB"],
)
def test_runner_path_uses_typed_error_for_noncanonical_run_ids(tmp_path, run_id):
    runner = SumRecipe(tmp_path)._runner

    with pytest.raises(InvalidRunIDError) as error:
        runner.path(run_id)

    assert error.value.code == "invalid_run_id"
    assert str(error.value) == "Expected a canonical run identity"
    assert list(tmp_path.iterdir()) == []
