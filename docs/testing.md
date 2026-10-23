# Offline testing

Local checks exercise deterministic behavior with synthetic data. They do not
contact Terac, request expert work or incur provider effects. Passing local
checks and hosted CI does not establish live-provider or named-client behavior.

## Run the repository checks

Install the locked development and MCP test dependencies, then run the checks
used by the offline workflow:

```sh
uv sync --locked --extra connectors
uv run --no-sync python scripts/check_docs.py
uv run --no-sync python scripts/render_docs.py --check
uv run --no-sync python -m pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv build
uv run --no-sync python scripts/smoke_wheel.py
uv run --no-sync python scripts/smoke_wheel.py --with-mcp
git diff --check
```

If canonical Markdown changes, regenerate the offline HTML views with
`uv run --no-sync python scripts/render_docs.py` and then repeat the render
check. Markdown is the source; the generated pages provide local navigation and
work without external scripts or assets.

## Installed-wheel smoke

The smoke script runs from the checkout but installs the built wheel into a
temporary environment and exercises it from outside the source tree. Run it
without options for a core-only install; it uses locked runtime requirements,
verifies that MCP is absent, and covers the CLI replay outcomes and inspection.
Run it again with `--with-mcp` to install the connector extra, check the
packaged MCP module entrypoint, and use the SDK stdio client to discover tools,
restart a server, resume a synthetic run and inspect its saved state.

The `offline` CI job runs the full test, lint, documentation and build checks on
Python 3.11 and 3.12, then exercises both wheel installation modes. The
`portable-wheel` job builds and smokes both modes on Windows and macOS. The
separate `windows-credentials` job uses synthetic local fixtures. These checks
do not contact Terac or spend money, and wheel smoke does not establish support
for a particular MCP application client. The SDK stdio smoke starts the server
directly; it does not verify host-specific registration or named-client behavior.

For contribution review expectations, see [Contributing](../CONTRIBUTING.md)
and the [independent review contract](../REVIEW.md).
