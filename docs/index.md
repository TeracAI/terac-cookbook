# Terac Cookbook

**Build a workflow you can inspect.** Terac Cookbook shows how an agent can request
expert feedback, wait for delivery, and decide how to use the result.

Start with **Oracle**, a review of a go-to-market brief. Run its synthetic replay
locally to understand the complete workflow without an account or expert spend.

## Choose a starting point

- **Run the example:** [Oracle](recipes/oracle.md) walks through waiting,
  clarification, feedback and linked work.
- **Understand the design:** [Architecture](architecture.md) explains how recipes,
  the runner, replay and checkpoints fit together.
- **Browse the catalog:** [Recipes](recipes/index.md) lists implemented examples
  and their available modes.
- **Add or improve an example:** [Contributing](../CONTRIBUTING.md) takes one
  proposal through source, offline checks and review.

## What this baseline delivers

Oracle has four caller actions: `start`, `inspect`, `respond` and `resume`.
Three synthetic cases demonstrate pass with comments, fail and inconclusive
feedback. Saved local state supports inspection and deliberate resumption after
a caller exits. Linked work starts a fresh run while preserving earlier feedback.

The agent owns the next decision: explain the result, revise the brief, continue
the task or request more scoped work. Delivery does not require an automatic
rewrite. A new brief has no inherited verdict.

## Read the boundaries

This baseline provides Python and CLI replay; live expert fulfillment is
unavailable. Replay timing and content are scripted. They demonstrate software
behavior, not real expert judgment or market validation.

Checkpoints are plaintext JSON for one process at a time. There are no background
wakeups, deadline handling or concurrent-writer guarantees. Keep caller data
private and preserve the exact state directory when restarting.

Markdown is canonical. The matching HTML pages and local SVG diagrams are
generated for offline reading; they use no external assets or browser scripts.

## Check Terac access separately

[Read-only diagnostics](terac-access.md) cover explicit context reads and Windows
key setup. They remain separate from the replay runner and live expert work.
