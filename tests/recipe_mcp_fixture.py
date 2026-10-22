"""Test-only stdio composition with a second recipe service."""

import argparse
from pathlib import Path

from mcp.types import CallToolResult, ToolAnnotations

from adapters.cookbook_mcp import CookbookMCP, create_server
from recipes.catalog import RecipeCatalog, RecipeService, RecipeToolBinding
from recipes.oracle import Oracle
from recipes.oracle.binding import OracleRecipeService
from recipes.oracle.mcp import OracleMCPBinding
from tests.test_recipe_catalog import SumInputError, SumRecipe


class SumMCPBinding(RecipeToolBinding):
    recipe_id = "sum"
    tool_names = (
        "sum_start",
        "sum_inspect",
        "sum_respond",
        "sum_resume",
        "sum_echo",
    )

    def bind(self, server: CookbookMCP, service: RecipeService) -> None:
        if not isinstance(service, SumRecipe):
            raise ValueError("Sum tools require a sum recipe service")

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def sum_start(values: list[int]) -> CallToolResult:
            """Start the test-only integer sum recipe."""
            return await server.invoke(
                lambda: service.start({"values": values}),
                error_for=_sum_error_code,
            )

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
        async def sum_inspect(run_id: str) -> CallToolResult:
            """Inspect a saved test-only integer sum run."""
            return await server.invoke(lambda: service.inspect(run_id), error_for=_sum_error_code)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def sum_respond(run_id: str, response: str) -> CallToolResult:
            """Forward the response operation to the test-only recipe."""
            return await server.invoke(
                lambda: service.respond(run_id, response),
                error_for=_sum_error_code,
            )

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
        async def sum_resume(run_id: str) -> CallToolResult:
            """Resume a saved test-only integer sum run."""
            return await server.invoke(lambda: service.resume(run_id), error_for=_sum_error_code)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
        async def sum_echo(value: str | None = None) -> CallToolResult:
            """Echo an optional string to exercise raw JSON preservation."""
            return await server.invoke(lambda: {"value": value})


def _sum_error_code(error: Exception) -> str | None:
    return error.code if isinstance(error, SumInputError) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    state_dir = parser.parse_args().state_dir
    catalog = RecipeCatalog(
        (
            OracleRecipeService(Oracle(state_dir)),
            SumRecipe(state_dir),
        )
    )
    server = create_server(
        catalog=catalog,
        bindings=(OracleMCPBinding(), SumMCPBinding()),
    )
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
