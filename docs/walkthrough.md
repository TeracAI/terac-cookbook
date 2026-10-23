# Cookbook walkthrough

A short, local-first demonstration of the current seams. The Oracle case and its
feedback are scripted and synthetic; no Terac account or expert spend is needed
for the main walkthrough.

## Run Oracle replay

Install the locked project dependencies, then start the default case:

```sh
uv sync --locked --extra connectors
uv run --no-sync terac-oracle start --brief "Synthetic repair-shop appointment tool: demand and pricing are unproven."
```

Copy the returned `run_id` into the commands below. The default case waits for an
explicit `resume`, requests one clarification, and then delivers scripted
feedback after `respond` and another `resume`:

```sh
uv run --no-sync terac-oracle inspect RUN_ID
uv run --no-sync terac-oracle resume RUN_ID
uv run --no-sync terac-oracle respond RUN_ID --answer "Independent repair shops"
uv run --no-sync terac-oracle resume RUN_ID
uv run --no-sync terac-oracle inspect RUN_ID
```

Inspection reads saved state without advancing it. The detailed
[Oracle guide](recipes/oracle.md) shows expected snapshots, all three outcomes,
linked work and restart behavior.

## Show the MCP caller

Use the installed `terac-cookbook-mcp` entrypoint with an absolute local state
directory. The [MCP guide](mcp.md) explains the stdio launch configuration,
discovery and the four replay actions. This is the inbound Cookbook MCP process;
it does not call Terac.

## Show the separate Terac diagnostics

The project also contains real, bounded read-only connectors: a REST organization
context reader and a Windows operator probe for REST context plus hosted MCP tool
and allowlisted context access. They report sanitized connection and shape facts.
They do not submit expert work or return a report to Oracle. See
[Terac access](terac-access.md) for the operator procedure and credential limits.

## What the walkthrough establishes

It demonstrates the packaged local workflow, explicit caller progression, saved
state, recipe-owned feedback and optional inbound MCP. An authorized operator
may separately demonstrate the existing Terac diagnostics. Neither the replay
nor those diagnostics establishes expert-report delivery or production hosting.
