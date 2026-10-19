# Contributing

Help make agent–expert workflows easy to run, inspect and understand. A useful
contribution can be a clearer example, a bug fix, or a small new Recipe with an
offline demonstration.

## From idea to pull request

1. **Describe the job.** Open a recipe proposal issue for a new workflow. State
   the input, useful output, caller actions, waiting and failure cases. Explain
   how to demonstrate it with synthetic data and which effects live use would
   require. The YAML issue form collects a proposal; it is not an executable
   recipe format.
2. **Agree the contract.** Settle the caller interface and observable acceptance
   with a maintainer before adding runtime behavior. Keep domain meaning in the
   recipe, lifecycle in the runner and transport in adapters.
3. **Make one focused change.** Branch from the current `main`. Add source,
   synthetic fixtures, public-interface tests and the documentation needed to
   run the example. Document any explicit CLI or MCP registration changes.
4. **Check and explain it.** Run the offline checks below. Open a pull request
   explaining the resulting behavior, test evidence, fixture provenance and
   any simulated or unverified parts. Resolve review findings before merge.

If you have repository write access, push your feature branch to this repository.
Otherwise use a fork where the repository's visibility and permissions allow it,
or ask a maintainer for a contribution route. Pull requests target `main`.

## Set up a checkout

Requires Python 3.11 or later, Git and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/TeracAI/terac-cookbook.git
cd terac-cookbook
git switch -c recipe/your-example
uv sync --locked --extra connectors
```

Run the [Oracle walkthrough](docs/recipes/oracle.md#run-the-default-case) to see
the existing caller contract. Keep local checkpoints in ignored `.tmp/` or a
private directory outside the checkout.

The `connectors` extra installs the locked HTTPX and MCP dependencies needed by this
revision's stdio integration tests. It does not enable live provider work.
See the [MCP guide](docs/mcp.md) before changing tool schemas or process setup.

## Build a Recipe

Each implemented Recipe lives under `recipes/<recipe-id>/`. Its short README
points to one canonical guide and any detailed domain contract. Include:

- A concrete job, input, output and exact artifact/scope binding.
- Observable waiting, inspection, clarification, completion and failure behavior.
- A default replay with synthetic or explicitly cleared fixtures.
- Permitted effects, required authority and unavailable live behavior.
- Public-interface tests and an invocation from a clean installation.

Python defines execution. New recipes require explicit source registration at
the entrypoints they expose. A directory, descriptor or issue does not install
new runtime behavior. Use a second working example to justify shared abstractions.

See the [architecture](docs/architecture.md) and
[Oracle feedback contract](recipes/oracle/review-contract.md) for the existing
boundaries. Extend those contracts only where the new behavior needs it.

## Test the caller's workflow

Prefer focused end-to-end checks through the same Python, CLI or MCP interface a
caller uses. Cover the successful path and the failures that could change a
caller's decision: invalid input, paused work, read-only inspection, restart,
wrong artifact/scope, linked work and incompatible checkpoints where applicable.

Use fake providers and synthetic holdouts. Local tests and contribution CI must
run without live credentials, expert work or provider effects. Test observable
results and state preservation rather than copying private implementation logic
into assertions. Verify packaged fixtures and entrypoints from a built wheel
outside the source checkout when they change.

## Run offline checks

```sh
uv run --no-sync python scripts/check_docs.py
uv run --no-sync python scripts/render_docs.py --check
uv run --no-sync python -m pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv build
git diff --check
```

If you edit canonical Markdown, regenerate its HTML with
`uv run --no-sync python scripts/render_docs.py` before checking it. Markdown
owns the prose; generated HTML is its offline reading view. Inspect changed pages
and diagrams visually, and review links and local assets.

## Data, authority and review

Do not submit credentials, expert identities, private briefs, raw checkpoints or
confidential provider responses. Synthetic fixtures must be labeled. Recorded
material needs rights and privacy clearance for the exact version and intended
audience before it can be committed or published. Describe provenance without
including private clearance records.

Replay remains the default even when credentials exist. Expert advice cannot
authorize disclosure, recruitment, spend, follow-up, payment, rejection or
publication. A changed brief has no inherited recommendation.

Request review from someone who did not author the change. Review should check
the agreed behavior, repository invariants, fixture provenance and evidence from
the final revision. Passing CI establishes the checks that ran; required checks,
review rules and merge permissions are separate repository settings. Review and
local acceptance do not establish live-provider conformance.

## Optional agent guidance

The repository includes optional `recipe-creation` and `terac-cookbook` skills
under `.agents/skills/`. They point back to the canonical contribution and caller
contracts. Use them only through a host that supports their format; this does not
establish automatic discovery or application-client compatibility.
