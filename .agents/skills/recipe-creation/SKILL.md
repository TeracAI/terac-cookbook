---
name: recipe-creation
description: Create or extend a Terac Cookbook recipe with recipe-owned models, explicit Python/CLI/MCP registration and offline caller evidence. Use for recipe implementation and contribution, not running an existing recipe.
---

# Create a Cookbook recipe

Start with [Contributing](../../../CONTRIBUTING.md) and the
[architecture](../../../docs/architecture.md). Agree the input, useful output,
caller actions, waiting/failure cases and permitted effects before runtime code.
The [proposal form](../../../.github/ISSUE_TEMPLATE/recipe-proposal.yml) collects
that contract; YAML is intake, not an executable recipe or registration mechanism.

## Follow the implementation seams

Oracle is the worked example. Keep the new recipe's vocabulary and models local;
its job need not use a GTM brief, findings or pass/fail recommendations.

- **Recipe shape:** [Oracle's directory guide](../../../recipes/oracle/README.md)
  routes to its [input/result contract](../../../recipes/oracle/review-contract.md)
  and [runnable guide](../../../docs/recipes/oracle.md).
  [recipes/oracle/__init__.py](../../../recipes/oracle/__init__.py) owns domain
  models, validation, artifact binding, transitions and runner integration;
  [replay.json](../../../recipes/oracle/replay.json) supplies synthetic cases.
- **Shared lifecycle:** [runtime/runner.py](../../../runtime/runner.py) owns
  versioned run state, ownership, persistence and pure inspection.
  [adapters/replay.py](../../../adapters/replay.py) supplies replay events.
  Keep domain interpretation out of these shared components.
- **Python service and catalog:** [recipes/catalog.py](../../../recipes/catalog.py)
  defines `RecipeService`, `RecipeToolBinding` and `RecipeCatalog`.
  [OracleRecipeService](../../../recipes/oracle/binding.py) binds Oracle's domain
  API to that service and advertises its supported modes/cases.
- **MCP tools and composition:** [OracleMCPBinding](../../../recipes/oracle/mcp.py)
  defines explicit tool names, input schemas and recipe error mapping.
  [cookbook_app/__init__.py](../../../cookbook_app/__init__.py) supplies the
  services and bindings to [the MCP adapter](../../../adapters/cookbook_mcp.py).
  Register both the new service and its binding in the intended composition;
  the catalog does not dynamically load recipe directories.
- **CLI and packaging:** [Oracle's __main__.py](../../../recipes/oracle/__main__.py)
  parses commands and calls the same domain API.
  [pyproject.toml](../../../pyproject.toml) declares console scripts, dependencies
  and wheel packages. Add an entrypoint when a CLI is offered and verify that
  its fixtures are packaged.

The [architecture diagram](../../../docs/assets/cookbook-runner.svg) shows those
boundaries; the [Oracle workflow](../../../docs/assets/oracle-workflow.svg) shows
the caller's progression. A new directory alone never creates a callable tool.

## Demonstrate the new job

Implement under `recipes/<recipe-id>/` with recipe-owned input/output validation,
exact artifact/scope binding and a synthetic default replay. Document unavailable
modes. Recorded fixtures need rights/privacy clearance and versioned provenance;
local checkpoints are private saved state, not fixtures.

Use the existing caller tests as starting points:

- [test_recipe_catalog.py](../../../tests/test_recipe_catalog.py) includes a
  distinct integer-sum recipe, shared lifecycle/restart, duplicate registration,
  cross-recipe run/parent rejection and unchanged state on failure.
- [test_cookbook_mcp.py](../../../tests/test_cookbook_mcp.py) exercises two recipes
  over real stdio, discovery, typed arguments and recipe-local errors.
  [recipe_mcp_fixture.py](../../../tests/recipe_mcp_fixture.py) is its explicit
  test composition and second binding, not another shipped product recipe.
- [test_oracle.py](../../../tests/test_oracle.py) covers Python/CLI behavior and
  checkpoint compatibility; [test_cookbook_app.py](../../../tests/test_cookbook_app.py)
  checks the default application's catalog and tool composition.

Prove the new recipe and Oracle work together through their public interfaces,
without live credentials or provider effects. Cover delivery, waiting, repeated
read-only inspection, explicit resume, restart, invalid input/result and wrong
artifact/owner. Exercise clarification and linked work where the recipe supports
them; preserve incompatible saved state rather than rewriting it.

## Make the contribution reviewable

Add a canonical recipe guide with an offline invocation, result meaning, effects
and limitations, then update [recipe discovery docs](../../../docs/recipes/index.md).
For a rendered guide, register its Markdown/HTML pair in the manual `PAGES` map
in [scripts/render_docs.py](../../../scripts/render_docs.py) and generate the HTML.
Extend [scripts/smoke_wheel.py](../../../scripts/smoke_wheel.py) for new shipped
recipes or entrypoints: its installed-wheel checks cover CLI invocation, MCP tools
and catalog discovery outside the source checkout.
Follow [the contribution checks](../../../CONTRIBUTING.md#run-offline-checks)
and independent review path. When packaging changes, verify the installed wheel
outside the checkout. Report registration changes, fixture provenance, caller
evidence and compatibility limits in the PR. Replay success does not establish
live fulfillment or authority to disclose, recruit, spend, pay or publish.
