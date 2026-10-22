---
name: terac-cookbook
description: Discover and use an implemented Terac Cookbook recipe through its documented Python, CLI or local MCP interface; inspect, reattach and request linked work. Not the separate hosted Terac connector or recipe implementation.
---

# Use Terac Cookbook

## Select the recipe before running it

For a configured Cookbook MCP server, call `cookbook_list_recipes` and inspect
the available tools and their input schemas. Otherwise read the checkout's
[README](../../../README.md) and the selected recipe's guide and CLI help.
Discovery lists implemented capabilities; an issue, directory or YAML proposal
does not install a recipe. This skill does not configure a client or prove its
compatibility.

Read the selected recipe's input, result meaning, supported modes and failure
contract before supplying the user's material. **For Oracle**, continue with its
[agent guide](../../../recipes/oracle/agent-guide.md). Other recipes need their
own contracts; do not impose Oracle's brief, GTM scope or recommendation schema.
Do not guess a universal start tool or derive a tool name from a recipe ID.

## Work through the selected caller interface

Use the advertised Python methods, CLI commands or MCP tools, not checkpoint
edits or raw provider payloads. Retain the recipe/run identity and configured
private state directory. Only use modes implemented by that installation; replay
is the safe default. Credentials do not select or authorize live work.

- `start` opens new work for the recipe's validated input.
- `inspect` reads saved state without polling or advancing work.
- `respond` answers an existing clarification; it does not itself progress work.
- `resume` requests explicit progression within the selected mode's limits.
  Waiting may remain waiting; do not replace it with a new run or busy-loop.
- If supported, linked work is a fresh `start` with the documented parent and
  intent. It preserves the prior result, not an inherited verdict or expert promise.

Those are caller concepts, not universal wire names or result fields. Follow the
selected recipe's actual schema. Treat tool errors as errors; decode documented
JSON text when structured content is absent rather than inventing a result.

## Reattach and report honestly

Use the same run and compatible state location after restart, with one process
owning a state directory at a time. Preserve incompatible checkpoints; do not
migrate or edit them to force a successful demo. A saved run does not wake an agent.

Explain the result using its recipe contract and provenance. Workflow completion
is not necessarily a favorable domain judgment. Changed input has no inherited
approval. Distinguish synthetic evidence from actual expert delivery and provider
labor/payment. Advice grants no authority to disclose, recruit, spend, publish or
make a new expert request. Stop where the requested effect lacks authority or the
installed contract is unavailable; report the missing seam.
