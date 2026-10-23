"""Default application composition for the Terac Cookbook package."""

import argparse
import os
from pathlib import Path

from adapters.cookbook_mcp import CookbookMCP, create_server
from adapters.terac_study_contract import StudyReadError
from recipes.catalog import RecipeCatalog
from recipes.oracle import Oracle
from recipes.oracle.binding import OracleRecipeService
from recipes.oracle.live_contract import OracleLiveBinding, load_oracle_live_binding
from recipes.oracle.mcp import OracleMCPBinding


def create_default_server(
    state_dir: Path,
    *,
    live_binding: OracleLiveBinding | None = None,
    study_adapter=None,
    credential_provider=None,
    live_transport_factory=None,
) -> CookbookMCP:
    """Compose the first product recipe outside the generic MCP adapter."""
    service = OracleRecipeService(
        Oracle(
            state_dir,
            live_binding=live_binding,
            study_adapter=study_adapter,
            credential_provider=credential_provider,
            live_transport_factory=live_transport_factory,
        )
    )
    return create_server(
        catalog=RecipeCatalog((service,)),
        bindings=(OracleMCPBinding(),),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cookbook local MCP over stdio")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--oracle-live-binding",
        type=Path,
        help="absolute path to one reviewed, read-only Terac study binding",
    )
    parser.add_argument(
        "--terac-study-creation",
        action="store_true",
        help="enable Oracle's explicit Terac feasibility, draft, and launch calls",
    )
    args = parser.parse_args()
    if not args.state_dir.is_absolute():
        parser.error("--state-dir must be absolute")
    if args.oracle_live_binding is not None and not args.oracle_live_binding.is_absolute():
        parser.error("--oracle-live-binding must be absolute")
    try:
        live_binding = (
            load_oracle_live_binding(args.oracle_live_binding)
            if args.oracle_live_binding is not None
            else None
        )
    except StudyReadError as exc:
        parser.error(exc.code)
    study_adapter = None
    if args.terac_study_creation:
        from adapters.terac_studies import TeracStudies

        study_adapter = TeracStudies(lambda: os.environ.get("TERAC_API_KEY"))
    create_default_server(
        args.state_dir,
        live_binding=live_binding,
        study_adapter=study_adapter,
    ).run(transport="stdio")
    return 0
