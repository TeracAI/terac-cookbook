# Local MCP

Expose Oracle to a local agent through Model Context Protocol (MCP). The packaged
`cookbook_app` uses standard input/output (stdio) and explicitly registers Oracle
with the recipe catalog. It calls the same Recipe interface as Python and CLI
callers. Replay is the default. A reviewed binding enables read-only observation
of one exact study; a separate `--terac-study-creation` opt-in enables Oracle's
feasibility and draft workflow. Live expert feedback delivery is unavailable.

This is an **inbound Cookbook adapter**: a client invokes Cookbook. It is separate
from outbound Terac MCP/REST and Terac's hosted provider connector. Without a
reviewed binding or study-creation opt-in, it performs no provider requests and
reads no provider credentials. An explicit live `oracle_resume` may read bound
study status/cost or, with study creation enabled, submit one feasibility or draft
step. It never launches. `oracle_launch` is a separate effectful tool that needs
fresh explicit confirmation of the current review summary.

## Install and launch

From the selected reviewed checkout:

```sh
uv sync --locked --extra connectors
```

Configure a local MCP host to launch this process. Use the installed environment's
absolute Python path so launch does not depend on a shell's current directory.
The example enables the optional read-only attachment; for replay-only use, omit
the binding flag and its path. To enable the separate study-creation adapter, add
`--terac-study-creation`; this configures the capability but does not make a
provider request at startup:

```text
command: ABSOLUTE_PATH_TO_CHECKOUT/.venv/bin/python
args:
  - -m
  - cookbook_app
  - --state-dir
  - ABSOLUTE_PATH_TO_PRIVATE_STATE_DIRECTORY
  - --oracle-live-binding
  - ABSOLUTE_PATH_TO_REVIEWED_BINDING_JSON
```

On Windows the environment interpreter is `.venv\Scripts\python.exe`.
Replace all three placeholders with actual absolute paths. This is a generic
command/argument specification; translate it into your host's supported format.
It is not a claim of accepted configuration for any named application.

The installed console entrypoint is also available:

```sh
terac-cookbook-mcp --state-dir ABSOLUTE_PATH_TO_PRIVATE_STATE_DIRECTORY
```

The state directory is required and must be absolute; relative paths are rejected
at startup. Supply `--oracle-live-binding` only for read-only attachment to an
existing study; its path must also be absolute. `--terac-study-creation` is a
separate opt-in. A live `oracle_start` with `study_request` saves a local
checkpoint; a later explicit `oracle_resume` performs at most one feasibility or
draft provider step. Provide `TERAC_API_KEY` through a trusted local secret
mechanism. The key is read only when an explicit provider step needs it. Tools
cannot choose a state path or pass credentials. Standard output carries MCP
protocol messages; the process adds no HTTP server, tunnel or remote hosting.

## Discover and call

| Tool | Inputs and behavior |
|---|---|
| `cookbook_list_recipes` | No arguments; reports Oracle modes, binding availability, default replay case and packaged case descriptors |
| `oracle_start` | `brief`; optional `mode`, `parent_id`, `intent`, `replay_case`, `study_request`; saves a run or local live study request without a provider call |
| `oracle_inspect` | `run_id`; reads saved state without advancing |
| `oracle_respond` | `run_id`, `answer`; records a nonempty answer to a pending clarification |
| `oracle_resume` | `run_id`; releases at most one replay event or performs one explicit live provider step; with study creation enabled, may request feasibility or create a draft, never launches |
| `oracle_launch` | `run_id`, `expected_total_cost_cents`, `maximum_total_cost_cents`, `scope_sha256`, `confirmation`; separately launches after fresh review and draft readback |

Before `oracle_launch`, show the user the feasibility quote and dashboard link,
draft link, audience, task, screening mode, total cost and cap. The tool rereads
the draft and checks its current total immediately before launch. Terac exposes
no atomic price/cap condition, so this is a local pre-launch check rather than a
transactional spending guarantee; Terac controls actual pricing and publication.

