# Local MCP

Expose Oracle to a local agent through Model Context Protocol (MCP). The Cookbook
server uses standard input/output (stdio) and calls the same Recipe interface as
Python and CLI callers. Replay is default; local simulation requires opt-in
process configuration and an explicit simulation start.

This is an **inbound Cookbook adapter**: a client invokes Cookbook. It is separate
from outbound Terac MCP/REST and Terac's hosted provider connector. It performs no
provider requests, reads no provider credentials and enables no live fulfillment.

## Install and launch

From the selected reviewed checkout:

```sh
uv sync --locked --extra connectors
```

Configure a local MCP host to launch this process. Use the installed environment's
absolute Python path so launch does not depend on a shell's current directory:

```text
command: ABSOLUTE_PATH_TO_CHECKOUT/.venv/bin/python
args:
  - -m
  - adapters.cookbook_mcp
  - --state-dir
  - ABSOLUTE_PATH_TO_PRIVATE_STATE_DIRECTORY
```

On Windows the environment interpreter is `.venv\Scripts\python.exe`.
Replace both placeholders with actual absolute paths. This is a generic
command/argument specification; translate it into your host's supported format.
It is not a claim of accepted configuration for any named application.

The installed console entrypoint is also available:

```sh
terac-cookbook-mcp --state-dir ABSOLUTE_PATH_TO_PRIVATE_STATE_DIRECTORY
```

The state directory is required and must be absolute; relative paths are rejected
at startup. Choose a directory owned by the process's local user. Tools cannot
choose a state path or pass credentials. Standard output carries MCP protocol
messages; the process adds no HTTP server, tunnel or remote hosting.

To enable synthetic simulation, add `--simulation-dir ABSOLUTE_PRIVATE_MAILBOX`
to the process arguments, or compose `create_server(state_dir,
simulation_dir=mailbox_dir)` in Python. The CLI requires an absolute mailbox
path. Tools accept no mailbox path. An independent local operator publishes
observations using the [Oracle simulation lifecycle](recipes/oracle.md#opt-in-to-local-simulation).
Restart with the same state and mailbox directories. This grants no provider
authority and leaves replay as default.

## Discover and call

| Tool | Inputs and behavior |
|---|---|
| `cookbook_list_recipes` | No arguments; reports Oracle, configured modes, default replay case and packaged case descriptors |
| `oracle_start` | `brief`; optional `mode`, `parent_id`, `intent`, `replay_case`; creates a saved waiting run |
| `oracle_inspect` | `run_id`; reads saved state without advancing |
| `oracle_respond` | `run_id`, `answer`; records a nonempty answer to a pending clarification |
| `oracle_resume` | `run_id`; releases at most one replay event or observes the next simulation revision; paused/terminal state is unchanged |

`mode` defaults to `replay`; `live` returns `live_unavailable`. Omitted or null
`replay_case` selects `inconclusive`. Other packaged selectors are
`pass_with_comments` and `fail`. A literal string such as `"null"` is text, not
an omitted selector.

Without mailbox configuration, discovery advertises replay only and a simulation
start returns `simulation_unavailable`. With configuration it advertises replay
and simulation; `live_available` stays false. Set `mode: "simulation"` explicitly
and omit `replay_case` for simulation. The server never publishes observations.

`intent` defaults to `review`. For fresh linked work, supply an existing
`parent_id` and explicit `follow_up` or `new_query`. Each new run has its own
brief binding, begins without feedback, and preserves earlier work. Replay pins
its selected case; simulation binds its own synthetic reference and revision.

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
`parent_id`, `intent`, `artifact_id`, `brief`, `scope`, `mode`,
`status`, `phase`, `question`, `feedback` and `error`. Replay adds `replay_case`;
simulation adds `simulation_ref` and `simulation_revision` instead.

Error codes include `invalid_replay_case`, `replay_case_unavailable`,
`checkpoint_incompatible`, `live_unavailable`, `invalid_request_or_checkpoint`,
`checkpoint_unavailable`, `simulation_unavailable`,
`simulation_observation_invalid` and `unknown_tool`. Tool error results omit raw inputs,
storage paths and exception details. Malformed replay feedback can also return a
saved failed snapshot; inspect `status` and `error` as well as MCP's tool-error
flag. Simulation rejects an invalid observation without changing its checkpoint.

## Own one state directory with one process

Oracle calls are serialized within a server process. This is not a multi-process
lock: do not run another server or CLI writer against that directory at the same
time. Checkpoints retain the single-process plaintext JSON limits.

Restart with the same directory and `run_id`, plus the same mailbox configuration
for simulation. Every replay run and linked ancestor resolves its own
case/version/digest pin. Simulation retains its reference, accepted revision and
observation digest instead. Unsupported or changed checkpoints are rejected
without rewriting them. There are no background wakeups, deadlines, cancellation,
automatic retries or silent migrations.

Discovery and `inspect` observe availability or state; they do not advance work
or approve effects. Delivery, review recommendation, revised brief and provider
labor/payment state stay separate. Expert advice grants no additional authority.

## Compatibility and acceptance

The tool contract is client-neutral. Actual acceptance inside Codex, Claude Code,
Pi, Hermes or another application is **unverified**. A local SDK stdio exchange
does not establish application-specific launch, permissions or rendering behavior.
Remote ChatGPT connectivity requires a separately verified transport and deployment.

For each client, record its version and the installed server revision, then
exercise discovery, all four actions, unchanged inspection, restart and linked
work. Advertise only clients whose complete path has been verified.

The repository's [MCP integration tests](../tests/test_cookbook_mcp.py) use actual
SDK stdio with synthetic data:

```sh
uv run --no-sync python -m pytest tests/test_cookbook_mcp.py -q
```

These checks need the `connectors` extra. They establish offline protocol and
workflow behavior, not provider conformance or named-client acceptance. See
[Architecture](architecture.md) and [Contributing](../CONTRIBUTING.md).
