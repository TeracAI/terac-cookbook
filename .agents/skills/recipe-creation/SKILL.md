---
name: recipe-creation
description: Create or extend a Terac Cookbook recipe with recipe-owned models, explicit caller registration, offline replay, isolation tests and contributor evidence. Not ordinary use of an existing recipe or an executable-YAML workflow.
---

# Create a Cookbook recipe

Read [Contributing](../../../CONTRIBUTING.md), the architecture linked from the
[README](../../../README.md), and the relevant source/tests in this checkout.
Agree the caller contract and acceptance before runtime code. A proposal issue
or YAML form is data-only intake, not an executable recipe, plugin or approval.

## Define the recipe's job

Record the user problem, required input, useful output, artifact identity,
waiting/clarification/failure cases, permitted effects and explicit non-goals.
Use recipe-local models and validation under `recipes/<recipe-id>/`. Oracle is an
example, not a universal brief or verdict schema. Do not require a second recipe
to produce GTM findings or pass/fail recommendations unless its own job needs them.

Keep lifecycle/persistence in the shared runner and protocol encoding in adapters.
The recipe owns domain interpretation; provider progress alone is not its result.
If the existing interface cannot represent the new job, propose the smallest
shared change with concrete caller cases, not a second runtime or generic action
interpreter. Read-only inspection must never advance work.

## Make registration and fixtures explicit

Inspect how this revision composes recipes, entrypoints and tool definitions.
Register the new recipe at each intended surface: Python composition, CLI
entrypoint if offered, and MCP discovery plus named tools if offered. Explain
which source files change. Neither a recipe directory nor naming convention
creates a callable tool. Use an existing explicit binding/registration interface
where present; do not assume this baseline has a plugin registry or invent one.

Provide synthetic default replay and a fake provider at the supported seam.
Fixtures need version/provenance and exact input/result binding; recorded material
needs rights/privacy clearance. Document unavailable modes. Live requirements,
credentials and effect authorization remain separate from replay success.

## Prove the caller's workflow

Test through the same Python, CLI or real MCP interface callers use, with no live
credentials or provider effects. Cover valid delivery, waiting, repeated pure
inspect, clarification where supported, explicit resume, restart, malformed result,
wrong artifact and unsupported checkpoint versions. Reject bad input without
rewriting prior state.

Run the existing recipe and the new one together. Verify discovery/tools remain
correct, one recipe's run/parent cannot be consumed or overwritten by the other,
and domain validation/errors do not leak into shared transport. Exercise linked
work only where supported; do not invent universal lineage behavior. Preserve
existing caller behavior while proving the new recipe's distinct input/result.

## Finish the contribution

Add a concise recipe guide with a runnable offline example, setup, result meaning,
effects, limitations and acceptance evidence. Update the intended discovery/docs
links. Follow the checkout's contribution checks and review requirements rather
than duplicating an outdated command list here. If packaging changes, verify the
fixture and entrypoint from an installed wheel outside the checkout; source-only
success does not prove distribution.

The PR should identify changed registration points, models, fixtures/provenance,
caller-level test evidence, compatibility policy and remaining gates. Explain
failing/passing CI and actual required-check enforcement separately. Keep local
checkpoints, credentials and uncleared material out of the change. This skill
grants no Git, provider, recruitment/payment or publication authority.
