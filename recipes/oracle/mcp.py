"""Oracle's MCP tool names, arguments and error meanings."""

from mcp.types import CallToolResult, ToolAnnotations

from adapters.terac_study_contract import StudyReadError
from recipes.catalog import RecipeService, RecipeToolBinding
from recipes.oracle import (
    InvalidReplayCaseError,
    LiveIntegrationUnavailableError,
    LiveLinkedWorkUnavailableError,
    ReplayCaseUnavailableError,
)
from recipes.oracle.binding import OracleRecipeService
from runtime.runner import CheckpointIncompatibleError


class OracleMCPBinding(RecipeToolBinding):
    recipe_id = "oracle"
    tool_names = ("oracle_start", "oracle_inspect", "oracle_respond", "oracle_resume")

    def bind(self, server: object, service: RecipeService) -> None:
        if not isinstance(service, OracleRecipeService):
            raise ValueError("Oracle tools require an Oracle recipe service")

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def oracle_start(
            brief: str,
            mode: str = "replay",
            parent_id: str | None = None,
            intent: str = "review",
            replay_case: str | None = None,
        ) -> CallToolResult:
            """Start replay or an explicitly configured read-only attachment."""
            return await server.invoke(
                lambda: service.start(
                    {
                        "brief": brief,
                        "mode": mode,
                        "parent_id": parent_id,
                        "intent": intent,
                        "replay_case": replay_case,
                    }
                ),
                invalid_error="invalid_request_or_checkpoint",
                error_for=_oracle_error_code,
            )

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
        async def oracle_inspect(run_id: str) -> CallToolResult:
            """Read a saved run without advancing it."""
            return await server.invoke(
                lambda: service.inspect(run_id), error_for=_oracle_error_code
            )

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def oracle_respond(run_id: str, answer: str) -> CallToolResult:
            """Record a clarification answer without progressing replay."""
            return await server.invoke(
                lambda: service.respond(run_id, answer), error_for=_oracle_error_code
            )

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
        async def oracle_resume(run_id: str) -> CallToolResult:
            """Release one local event or make an explicit read-only provider refresh."""
            return await server.invoke(lambda: service.resume(run_id), error_for=_oracle_error_code)

        server.set_invalid_input_error("oracle_start", _oracle_start_input_error)


def _oracle_error_code(error: Exception) -> str | None:
    if isinstance(
        error,
        (
            CheckpointIncompatibleError,
            InvalidReplayCaseError,
            LiveIntegrationUnavailableError,
            LiveLinkedWorkUnavailableError,
            ReplayCaseUnavailableError,
            StudyReadError,
        ),
    ):
        return error.code
    return None


def _oracle_start_input_error(arguments: dict) -> str | None:
    replay_case = arguments.get("replay_case")
    if "replay_case" in arguments and replay_case is not None and not isinstance(replay_case, str):
        return "invalid_replay_case"
    return None
