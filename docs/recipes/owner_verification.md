# Owner verification

**Check that a small-business owner is credible before going further.** A
general-population reviewer reads a redacted, consented summary of an
identity-and-intent call and returns a structured verdict. The calling agent
decides what to do with it.

The implemented example is a fictional landscaping business owner considering a
sale. Its replay is synthetic and scripted. It needs no account, network call or
expert spend. Live study creation and verdict retrieval are unavailable.

## Input and result

Pass `start` a nonempty `summary` (at most 8,000 characters) and
`consent_recorded: true`. The summary must already be redacted; text containing
an email address or phone number is rejected with `unredacted_input`, and a
missing consent flag is rejected with `consent_required`. Other identifiers,
such as names or addresses, remain the caller's responsibility.

Every snapshot returns `run_id`, `mode`, `replay_case`, `artifact_id`,
`summary`, `status`, `phase`, `result` and `error`. The exact summary text
determines `artifact_id`.

`result` carries `verdict` (`verified`, `concerns` or `fail`), `reasons`,
`red_flags`, short `answers` to *credible owner*, *business operating* and
*genuine intent*, `limitations` and `provenance: "synthetic"`. The
[verdict contract](../../recipes/owner_verification/verdict-contract.md)
defines each field.

## Run the default case

From a checkout installed with `uv sync --locked`, run:

```sh
uv run --no-sync terac-owner-verification start --consent-recorded --summary "Synthetic call: owner of a fictional 12-person landscaping business describes crews, equipment and a two-year succession plan."
```

Copy the returned `run_id` and replace `RUN_ID` below. State is stored in
`.tmp/owner_verification` by default.

```sh
uv run --no-sync terac-owner-verification inspect RUN_ID
uv run --no-sync terac-owner-verification resume RUN_ID
uv run --no-sync terac-owner-verification inspect RUN_ID
```

Expected sequence:

1. `start` saves a `working` run in phase `waiting_for_reviewer`.
2. `inspect` reads it without advancing.
3. `resume` releases the synthetic verdict: `succeeded`, phase
   `verdict_available`, `result.verdict` is `verified`.
4. Later `inspect` and `resume` calls return the same saved verdict.

Select `--replay-case concerns` or `--replay-case fail` for the other outcomes.
Over [local MCP](../mcp.md) the same actions are `owner_verification_start`,
`owner_verification_inspect` and `owner_verification_resume`.

## Effects and limitations

Replay makes no network call and has no provider effects. The packaged verdicts
are fixed per case and do not evaluate the supplied summary. There is no
clarification step.

A live version would publish a short task (for example about ten minutes, one
reviewer, manual approval before payment) whose task URL points at the caller's
own access-controlled review page, such as
`https://reviews.example.com/review?case=<case-id>&token=<token>`. That flow is
not implemented here. A verdict is advice; it does not authorize contact,
disclosure, payment, rejection or a transaction, and it is not identity proof.
