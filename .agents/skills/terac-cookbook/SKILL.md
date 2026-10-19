---
name: terac-cookbook
description: Run and interpret Oracle through Terac Cookbook's local Python, CLI or MCP replay interface. Use for Cookbook runs, inspection, restart and linked work; not for the separate hosted Terac connector.
---

# Use Terac Cookbook

Read the [Oracle guide](../../../docs/recipes/oracle.md); for local MCP, read its
[launch and result contract](../../../docs/mcp.md). Use an available, configured
caller interface. This skill does not install clients or establish compatibility.

Retain `run_id` and the configured state directory. `inspect` only reads;
`respond` answers a pending clarification without progression; `resume` releases
at most one event. The default `inconclusive` replay needs clarification before
delivery. Pass/fail cases deliver on their first resume. Content is scripted,
not a real evaluation of the user's brief.

After delivery, explain the recommendation and limitations before deciding how
to use them. A successful run can carry `fail` or `inconclusive` feedback. Revision
has no inherited verdict. Follow-up/new query uses fresh linked `start` and
preserves the parent; it does not establish same-expert continuity.

Keep replay as default. Reject unavailable live work; credentials or feedback
grant no effect authority. Use one process per plaintext state directory and
preserve incompatible checkpoints rather than migrating or editing them. For MCP,
discovery is JSON text only under SDK 1.30; Oracle actions also return structured
content. Named application-client acceptance is separate and remains unverified.
