# Agent skills and package contents

Skills guide an agent. Recipes implement workflows. The Python package supplies
runtime and caller commands. These are different parts of a checkout, not
interchangeable installation formats.

## Choose a skill

- [terac-cookbook](../.agents/skills/terac-cookbook/SKILL.md): discover and run an
  implemented recipe through its supported Python, CLI or local MCP interface;
  understand results, inspect saved state and reattach.
- [recipe-creation](../.agents/skills/recipe-creation/SKILL.md): implement a recipe's
  models, explicit registrations, fixtures, packaging and caller-level checks.
  Proposal YAML is data intake, not an executable recipe or automatic registration.
- [study-operations](../.agents/skills/study-operations/SKILL.md): plan a study or
  explain observed recruitment/screening/review progress and propose a next step.
  See its [operating guide](study-operations.md).

For example, use terac-cookbook for Oracle replay, study-operations to understand
an existing opportunity, and recipe-creation to contribute a different recipe.
Skills supply neither missing provider endpoints nor permission to disclose,
recruit, spend, publish, reject work or pay experts.

## Runtime installation and instruction discovery

From a reviewed checkout, `uv sync --locked` prepares the core runtime and default
development dependencies. Add `--extra connectors` for optional local stdio MCP
and its SDK tests; see [setup](setup.md). This does not register an MCP server
with an agent host automatically.

The Python package defines `terac-oracle` and `terac-cookbook-mcp`, dependencies
and recipe fixtures in [pyproject.toml](../pyproject.toml). Its current wheel and
source distribution do not contain `.agents/skills`. The wheel excludes the
repository docs; the source distribution includes `docs/`. Installing a wheel
therefore does not install these skill instructions or their linked guides. No
package-registry publication is claimed here.

Use a Git checkout to supply `.agents/skills/<name>/SKILL.md` and linked docs to
the agent. Discovery/import is host-specific: use supported host configuration
or explicitly ask the agent to read the selected file if it does not discover
repository skills. Do not assume every agent uses Codex discovery or promise
automatic skill availability to a wheel-only client.

Native MCP tool calls also require host registration of the documented stdio
command and private state directory. A manually started server expects MCP
protocol input, not ordinary human commands. For terminal demonstrations, use
the recipe CLI. See the [local MCP guide](mcp.md) and
[native host registration guidance](setup.md#register-with-a-native-host).

## Contribute a change

Follow [contributing](../CONTRIBUTING.md) and the selected skill's concrete
source/test links. A recipe directory or skill alone creates no callable tool.
Registration, CLI/MCP behavior and packaged fixtures need checks and review.

Keep credentials, local checkpoints and uncleared expert material outside Git.
Use the contribution guide for offline checks and independent review.
