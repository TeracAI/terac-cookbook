"""Default application composition for the Terac Cookbook package."""

import argparse
from pathlib import Path

from adapters.cookbook_mcp import CookbookMCP, create_server
from recipes.catalog import RecipeCatalog
from recipes.oracle import Oracle
from recipes.oracle.binding import OracleRecipeService
from recipes.oracle.mcp import OracleMCPBinding


def create_default_server(state_dir: Path) -> CookbookMCP:
    """Compose the first product recipe outside the generic MCP adapter."""
    service = OracleRecipeService(Oracle(state_dir))
    return create_server(
        catalog=RecipeCatalog((service,)),
        bindings=(OracleMCPBinding(),),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cookbook local MCP over stdio")
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.state_dir.is_absolute():
        parser.error("--state-dir must be absolute")
    create_default_server(args.state_dir).run(transport="stdio")
    return 0
