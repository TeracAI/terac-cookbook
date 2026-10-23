"""Oracle's MCP tool names, arguments and error meanings."""

from mcp.types import CallToolResult, ToolAnnotations

from adapters.terac_study_contract import StudyReadError, StudyWriteError
from recipes.catalog import RecipeService, RecipeToolBinding
from recipes.oracle import (
    InvalidReplayCaseError,
    LiveIntegrationUnavailableError,
    LiveLinkedWorkUnavailableError,
    ReplayCaseUnavailableError,
    StudyCreationUnavailableError,
)
from recipes.oracle.binding import OracleRecipeService
from runtime.runner import CheckpointIncompatibleError


class OracleMCPBinding(RecipeToolBinding):
    recipe_id = "oracle"
    tool_names = (
        "oracle_start",
        "oracle_inspect",
        "oracle_respond",
        "oracle_resume",
        "oracle_launch",
    )

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
            study_request: dict[str, object] | None = None,
        ) -> CallToolResult:
            """Start replay, a bound read-only attachment, or an opt-in study request.

            A live study request is saved locally and makes no provider request until a later
            explicit resume.
            """
            return await server.invoke(
                lambda: service.start(
                    {
                        "brief": brief,
                        "mode": mode,
                        "parent_id": parent_id,
                        "intent": intent,
                        "replay_case": replay_case,
                        "study_request": study_request,
                    }
                ),
                invalid_error=(
                    "study_creation_unavailable"
                    if study_request is not None and not service.oracle.study_creation_available
                    else "live_unavailable"
                    if mode == "live" and not service.oracle.live_available
                    else "invalid_request_or_checkpoint"
                ),
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
            """Advance one replay event or provider step. On an explicitly enabled live study run,
            this may submit a feasibility request or create a draft; it never launches. A bound
            read-only run performs status and cost reads.
            """
            return await server.invoke(lambda: service.resume(run_id), error_for=_oracle_error_code)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
        async def oracle_launch(
            run_id: str,
            expected_total_cost_cents: int,
            maximum_total_cost_cents: int,
            scope_sha256: str,
            confirmation: str,
        ) -> CallToolResult:
            """Show the user the exact review summary: returned feasibility quote and dashboard
            link, draft link, audience, task, screening mode, USD total and cap. Launch only after
            explicit fresh confirmation. The fresh draft read is a pre-launch price check; Terac
            has no atomic price/cap condition, so actual pricing and publication remain under
            Terac's control."""
            return await server.invoke(
                lambda: service.launch(
                    run_id,
                    expected_total_cost_cents=expected_total_cost_cents,
                    maximum_total_cost_cents=maximum_total_cost_cents,
                    scope_sha256=scope_sha256,
                    confirmation=confirmation,
                ),
                error_for=_oracle_error_code,
            )

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
            StudyCreationUnavailableError,
            StudyReadError,
            StudyWriteError,
        ),
    ):
        return error.code
    return None


def _oracle_start_input_error(arguments: dict) -> str | None:
    replay_case = arguments.get("replay_case")
    if "replay_case" in arguments and replay_case is not None and not isinstance(replay_case, str):
        return "invalid_replay_case"
    if (
        "study_request" in arguments
        and arguments["study_request"] is not None
        and not isinstance(arguments["study_request"], dict)
    ):
        return "invalid_study_request"
    return None
