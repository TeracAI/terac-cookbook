# Agent guide

Read [README.md](README.md), [CONTEXT.md](CONTEXT.md),
[CONTRIBUTING.md](CONTRIBUTING.md), the [architecture](docs/architecture.md) and
the affected recipe guide before changing behavior. Inspect the branch and working
tree; preserve unrelated work.

## Repository map

- `recipes/`: recipe meaning, input/output binding and synthetic fixtures.
- `runtime/`: shared run identity, lifecycle and checkpoint behavior.
- `adapters/`: replay scheduling and transport boundaries.
- `tests/`: caller-interface correctness checks using fake providers.
- `docs/`: canonical Markdown, generated HTML and local assets.
- `scripts/`: offline checks, documentation rendering and the demo.

## Invariants

- Replay is default. Credentials alone never select live work.
- `inspect` reads saved state without advancement or approval of effects.
- Keep run completion, delivery, review recommendation, revision and provider
  labor/payment state separate. A changed artifact inherits no verdict.
- Keep domain rules inside the Recipe; agree caller seams, failure cases and
  acceptance before changing runtime behavior.
- Deterministic checks own state, exact artifact/scope binding, secrets, effects
  and exact calculations. Tests and callers use the same public interface.
- No credentials, real identities, private payloads or local checkpoints in Git.
  Recorded fixtures require rights and privacy clearance before publication.
- Feedback grants no authority for disclosure, recruitment, spend, follow-up,
  payment, rejection or publication.

Use the offline commands and review procedure in
[CONTRIBUTING.md](CONTRIBUTING.md). Regenerate HTML after changing its canonical
Markdown. Keep claims bounded to implemented and tested behavior; distinguish
synthetic delivery from live-provider and application-client acceptance.
