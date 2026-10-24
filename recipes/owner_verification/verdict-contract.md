# Owner verification verdict contract

## Input

- `summary`: a nonempty written summary (at most 8,000 characters) of an
  identity-and-intent call with a small-business owner. It must already be
  redacted: the recipe rejects text containing an email address or a phone
  number (`unredacted_input`). Other identifiers remain the caller's
  responsibility.
- `consent_recorded`: must be `true`. The caller asserts that the owner agreed,
  on the record, to the call being summarized and reviewed (`consent_required`).

The run binds to `artifact_id`, the SHA-256 of the exact UTF-8 summary. A result
whose `artifact_id` differs is rejected.

## Result

| Field | Meaning |
| --- | --- |
| `verdict` | `verified`: credible operating owner with genuine intent. `concerns`: plausible but needs follow-up. `fail`: material contradiction or red flag. |
| `reasons` | At least one nonempty reason. |
| `red_flags` | Empty for `verified`; at least one for `fail`. |
| `answers` | Short answers to `credible_owner`, `business_operating`, `genuine_intent`. |
| `limitations` | What the verdict does not establish. |
| `provenance` | `synthetic` for every packaged replay result. |

A verdict is advice to the caller. It does not authorize contact, disclosure,
payment, rejection or any transaction, and it is not identity proof or legal
due diligence.
