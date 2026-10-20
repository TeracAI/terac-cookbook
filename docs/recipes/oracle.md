# Oracle

**Turn a go-to-market brief into a clear next decision.** Oracle returns scoped
review feedback; the calling agent decides whether to explain it, revise the
brief, continue the task or request linked work.

The implemented example is a fictional appointment tool for independent repair
shops. Its replay is synthetic and scripted. It needs no account, network call
or expert spend. Live expert fulfillment is unavailable.

![Oracle's four actions move a saved run from waiting through optional clarification to feedback. The calling agent then chooses presentation, revision or fresh linked work.](../assets/oracle-workflow.svg)

## Input, scope and result

Pass a nonempty brief to `start`. The fixed review scope is **buyer and pain**,
**willingness to pay**, and **channel fit**. State which claims are supported,
which remain hypotheses and what evidence is available.

Every snapshot returns `run_id`, `parent_id`, `intent`, `artifact_id`, `brief`,
`scope`, `mode`, `status`, `phase`, `question`, `feedback` and
`error`. The exact brief text determines its artifact identity; each execution
has a distinct run identity. Replay adds `replay_case`; opt-in simulation adds
`simulation_ref` and `simulation_revision` instead.

Feedback carries the reviewed identity/scope, recommendation, rationale, material
findings, advisory comments, limitations, next steps and synthetic provenance.
The [feedback contract](../../recipes/oracle/review-contract.md) defines its
fields and interpretation.

## Run the default case

From a checkout installed with `uv sync --locked`, run:

```sh
uv run --no-sync terac-oracle start --brief "Synthetic repair-shop appointment tool: demand and pricing are unproven."
```

Copy the returned `run_id` and replace `RUN_ID` below. The CLI stores state in
`.tmp/oracle` by default. Keep that directory consistent across commands.

```sh
uv run --no-sync terac-oracle inspect RUN_ID
uv run --no-sync terac-oracle resume RUN_ID
uv run --no-sync terac-oracle inspect RUN_ID
uv run --no-sync terac-oracle respond RUN_ID --answer "Independent repair shops"
uv run --no-sync terac-oracle resume RUN_ID
uv run --no-sync terac-oracle inspect RUN_ID
```

Expected sequence:

1. `start` saves `working / waiting_for_expert`.
2. `inspect` returns that state unchanged.
3. First `resume` exposes `needs_input / awaiting_clarification` and a question.
4. `respond` records the answer and returns to waiting without advancing replay.
5. Next `resume` delivers `succeeded / feedback_available`, with an
   `inconclusive` recommendation.
6. `inspect` reads delivered feedback unchanged. Terminal `resume` also returns
   the saved result.

Replay does not simulate elapsed expert time. Its review does not adapt to the
brief or answer. An unanswered clarification keeps the run paused.

## Select another outcome

| `replay_case` | Schedule and feedback |
|---|---|
| `inconclusive` | Default; clarification, answer, then feedback describing insufficient evidence |
| `pass_with_comments` | First resume delivers `pass`, with advisory comments and no material findings |
| `fail` | First resume delivers a material finding: a claim of 20 paying pilot customers conflicts with 20 free trials and zero paid commitments |

Select the case when starting; the run retains it after restart:

```sh
uv run --no-sync terac-oracle start --brief "Synthetic channel hypothesis for independent repair shops." --replay-case pass_with_comments
uv run --no-sync terac-oracle start --brief "Synthetic claim: 20 paying pilot customers. Evidence: 20 free trials and zero paid commitments." --replay-case fail
```

Resume each returned run once to receive its scripted review. Omitting
`replay_case` in Python, or passing `None`, selects `inconclusive`.

## Use the same interface from Python

```python
from pathlib import Path

from recipes.oracle import Oracle

oracle = Oracle(Path(".tmp/oracle"))
started = oracle.start("Synthetic repair-shop appointment tool.")
waiting = oracle.inspect(started.run_id)
question = oracle.resume(started.run_id)
oracle.respond(started.run_id, "Independent repair shops")
delivered = oracle.resume(started.run_id)
print(delivered.feedback.recommendation)
```

Python and CLI use the same four actions. `inspect` reads without advancement;
`respond` saves an answer; `resume` releases at most one event.

## Call from a local MCP client

The optional [Cookbook MCP process](../mcp.md) exposes discovery plus
`oracle_start`, `oracle_inspect`, `oracle_respond` and `oracle_resume`. These call
the same Oracle interface and retain replay, exact-brief binding and explicit
resumption. Configure one local process with an absolute state-directory path.
Application-client acceptance remains unverified; follow the MCP guide's launch
and response contract rather than assuming a client-specific configuration.

## Decide what comes next

A `succeeded` run records delivery of valid feedback. A valid `fail` or
`inconclusive` review can complete delivery successfully. Neither completion nor
the recommendation determines provider labor approval or payment.

The calling agent may present the result, apply or decline advice, revise the
brief or continue without another Oracle request. Explain changes relative to
the reviewed text. A revised brief has a new identity and no inherited verdict.

To request fresh linked work, use `start` with the earlier `run_id` as parent
and an explicit intent:

```sh
uv run --no-sync terac-oracle start --brief "Synthetic follow-up: investigate the channel hypothesis." --parent-id RUN_ID --intent follow_up --replay-case pass_with_comments
```

Use `new_query` for a new linked question. Linked work creates its own run and
starts without feedback; it preserves the parent and can select its own case.
It is a use of `start`, not a fifth action. `respond` answers a currently pending
clarification; linked `start` opens new work. No real-expert continuity is implied.

## Restart and updates

Exit and later use the same run ID and state directory. To choose a different
state directory, place `--state-dir PATH` before the action on every invocation:

```sh
uv run --no-sync terac-oracle --state-dir .tmp/my-oracle inspect RUN_ID
```

Each replay checkpoint pins its case, checkpoint/recipe/fixture versions and semantic
fixture digest. Every linked ancestor uses its own saved pin. Unsupported,
legacy or changed pins return `checkpoint_incompatible` without rewriting saved
state. There is no automatic migration.

To update a clean checkout, select the reviewed revision deliberately, then run
`uv sync --locked`. Updating source and upgrading dependencies are different
operations. Preserve private checkpoints separately; do not assume an update can
resume incompatible state.

Local storage is plaintext JSON with atomic replacement, for one process at a
time. It is not a daemon or shared database. No background wakeups, deadlines,
cancellation, automatic retries or concurrent recovery are implemented.

## Errors and effects

Empty briefs or answers, a response without a pending question, invalid parent
intent and unknown runs are rejected. Unknown cases return `invalid_replay_case`;
a known case unavailable to an injected replay adapter returns
`replay_case_unavailable`. Invalid checkpoints return `checkpoint_incompatible`.
The CLI reports these runtime rejections as a JSON error and exits with status 2.

Schema validation and exact artifact/scope binding precede feedback delivery.
Malformed or mismatched replay events cause workflow failure. A material finding cannot
accompany `pass`.

Replay stays default regardless of credentials; `--mode live` is rejected as
`live_unavailable`. Disclosure, recruitment, spend, follow-up, payment, rejection
and publication require declared authority. Feedback cannot grant it. Do not
commit local checkpoints, private briefs or confidential responses.

## Opt in to local simulation

Simulation rehearses independently delivered observations through the same four
actions. It is synthetic, local and explicitly selected. Replay remains the
default even when simulation is configured or credentials exist. It establishes
no Terac transport, expert delivery or provider-schema compatibility.

Python configures `Oracle(state_dir, simulation_dir=mailbox_dir)`. CLI users
place the absolute mailbox path before the action, then select simulation on
start. Use the same two directories on subsequent commands:

```sh
terac-oracle --state-dir ABSOLUTE_PRIVATE_STATE --simulation-dir ABSOLUTE_PRIVATE_MAILBOX start --mode simulation --brief "Synthetic repair-shop channel hypothesis."
terac-oracle --state-dir ABSOLUTE_PRIVATE_STATE --simulation-dir ABSOLUTE_PRIVATE_MAILBOX inspect RUN_ID
terac-oracle --state-dir ABSOLUTE_PRIVATE_STATE --simulation-dir ABSOLUTE_PRIVATE_MAILBOX resume RUN_ID
```

Replace the absolute-path placeholders and `RUN_ID` with your local values.
Without mailbox configuration, simulation start returns
`simulation_unavailable` and creates no checkpoint. A simulation run cannot
select `replay_case`. Live remains unavailable in every configuration.

Start saves revision 0 and a `sim:<uuid>` reference. The operator or test harness
publishes observations out of band; normal callers only invoke the four actions.
Keep mailbox messages and checkpoints private and outside tracked files.
There is no operator publication MCP tool or automatic response generator.

The operator uses the UUID after `sim:` to form this path:

```text
<simulation-dir>/runs/<uuid>/observations/<revision:020d>.json
```

An observation is UTF-8 JSON, at most 65,536 bytes. Copy the exact run ID,
reference, artifact ID and scope from the snapshot. Choose the next positive
revision, starting at 1. For example, a waiting observation is:

```json
{
  "run_id": "COPY_RUN_ID",
  "reference": "sim:COPY_REFERENCE_UUID",
  "artifact_id": "COPY_ARTIFACT_SHA256",
  "scope": ["buyer_pain", "willingness_to_pay", "channel_fit"],
  "revision": 1,
  "kind": "waiting"
}
```

Publish by writing a temporary file in the observation directory, closing it,
then atomically moving it to the final zero-padded revision filename with a
no-overwrite operation. On Windows, `[System.IO.File]::Move($temporary, $final)`
uses this behavior. Create the directory first; both variables must name files
in that directory. An existing final filename must fail publication even when
the bytes are identical. Do not write directly to a final filename or replace
published observations.

For a clarification, set `kind` to `clarification` and add a nonempty
`question`. For delivery, set `kind` to `review` and add a `feedback` object
following the [feedback contract](../../recipes/oracle/review-contract.md), with
the exact artifact/scope and `provenance: "synthetic"`. Waiting has neither
payload field; clarification has no `feedback`; review has no `question`.
Omit unused fields entirely, including null-valued fields. Unknown fields,
duplicate JSON keys and nonfinite numbers are rejected.

Each explicit resume reads only the expected next revision. A missing file is
an unchanged snapshot and unchanged checkpoint; later files are not scanned.
An accepted waiting observation stays working; clarification pauses until
`respond` records an answer; validated review delivery succeeds. `respond`
records the answer locally without reading or writing the mailbox. The operator
independently chooses the next synthetic observation. Inspection, unanswered
clarification and terminal resume read no observation.

Malformed, oversized, incorrectly bound or out-of-sequence observations return
`simulation_observation_invalid` without changing checkpoint bytes. Errors omit
raw payloads and local paths. Correct unpublished input before publication;
if a malformed final file was published, preserve it as private evidence and
start a fresh run rather than overwriting history. Accepted files are not reread
or audited; append-only publication is a trusted operator responsibility.

Simulation checkpoint version 2 retains the last accepted revision, observation
digest and lifecycle facts, without replay fixture pins. Replay version 1 retains
its existing shape. Restart uses the same run ID, state directory and mailbox;
there is no implicit migration. Simulation feedback proves software behavior
with operator-authored synthetic content, not real expert judgment. Recorded
responses still require rights and privacy clearance before fixture publication.

[Architecture](../architecture.md) explains shared state and adapter boundaries.
[Contributing](../../CONTRIBUTING.md) describes offline testing and new Recipes.
