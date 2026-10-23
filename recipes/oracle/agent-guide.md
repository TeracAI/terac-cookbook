# Oracle: agent guidance

Read the [recipe guide](README.md) and [feedback contract](review-contract.md)
for exact inputs, result fields and failure semantics. Oracle reviews a brief
within buyer/pain, willingness to pay and channel fit; it does not prove market
demand or rewrite the brief automatically.

## Offline first run

Use an already installed checkout/environment; do not install dependencies or
configure a provider merely to invoke this guide. Read CLI help if the executable
is unavailable. From the checkout, the equivalent module entrypoint is:

```sh
python -m recipes.oracle --state-dir .tmp/oracle-agent start --brief "Synthetic repair-shop appointment tool: demand and pricing are unproven."
```

Use that environment's Python executable. Keep the same state directory for
subsequent commands and replace RUN_ID with the returned identifier:

```sh
python -m recipes.oracle --state-dir .tmp/oracle-agent inspect RUN_ID
python -m recipes.oracle --state-dir .tmp/oracle-agent resume RUN_ID
python -m recipes.oracle --state-dir .tmp/oracle-agent respond RUN_ID --answer "Independent repair shops; willingness to pay is untested."
python -m recipes.oracle --state-dir .tmp/oracle-agent resume RUN_ID
python -m recipes.oracle --state-dir .tmp/oracle-agent inspect RUN_ID
```

For the default replay, first inspect remains waiting, first resume exposes a
clarification, respond saves the answer, and next resume delivers inconclusive
synthetic feedback. Answer the actual pending question in an ordinary run rather
than replaying the example answer blindly. Pass/fail replay cases can deliver
without clarification. The fixture is scripted, not an expert evaluation of the
brief or clarification answer.

With configured Cookbook MCP, discover the recipe and inspect the exposed schemas
before using `oracle_start`, `oracle_inspect`, `oracle_respond` and `oracle_resume`.
They cross the same caller interface as the CLI. Discovery may be JSON text;
action results also expose structured content in this baseline. This is separate
from Terac's hosted connector and does not establish named-client compatibility.

## Interpret only Oracle's result

A succeeded run can deliver pass, fail or inconclusive. Explain the rationale,
material findings, advisory comments, limitations and next steps without converting
an upload or provider approval into a favorable verdict. A missing expert verdict
is not automatically an expert's inconclusive opinion. Real report retrieval,
binding and interpretation require their own supported contract.

Caller revision is optional and has fresh artifact identity. If the recipe supports
it, follow-up/new query uses a fresh start with parent_id and explicit intent; it
preserves the parent's review, not the same expert or payment arrangement.

Replay is available without a key. Do not edit fixtures or checkpoints as a caller.
A separate opt-in Oracle flow supports feasibility, draft preparation and launch
after fresh confirmation. It does not retrieve or deliver expert reports. Follow
the [study-creation and launch contract](../../docs/recipes/oracle.md#explicit-study-creation-and-launch);
do not bypass its effect and confirmation boundaries with direct hosted-connector
calls.
