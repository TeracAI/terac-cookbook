---
name: terac-cookbook
description: Discover and run implemented Terac Cookbook recipes through Python, CLI or local MCP; inspect and resume saved runs or request supported linked work. Use for Cookbook callers, not the hosted Terac connector or new recipe implementation.
---

# Use Terac Cookbook

[Terac](https://terac.com) provides on-demand human experts through a
dashboard, REST API and MCP. Cookbook supplies runnable examples of agents
working with expert feedback. Replay remains Oracle's default. A separate opt-in supports live feasibility,
draft preparation and launch after fresh confirmation. It does not deliver
expert feedback or retrieve reports; see Oracle's guide for the exact caller
contract.

**Inbound MCP** is your agent calling the local Cookbook stdio server.
**Outbound MCP/REST** is a call to Terac's provider interface. They have different
setup and authority; connecting the local server does not connect a Recipe Run
to expert fulfillment. See the [MCP guide](../../../docs/mcp.md) for process
configuration and the [architecture](../../../docs/architecture.md#transport-and-provider-boundaries)
for the boundary.

## Discover, then select a recipe

For a configured Cookbook MCP server, call `cookbook_list_recipes`, then inspect
the selected recipe's actual tools and input schemas. Otherwise use the
[recipe catalog](../../../docs/recipes/index.md),
[CLI quickstart](../../../README.md#run-your-first-recipe) and CLI help in this
checkout. Discovery describes implemented capabilities; neither a proposal nor
a directory installs a recipe. Do not guess a universal start tool or derive
tool names from a recipe ID.

**For Oracle, continue directly with its
[agent guide](../../../recipes/oracle/agent-guide.md).** Use its
[input/result contract](../../../docs/recipes/oracle.md#input-scope-and-result),
[Python example](../../../docs/recipes/oracle.md#use-the-same-interface-from-python)
or [MCP schemas and result formats](../../../docs/mcp.md#discover-and-call)
as needed. Oracle owns its GTM meaning; future recipes need their own input and
result contracts. The actual bindings are
[OracleRecipeService](../../../recipes/oracle/binding.py) and
[OracleMCPBinding](../../../recipes/oracle/mcp.py).

## Act on the saved run

Use the selected public interface and preserve the recipe ID, run ID and private
state directory. The common caller concepts below are not universal wire names
or result fields; follow the selected recipe's schemas.

- `start` validates input and opens fresh saved work.
- `inspect` reads saved state without polling or advancing it.
- `respond` records an answer to a pending clarification without progressing work.
- Explicit resume requests progression. Waiting may remain waiting; retain the
  run rather than starting a replacement or busy-looping.
- Supported linked work is a fresh `start` with the documented parent and intent;
  it preserves the earlier result without inheriting its verdict.

After process exit, reattach using the same run and compatible state location,
with one owning process at a time. Saved runs do not wake an agent automatically.
Use [Oracle's restart and update guidance](../../../docs/recipes/oracle.md#restart-and-updates)
for compatibility errors; preserve incompatible checkpoints without editing them.
Decode documented JSON text when MCP structured content is absent, and treat
tool errors as errors rather than fabricating a successful snapshot.

## Interpret and route the next step

Explain results using the recipe contract and provenance. Run completion is not
a favorable domain judgment; a changed artifact has no inherited recommendation.
Keep synthetic feedback, actual expert delivery and provider labor/payment
separate. Replay remains default even with credentials, and advice grants no
authority for disclosure, recruitment, spend, payment or publication.

- **Oracle revision or linked work:** follow
  [the next-decision guide](../../../docs/recipes/oracle.md#decide-what-comes-next).
- **Terac access or study work:** use
  [read-only diagnostics](../../../docs/terac-access.md) for connection facts and
  Oracle's [study creation and launch guide](../../../docs/recipes/oracle.md#explicit-study-creation-and-launch)
  for the separately opted-in live flow. Explicit resume performs bounded
  feasibility or draft steps; launch requires fresh confirmation. Expert-report
  retrieval and delivery remain unavailable. Do not present study creation or
  launch as expert feedback.
- **A new recipe or changed caller contract:** use
  [Contributing](../../../CONTRIBUTING.md) and the
  [recipe-creation skill](../recipe-creation/SKILL.md), which links the actual
  implementation and registration seams.

These are checkout instructions. MCP host setup, automatic skill installation
and named-client acceptance are not implied by package installation.
