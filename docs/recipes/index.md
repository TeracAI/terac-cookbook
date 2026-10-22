# Recipes

A Recipe is a runnable agent–expert workflow with understandable input, output,
permitted effects and replay data. The catalog currently contains one implemented
Recipe.

## Oracle

**Review a go-to-market brief.** Oracle returns scoped feedback on buyer and pain,
willingness to pay, and channel fit. The calling agent decides how to use it.

- **Identifier:** `oracle`
- **Available mode:** synthetic offline replay
- **Callers:** Python, `terac-oracle`, and optional [local MCP](../mcp.md)
- **Input:** a concise brief with its claims and evidence
- **Output:** exact-brief feedback, including a recommendation and its limitations
- **Cases:** `pass_with_comments`, `fail`, and default `inconclusive`

[Run Oracle](oracle.md) · [Read its feedback contract](../../recipes/oracle/review-contract.md)

The three cases demonstrate workflow behavior. They do not evaluate arbitrary
briefs, adapt to clarification answers or establish live expert judgment.

## Add a useful example

Start with a developer's concrete input and desired output. Define the caller
contract, waiting behavior, permitted effects and failures. Provide synthetic
replay before optional live integration.

An accepted proposal becomes a small Python implementation, focused tests,
documented entrypoints and clear provenance. New Recipes require explicit source
registration; adding YAML does not create executable behavior. Follow the
[contribution flow](../../CONTRIBUTING.md).
