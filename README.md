# Terac Cookbook

Runnable examples of agents working with expert feedback. Start with **Oracle**:
review a go-to-market brief, inspect the result, and decide what to do next.

The offline baseline runs locally with synthetic data. It needs no Terac account,
API key or expert spend. Oracle is the first implemented recipe.

## Try Oracle

Requires Python 3.11 or later, Git and [uv](https://docs.astral.sh/uv/).
Repository access is required to clone this checkout.

```sh
git clone https://github.com/TeracAI/terac-cookbook.git
cd terac-cookbook
uv sync --locked
uv run --no-sync terac-oracle start --brief "Synthetic repair-shop appointment tool: demand and pricing are unproven."
```

Copy the returned `run_id`. Use the same checkout and state directory for the
following commands, replacing `RUN_ID` with that value:

```sh
uv run --no-sync terac-oracle inspect RUN_ID
uv run --no-sync terac-oracle resume RUN_ID
uv run --no-sync terac-oracle respond RUN_ID --answer "Independent repair shops"
uv run --no-sync terac-oracle resume RUN_ID
```

The default case pauses for a scripted clarification, then delivers
`inconclusive` feedback. Inspection reads saved state; resumption advances it.
The [Oracle guide](docs/recipes/oracle.md) also covers pass, fail and linked work.
Its [opt-in simulation walkthrough](docs/recipes/oracle.md#opt-in-to-local-simulation)
rehearses independent synthetic delivery through a private local operator mailbox.

On Windows, the complete synthetic walkthrough is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo_oracle.ps1
```

## Explore and build

- [Documentation](docs/index.md) — choose a starting point.
- [Architecture](docs/architecture.md) — recipe, runner, replay and state.
- [Recipes](docs/recipes/index.md) — the implemented catalog.
- [Local MCP](docs/mcp.md) — expose the same Oracle actions to a local agent client.
- [Contributing](CONTRIBUTING.md) — one path from proposal to tested pull request.

This checkout provides Python, CLI and optional local-stdio MCP replay. Install
MCP with `uv sync --locked --extra connectors`; the [MCP guide](docs/mcp.md)
describes process launch and client acceptance limits. Live expert fulfillment
is unavailable. Checkpoints are local plaintext JSON for one process at a time;
keep private run data out of Git. A delivered review, its recommendation, a
revised brief and provider payment are separate facts.

Install and update from a reviewed Git revision using `uv sync --locked`.
These instructions do not require a published package. See the
[update and recovery guidance](docs/recipes/oracle.md#restart-and-updates)
before moving an existing run to another revision.

## Optional read-only Terac diagnostics

The pre-release connector increment adds Windows hidden-key setup and a bounded
Python organization-context reader. These are explicit authenticated diagnostics;
they do not submit expert work or enable Oracle live mode. Follow the
[Terac access guide](docs/terac-access.md) for storage, calls, limits and tests.
