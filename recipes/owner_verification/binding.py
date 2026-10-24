"""Python caller binding for owner verification's shared recipe lifecycle."""

from typing import Any

from recipes.owner_verification import RECIPE_ID, OwnerVerification, Snapshot


class OwnerVerificationRecipeService:
    recipe_id = RECIPE_ID
    name = "Owner verification"

    def __init__(self, recipe: OwnerVerification):
        self.recipe = recipe

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "id": self.recipe_id,
            "name": self.name,
            "modes": ["replay"],
            "live_available": False,
            **self.recipe.replay_metadata,
        }

    def start(self, request: object) -> Snapshot:
        if not isinstance(request, dict) or "summary" not in request:
            raise ValueError("Owner verification start requires a summary")
        if set(request) - {"summary", "consent_recorded", "mode", "replay_case"}:
            raise ValueError("Owner verification start contains unsupported fields")
        return self.recipe.start(
            request["summary"],
            consent_recorded=request.get("consent_recorded", False),
            mode=request.get("mode", "replay"),
            replay_case=request.get("replay_case"),
        )

    def inspect(self, run_id: str) -> Snapshot:
        return self.recipe.inspect(run_id)

    def respond(self, run_id: str, response: object) -> Snapshot:
        return self.recipe.respond(run_id, response)

    def resume(self, run_id: str) -> Snapshot:
        return self.recipe.resume(run_id)
