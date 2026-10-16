# Cookbook language

- **Terac Cookbook:** runnable examples of agents working with expert feedback.
- **Recipe:** a documented workflow with defined inputs, outputs, permitted
  effects and replay data.
- **Oracle** (`oracle`): the first Recipe; scoped review of a go-to-market brief.
- **Recipe Run:** one execution for an exact artifact, scope and authority
  context. Waiting, clarification and delivery are observable parts of the run.
- **Expert Turn:** one bounded request for expert work. Linked work has its own
  identity and scope, even when it refers to an earlier run.
- **Run Status / Phase:** workflow lifecycle and current work. They do not express
  an expert's recommendation or grant authority.
- **Review Recommendation:** Oracle's scoped `pass`, `fail` or `inconclusive`
  judgment about the exact reviewed brief.
- **Review Feedback:** the recommendation plus rationale, findings, comments,
  limitations and suggested next steps.
- **Revised Brief:** changed content with a new artifact identity and no inherited
  recommendation.
- **Replay Fixture:** synthetic or rights-cleared versioned input/response data.
  Generated checkpoints are private local state, not fixtures.
- **Managed Fulfillment:** the provider's expert recruitment, assignment,
  interaction and payment, separate from local workflow delivery.

The [architecture](docs/architecture.md) owns shared responsibilities. The
[Oracle guide](docs/recipes/oracle.md) and
[feedback contract](recipes/oracle/review-contract.md) own Oracle's domain meaning.
