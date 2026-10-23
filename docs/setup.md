# Local setup

Use a reviewed Git checkout to run the Cookbook examples. The supported local
workflow in this revision is synthetic Oracle replay; read-only Terac diagnostics
have a separate contract in the [Terac access guide](terac-access.md).

## Install from a checkout

Install Git, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/), then
clone the repository and install its locked default environment:

```sh
git clone https://github.com/TeracAI/terac-cookbook.git
cd terac-cookbook
uv sync --locked
```

The `connectors` extra adds the optional local MCP server and its SDK:

```sh
uv sync --locked --extra connectors
```

The default CLI stores checkpoints under `.tmp/oracle` relative to the current
working directory. Keep using the same checkout and directory when resuming a
run. For separate checkouts or longer-lived work, pass an absolute
`--state-dir` outside the repository. Checkpoints are local plaintext JSON; keep
them private and use one CLI or MCP process per state directory.

The [Oracle guide](recipes/oracle.md) shows the replay cases, restart flow and
linked follow-up. Replay uses synthetic feedback and does not assess a real
market or contact an expert.

## Connect a local MCP client

Install the extra and follow the [local MCP guide](mcp.md) to configure a local
stdio process with an absolute state directory. The guide describes the server's
tool schemas and process behavior; it does not establish support for a named
application client.

## Register with a native host

Register the `command` and `args` from the [local MCP launch example](mcp.md#install-and-launch)
in the host's MCP configuration. The host starts and manages this local stdio
process; Cookbook does not run an HTTP daemon or hosted MCP endpoint. Python and
CLI callers invoke the Oracle recipe directly. The inbound MCP adapter calls the
same recipe service through `cookbook_app`; it does not launch the CLI as a
subprocess.

Use the host's current instructions for [Codex](https://learn.chatgpt.com/docs/extend/mcp),
[Claude Code](https://code.claude.com/docs/en/mcp), or [Hermes Agent](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)
to register a stdio command. Those pages describe host configuration mechanics;
the Cookbook launch example is not evidence of acceptance in any named client.
Only SDK-level stdio checks are recorded in this repository; native registration
and end-to-end acceptance in Codex, Claude Code, Hermes or another host remain
unverified. The [per-tool result formats](mcp.md#read-the-per-tool-result-format)
also matter because discovery and Oracle actions return different shapes.

## Update a checkout

Choose the reviewed Git revision explicitly, fast-forward the checkout to it,
then run `uv sync --locked` again. Include `--extra connectors` when using the
local MCP server. Do not run `uv lock` as part of an update. Check the
[restart and update notes](recipes/oracle.md#restart-and-updates) before moving
saved work between revisions.

This repository describes source checkout and local wheel build workflows. It
does not claim a PyPI release; see the version in [package metadata](../pyproject.toml).
