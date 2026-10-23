# Read-only Terac access

These diagnostics check a connection and response shape. They do not create or
launch a study, recruit or pay an expert, approve labor, or retrieve expert
feedback. Oracle remains replay by default, including when a key is present; its
separate read-only live attachment requires an explicit reviewed binding.

## Choose the access path

The Windows operator scripts keep an API key encrypted for the current Windows
account outside the checkout. The Python reader accepts a credential supplied
by the embedding application and returns sanitized context facts. These are
separate entrypoints; Python does not read the scripts' credential store.

API-key access and browser OAuth are different flows. These scripts do not
configure Terac's ChatGPT/Claude connector or verify browser OAuth.

## Windows terminal setup

From a trusted local checkout, run the following yourself. The prompt hides input;
never put a key into an argument, issue, chat, test fixture or committed `.env`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_terac_credentials.ps1
```

To replace an existing key, explicitly opt in before the hidden prompt:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_terac_credentials.ps1 -Replace
```

The store uses Windows CurrentUser DPAPI and restrictive user permissions under
`%LOCALAPPDATA%\TeracCookbook`. It rejects unsafe storage paths and permissions.
It does not validate a key by calling Terac. This is Windows-specific local
storage; it is not a portable secret manager or protection against a compromised
process already running as that user.

## Explicit operator probe

The following command uses the stored key and performs authenticated reads. Run
it only for an account/organization you are authorized to inspect:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\probe_terac_access.ps1
```

It requests the fixed REST organization-context endpoint, initializes hosted
Terac MCP with the supported `2025-11-25` protocol and lists tools. The diagnostic
requires the server to advertise its tools capability; a server without it is
outside this probe's supported scope. It may call
only the allowlisted `terac_get_context` tool after its input-schema check passes.
It does not invoke other discovered tools or any expert workflow.

Output contains status and shape facts, not the key, organization values,
balances, tool responses or raw provider errors. Invalid supported response
shapes keep facts unknown and produce a sanitized phase error. This is a narrow
diagnostic for supported fields, not a general MCP conformance validator.
Unsupported negotiation or context schemas stop that path; REST and MCP results
remain separate. Passing a diagnostic does not establish administrator rights,
write permissions, delivered expert feedback or same-expert follow-up support.

The PowerShell REST `response_shape_valid` field means a JSON object was parsed;
its remaining fields describe property presence. It does not validate the full
six-field context schema used by the Python reader below. Unsupported blank MCP
pagination cursors stop discovery with an incomplete catalog and a safe error.

## Python context reader

Install the connector dependencies from the reviewed revision:

```sh
uv sync --locked --extra connectors
```

An embedding application can call `adapters.terac_context.read_context` with its
in-memory bearer token. Supply credentials through the application's trusted
secret mechanism; there is no key-bearing command-line option.
Invalid credentials, including non-ASCII or whitespace/control characters, fail
locally with a generic input error before any request.

The reader sends one fixed
`GET https://terac.com/api/external/v2/organizations/current/context`, with no
request body, query, redirect following, retry, pagination or dashboard-link fetch.
It supports identity, gzip and deflate response encodings, with bounded decoding.
Other encodings fail explicitly. Its local limits are a 20-second network-operation
timeout and 1 MiB of decoded response data. These limits do not state a Terac
service guarantee or a whole-workflow wall-clock deadline.

A successful response must contain `markdown`, `organizationId`,
`organizationName`, `organizationSlug`, finite numeric `balanceDollars`, and
`dashboard`. Dashboard requires strings `home`, `opportunities`, `drafts`,
`feasibility`, `finance` and `api_keys`. Null/wrong-type required fields are
rejected; unrecognized fields are discarded. The returned `ContextObservation`
contains status and presence facts only, never those values.

`ContextReadError` exposes a safe error code and HTTP status when known, including
after a response body-stream failure. A positive decimal `Retry-After` of up to
ten digits may be reported, without waiting or retrying. Authentication,
permission, status, timeout, transport, size, JSON
and schema errors stay distinct. See the
[published operation](https://terac.com/docs/developers/reference/getOrganizationContext)
for provider documentation; the local reader's schema support is deliberately
bounded to its reviewed contract.

## Oracle exact-study status reader

Oracle can attach to one reviewed study using a local JSON binding. The binding
contains `format_version`, `template_version`, `read_only`, `organization_id`,
`project_id`, `opportunity_id`, `brief_sha256` and `definition_sha256`. The final
two values bind the exact brief and complete reviewed study definition. Obtain
these values through an authorized review process; do not infer provider IDs or
hashes. Store the binding outside Git and pass its absolute path as
`--oracle-live-binding` to `terac-oracle` or `cookbook_app`.

The CLI and MCP process read `TERAC_API_KEY` from their environment only when an
explicit `resume` refreshes a live run. Attaching and inspecting remain local.
The reader allows one context read and one read of the bound opportunity through
the hosted Terac MCP endpoint; it validates identity and reviewed-definition
hashes, accepts only documented top-level response fields, and returns status,
participant count and cost fields. Unrecognized top-level fields fail as
`invalid_response`. The context result requires a string `organizationId` that
must match the reviewed binding. It allows an optional string `markdown` and
may include the documented `organizationName`, `organizationSlug`,
`balanceDollars`, and `dashboard` metadata, which it accepts and discards. These
metadata values are not validated or exposed. Unknown context keys, malformed
`organizationId`, or non-string `markdown` fail before the opportunity read.
The reader does not call a submission/report retrieval tool or expose expert
content, and does not invoke creation, launch, clarification, recruitment or
payment tools. A failed read leaves the checkpoint unchanged.
Replay remains the default even when a key or binding is configured.

## Tests and remaining live work

The default test suite uses fake HTTP transport and synthetic secrets. Windows
credential tests use isolated temporary stores. CI does not read an operator's
store or call Terac. The supported public seam is tested for fixed requests,
response budgets, malformed data and sanitized failures.

The status reader does not connect an Oracle run to expert feedback. Fulfillment
needs a verified request/report linkage, substantive feedback retrieval,
continuation and recontact semantics, explicit disclosure/spend authority and
uncertain-write reconciliation. Recorded responses also need rights/privacy
clearance before they can become replay fixtures. See
[architecture](architecture.md) for how transport and Recipe responsibilities
remain separate.
