# Owner verification

A general-population reviewer reads a redacted, consented summary of an
identity-and-intent call with a small-business owner and returns a structured
verdict: `verified`, `concerns` or `fail`, with reasons and red flags.
Recipe identifier: `owner_verification`.

The implemented Python/CLI/MCP example is synthetic replay only, with three
selectable outcomes and local checkpoints. Live study creation and verdict
retrieval are unavailable in this revision.

- [Owner verification guide](../../docs/recipes/owner_verification.md): input, replay actions, result meaning, effects and limitations.
- [Verdict contract](verdict-contract.md): exact binding and verdict meaning.
- [Replay cases](replay.json): packaged synthetic fixture catalog.
