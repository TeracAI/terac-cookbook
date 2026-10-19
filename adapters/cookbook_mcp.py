"""Local stdio transport for Cookbook's accepted Oracle caller interface."""

import argparse
import json
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

import anyio
from jsonschema import ValidationError, validate
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from recipes.oracle import (
    InvalidReplayCaseError,
    Oracle,
    ReplayCaseUnavailableError,
    Snapshot,
)
from runtime.runner import CheckpointIncompatibleError

_request_arguments: ContextVar[dict] = ContextVar("cookbook_request_arguments")


def tool_result(data: dict, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data))],
        structuredContent=data,
        isError=is_error,
    )


class CookbookMCP(FastMCP):
    """Reject extra/coerced inputs and keep SDK validation details out of tool errors."""

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
        return tools

    async def call_tool(self, name: str, arguments: dict):
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is None:
            return tool_result({"error": "unknown_tool"}, is_error=True)
        if name == "oracle_start" and "replay_case" in arguments:
            replay_case = arguments["replay_case"]
            if replay_case is not None and not isinstance(replay_case, str):
                return tool_result({"error": "invalid_replay_case"}, is_error=True)
        token = _request_arguments.set(arguments)
        try:
            validate(arguments, tool.inputSchema)
            sdk_arguments = arguments
            if name == "oracle_start" and isinstance(arguments.get("replay_case"), str):
                # FastMCP pre-parses JSON-looking strings before validating str fields.
                # Keep the selector in the request context and give its binder a safe value.
                sdk_arguments = {**arguments, "replay_case": None}
            return await super().call_tool(name, sdk_arguments)
        except (ValidationError, ToolError):
            return tool_result({"error": "invalid_request_or_checkpoint"}, is_error=True)
        finally:
            _request_arguments.reset(token)


def create_server(state_dir: Path) -> FastMCP:
    server = CookbookMCP("Terac Cookbook", log_level="ERROR")
    oracle = Oracle(state_dir)
    run_lock = anyio.Lock()

    async def invoke(
        operation: Callable[[], Snapshot], *, invalid_error: str = "invalid_request_or_checkpoint"
    ) -> CallToolResult:
        async with run_lock:
            try:
                return tool_result(operation().model_dump(mode="json"))
            except CheckpointIncompatibleError as exc:
                return tool_result({"error": exc.code}, is_error=True)
            except (InvalidReplayCaseError, ReplayCaseUnavailableError) as exc:
                return tool_result({"error": exc.code}, is_error=True)
            except ValueError:
                return tool_result({"error": invalid_error}, is_error=True)
            except OSError:
                return tool_result({"error": "checkpoint_unavailable"}, is_error=True)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def cookbook_list_recipes() -> dict:
        """List implemented recipes and modes without reading or advancing runs."""
        return {
            "recipes": [
                {
                    "id": "oracle",
                    "name": "Oracle",
                    "modes": ["replay"],
                    "live_available": False,
                    **oracle.replay_metadata,
                }
            ]
        }

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
    async def oracle_start(
        brief: str,
        mode: str = "replay",
        parent_id: str | None = None,
        intent: str = "review",
        replay_case: str | None = None,
    ) -> CallToolResult:
        """Start replay review or linked follow_up/new_query; live is unavailable."""
        # SDK v1 pre-parses optional strings, including the literal string "null".
        parent_id = _request_arguments.get().get("parent_id", parent_id)
        replay_case = _request_arguments.get().get("replay_case", replay_case)
        return await invoke(
            lambda: oracle.start(
                brief,
                mode=mode,
                parent_id=parent_id,
                intent=intent,
                replay_case=replay_case,
            ),
            invalid_error="live_unavailable" if mode == "live" else "invalid_request_or_checkpoint",
        )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def oracle_inspect(run_id: str) -> CallToolResult:
        """Read saved waiting, clarification, feedback or failure without advancing work."""
        return await invoke(lambda: oracle.inspect(run_id))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
    async def oracle_respond(run_id: str, answer: str) -> CallToolResult:
        """Record an answer to the pending clarification without progressing replay."""
        return await invoke(lambda: oracle.respond(run_id, answer))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
    async def oracle_resume(run_id: str) -> CallToolResult:
        """Explicitly release at most one replay event, or return paused/terminal state."""
        return await invoke(lambda: oracle.resume(run_id))

    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Cookbook local replay MCP over stdio")
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.state_dir.is_absolute():
        parser.error("--state-dir must be absolute")
    create_server(args.state_dir).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
