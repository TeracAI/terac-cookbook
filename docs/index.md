# Terac Cookbook

**Build a workflow you can inspect.** Terac Cookbook shows how an agent can request
expert feedback, wait for delivery, and decide how to use the result.

Start with **Oracle**, a review of a go-to-market brief. Run its synthetic replay
locally to understand the complete workflow without an account or expert spend.

## Choose a starting point

- **Set up a checkout:** [Local setup](setup.md) covers the locked environment,
  optional connectors and checkpoint handling.
- **Run the checks:** [Offline testing](testing.md) explains the local suite and
  installed-wheel smoke.
- **Run the walkthrough:** [Cookbook walkthrough](walkthrough.md) demonstrates the
  local replay workflow and the available read-only Terac diagnostics.
- **Run the example:** [Oracle](recipes/oracle.md) walks through waiting,
  clarification, feedback and linked work.
- **Create a study:** [Oracle's opt-in study flow](recipes/oracle.md#explicit-study-creation-and-launch)
  covers feasibility, draft preparation and separately confirmed launch; report
  delivery is not included.
- **Understand the design:** [Architecture](architecture.md) explains how recipes,
  the Runner, replay and checkpoints fit together, with the current delivery scope.
- **Browse the catalog:** [Recipes](recipes/index.md) lists implemented examples
  and their available modes.
- **Connect a local agent:** [Local MCP](mcp.md) exposes discovery and the same
  four replay actions and the separate launch tool through stdio.
- **Add or improve an example:** [Contributing](../CONTRIBUTING.md) takes one
  proposal through source, offline checks and review.

## What this baseline delivers

Oracle's four replay workflow actions are start, inspect, respond and resume.
A separate opt-in flow supports feasibility, draft preparation and confirmed
study launch; it does not deliver expert reports. The MCP catalog also offers
recipe discovery. Three synthetic cases demonstrate pass with comments, fail
and inconclusive feedback. Saved local state supports inspection and deliberate
resumption after a caller exits. Linked work uses another start while preserving
earlier feedback.

The agent owns the next decision: explain the result, revise the brief, continue
the task or request more scoped work. Delivery does not require an automatic
rewrite. A new brief has no inherited verdict.

## Read the boundaries

Replay is the default for Python, CLI and local stdio MCP. Explicit opt-in Oracle
study creation supports feasibility, draft preparation and separate confirmed
launch. Expert-report retrieval and feedback delivery remain unavailable. Replay
timing and content are scripted; they demonstrate software behavior, not real
expert judgment or market validation.

MCP application-client acceptance is separate from SDK stdio checks. Codex,
Claude Code and other clients must be verified individually before advertising
support. Remote hosting is outside this local process contract.

Checkpoints are plaintext JSON for one process at a time. There are no background
wakeups, deadline handling or concurrent-writer guarantees. Keep caller data
private and preserve the exact state directory when restarting.

Markdown is canonical. The matching HTML pages and local SVG diagrams are
generated for offline reading; they use no external assets or browser scripts.

## Check Terac access separately

[Read-only Terac diagnostics](terac-access.md) expose sanitized context facts.
Oracle's separate opt-in study flow can create and launch work, but it does not
retrieve expert reports. See the
[Oracle study guide](recipes/oracle.md#explicit-study-creation-and-launch).
