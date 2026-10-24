"""Owner verification's MCP tool names, arguments and error meanings."""

from mcp.types import CallToolResult, ToolAnnotations

from recipes.catalog import RecipeService, RecipeToolBinding
from recipes.owner_verification import OwnerVerificationError
from recipes.owner_verification.binding import OwnerVerificationRecipeService
from runtime.runner import CheckpointIncompatibleError


class OwnerVerificationMCPBinding(RecipeToolBinding):
    recipe_id = "owner_verification"
    tool_names = (
        "owner_verification_start",
        "owner_verification_inspect",
        "owner_verification_resume",
    )

    def bind(self, server: object, service: RecipeService) -> None:
        if not isinstance(service, OwnerVerificationRecipeService):
            raise ValueError("Owner verification tools require its recipe service")

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def owner_verification_start(
            summary: str,
            consent_recorded: bool,
            mode: str = "replay",
            replay_case: str | None = None,
        ) -> CallToolResult:
            """Start a synthetic replay review of a redacted, consented owner call summary."""
            return await server.invoke(
                lambda: service.start(
                    {
                        "summary": summary,
                        "consent_recorded": consent_recorded,
                        "mode": mode,
                        "replay_case": replay_case,
                    }
                ),
                error_for=_error_code,
            )

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
        async def owner_verification_inspect(run_id: str) -> CallToolResult:
            """Read a saved run without advancing it."""
            return await server.invoke(lambda: service.inspect(run_id), error_for=_error_code)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def owner_verification_resume(run_id: str) -> CallToolResult:
            """Release the synthetic reviewer verdict for a waiting run."""
            return await server.invoke(lambda: service.resume(run_id), error_for=_error_code)


def _error_code(error: Exception) -> str | None:
    if isinstance(error, (OwnerVerificationError, CheckpointIncompatibleError)):
        return error.code
    return None
