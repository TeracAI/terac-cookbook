---
name: recipe-creation
description: Create or extend a Terac Cookbook Recipe with an agreed caller contract, synthetic replay, tests and documentation. Use for Cookbook recipe implementation, not unrelated agent workflows.
---

# Create a Cookbook Recipe

Read [Contributing](../../../CONTRIBUTING.md) and the
[architecture](../../../docs/architecture.md). Agree the input/output contract,
observable waiting and failure cases, permitted effects and acceptance before
runtime code. Oracle is the existing example, not a generic schema for every job.

Keep domain validation in `recipes/<recipe-id>/`, lifecycle in `runtime/` and
transport in `adapters/`. Python defines execution; proposal YAML is intake.
Adding a recipe needs explicit entrypoint/tool/discovery registration where used.

Provide a default synthetic replay and test the public caller workflow with fake
providers. Explain fixture provenance and unavailable live behavior. Preserve
read-only inspection and distinguish delivery from recommendation, caller revision
and provider payment. Do not infer effect authority from feedback or credentials.

Follow the canonical contribution checks and review procedure. Change canonical
Markdown, regenerate its HTML, and keep private runs and uncleared material out
of the contribution. This skill grants no Git, provider or publication authority.
