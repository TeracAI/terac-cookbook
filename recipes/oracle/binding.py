"""Python caller binding for Oracle's shared recipe lifecycle."""

from typing import Any

from recipes.oracle import Oracle, Snapshot


class OracleRecipeService:
    recipe_id = "oracle"
    name = "Oracle"

    def __init__(self, oracle: Oracle):
        self.oracle = oracle

    def catalog_entry(self) -> dict[str, Any]:
        modes = ["replay"]
        if self.oracle.live_available:
            modes.append("live")
        return {
            "id": self.recipe_id,
            "name": self.name,
            "modes": modes,
            "live_available": self.oracle.live_available,
            "study_creation_available": self.oracle.study_creation_available,
            **(
                {
                    "live_limit": (
                        "Oracle-only study creation and separately confirmed launch; "
                        "no report retrieval or expert feedback delivery."
                    )
                    if self.oracle.study_creation_available
                    else "Read-only status and cost observations for one reviewed study; "
                    "no launch or expert feedback."
                }
                if self.oracle.live_available
                else {}
            ),
            **self.oracle.replay_metadata,
        }

    def start(self, request: object) -> Snapshot:
        if not isinstance(request, dict) or "brief" not in request:
            raise ValueError("Oracle start requires a brief")
        allowed = {"brief", "mode", "parent_id", "intent", "replay_case", "study_request"}
        if set(request) - allowed:
            raise ValueError("Oracle start contains unsupported fields")
        return self.oracle.start(
            request["brief"],
            mode=request.get("mode", "replay"),
            parent_id=request.get("parent_id"),
            intent=request.get("intent", "review"),
            replay_case=request.get("replay_case"),
            study_request=request.get("study_request"),
        )

    def inspect(self, run_id: str) -> Snapshot:
        return self.oracle.inspect(run_id)

    def respond(self, run_id: str, response: object) -> Snapshot:
        return self.oracle.respond(run_id, response)

    def resume(self, run_id: str) -> Snapshot:
        return self.oracle.resume(run_id)

    def launch(
        self,
        run_id: str,
        *,
        expected_total_cost_cents: int,
        maximum_total_cost_cents: int,
        scope_sha256: str,
        confirmation: str,
    ) -> Snapshot:
        return self.oracle.launch(
            run_id,
            expected_total_cost_cents=expected_total_cost_cents,
            maximum_total_cost_cents=maximum_total_cost_cents,
            scope_sha256=scope_sha256,
            confirmation=confirmation,
        )