`mode` defaults to `replay`; credentials alone do not select live operation.
`mode: "live"` with the read-only attachment requires the startup binding and
creates a local checkpoint without a provider request. The next explicit
`oracle_resume` reads one bound study and can return only status, participant
count and cost fields; no report or feedback is retrieved. A study-creation
request requires `--terac-study-creation` and advances only on explicit resume.
Neither start nor resume launches. Live linked work is unavailable. Omitted or
null `replay_case` selects `inconclusive`; other packaged selectors are
`pass_with_comments` and `fail`. A literal string such as `"null"` is text, not
an omitted selector.

`intent` defaults to `review`. For fresh linked work, supply an existing
`parent_id` and explicit `follow_up` or `new_query`. Each new run has its own
brief binding, begins without feedback, and preserves earlier work. Replay pins
its selected case and fixture version.

Send strings as literal input. JSON-looking brief and answer strings retain their
exact text; the adapter must not reinterpret them as JSON values. Extra fields,
missing inputs and wrong JSON types are rejected. See
[Oracle](recipes/oracle.md) for the default clarification sequence and result
meaning.

## Read the per-tool result format

With the locked MCP SDK 1.30:

- **Discovery** returns the catalog as JSON text. It has no `outputSchema` or
  `structuredContent`.
- **Oracle actions** return a snapshot as JSON text and matching
  `structuredContent`.
- **Tool errors** set `isError: true` and return an `error` code as JSON text and
  structured content. Protocol framing errors remain SDK protocol errors.

Clients should parse the JSON text when structured content is absent rather than
assuming all tools share one response shape. Oracle snapshots contain `run_id`,
`parent_id`, `intent`, `artifact_id`, `brief`, `scope`, `mode`, `status`, `phase`,
`question`, `feedback` and `error`; replay snapshots include `replay_case`, while
live snapshots include a safe `provider_observation` after a successful refresh.

Error codes include `invalid_replay_case`, `replay_case_unavailable`,
`checkpoint_incompatible`, `live_unavailable`, `live_linked_work_unavailable`,
`credential_unavailable`, `binding_mismatch`, `invalid_request_or_checkpoint`,
`checkpoint_unavailable` and `unknown_tool`. Tool error results omit raw inputs,
storage paths and exception details. Malformed replay feedback can also return a
saved failed snapshot; inspect `status` and `error` as well as MCP's tool-error
flag.

## Own one state directory with one process

Oracle calls are serialized within a server process. This is not a multi-process
lock: do not run another server or CLI writer against that directory at the same
time. Checkpoints retain the single-process plaintext JSON limits.

Restart with the same directory and `run_id`. Every replay run and linked
ancestor resolves its own case/version/digest pin. Unsupported or changed
checkpoints are rejected without rewriting them. There are no background
wakeups, deadlines, cancellation, automatic retries or silent migrations.

Discovery and `inspect` observe availability or state; they do not advance work
or approve effects. Delivery, review recommendation, revised brief and provider
labor/payment state stay separate. Expert advice grants no additional authority.

## Compatibility and acceptance

The tool contract is client-neutral. Actual acceptance inside Codex, Claude Code,
Pi, Hermes or another application is **unverified**. A local SDK stdio exchange
does not establish application-specific launch, permissions or rendering behavior.
Remote ChatGPT connectivity requires a separately verified transport and deployment.
For native `command`/`args` registration, see [setup guidance](setup.md#register-with-a-native-host)
and the linked host documentation. Those configuration examples do not establish
that a named client has been tested with this Cookbook server.

For each client, record its version and the installed server revision, then
exercise discovery, the four replay actions, unchanged inspection, restart and
linked work; verify the separate launch tool's schema without performing an
unapproved live launch. Advertise only clients whose complete path has been
verified.

The repository's [MCP integration tests](../tests/test_cookbook_mcp.py) use actual
SDK stdio with synthetic data:

```sh
uv run --no-sync python -m pytest tests/test_cookbook_mcp.py -q
```

These checks need the `connectors` extra. They establish offline protocol and
workflow behavior, not provider conformance or named-client acceptance. See
[Architecture](architecture.md) and [Contributing](../CONTRIBUTING.md).
