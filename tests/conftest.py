"""Skip connector-dependent test modules when the `connectors` extra is absent.

`uv sync --locked` installs only the replay runtime. These modules import HTTPX,
AnyIO or MCP at module level, so collect them only when those packages exist.
"""

from importlib.util import find_spec

CONNECTOR_MODULES = ("anyio", "httpx", "mcp")
CONNECTOR_TESTS = (
    "recipe_mcp_fixture.py",
    "test_cookbook_app.py",
    "test_cookbook_mcp.py",
    "test_oracle_live.py",
    "test_oracle_study_creation.py",
    "test_terac_context.py",
    "test_terac_studies.py",
    "test_terac_study_read.py",
)

collect_ignore = (
    list(CONNECTOR_TESTS) if any(find_spec(name) is None for name in CONNECTOR_MODULES) else []
)
