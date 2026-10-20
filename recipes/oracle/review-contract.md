# Oracle feedback contract

This contract defines Oracle's local review meaning. The Python Recipe validates
it before delivering feedback. Current content is synthetic replay; it is not a
verified provider payload schema.

## Fields

| Field | Meaning |
|---|---|
| `artifact_id` | Identity of the exact reviewed brief text |
| `scope` | Fixed buyer/pain, willingness-to-pay and channel-fit dimensions |
| `recommendation` | `pass`, `fail` or `inconclusive` |
| `rationale` | Nonempty explanation |
| `findings` | Material unsupported or contradicted claims |
| `comments` | Advisory improvements; may be empty |
| `limitations` | What the review cannot establish |
| `next_steps` | Bounded suggested investigation |
| `provenance` | `synthetic` in the current implementation |

The typed snapshot returns tuple-backed feedback. Schema and exact artifact/scope
binding are checked before delivery. A material finding cannot accompany `pass`.
Malformed or mismatched content is never delivered as a recommendation. Replay
records workflow failure; simulation rejects the observation without changing
the saved run.

## Recommendation meaning

- **`pass`:** selected claims are supported within the declared scope and material
  limitations are explicit. Advisory comments can still suggest improvements.
- **`fail`:** a material unsupported or contradicted claim is presented as
  established.
- **`inconclusive`:** evidence or expertise is insufficient; the review identifies
  the gap.

These are scoped review anchors. Scripted fixtures demonstrate delivery and state
behavior, not reviewer qualifications, market quality or arbitrary-brief
evaluation. Verify actual work content before mapping a provider response into
these local fields.

## Revision and fresh work

The caller may apply, decline or defer advice and explain its decisions. A revised
brief has a new identity and no inherited recommendation. Oracle delivery does
not require a rewrite.

Follow-up or a new query starts a fresh linked run with explicit intent and
preserves the previous report. The offline example does not establish same-expert
recontact. Delivery, recommendation, provider labor approval and payment remain
separate facts. Advice grants no authority for disclosure, recruitment, spend,
follow-up, payment, rejection or publication.

See the [Oracle guide](../../docs/recipes/oracle.md) for executable examples and
[Contributing](../../CONTRIBUTING.md#data-authority-and-review) for fixture rights
and privacy requirements.
