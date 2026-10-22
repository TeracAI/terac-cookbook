# Independent Review Contract

Use this contract for every independent review of a change in this repository.

## 1. Who Reviews

- The reviewer must be a fresh agent that did not write the code. Give the
  reviewer only the diff, the repository, and this file.
- The writer's self-check is a checklist pass, not an independent review.
- Use `gpt-6.1-sol` at `xhigh` reasoning as the default reviewer. High-risk
  changes—including business rules, thresholds, scoring, pricing and validation
  gates—require a second reviewer on `gpt-5.6-sol` at `xhigh` reasoning.
  These profiles are the current repository review policy for future reviews;
  historical review receipts retain their original requested profiles.
  Do not use GPT 6 Astra for reviews. If either requested profile is unavailable,
  use the other approved Sol profile at xhigh and record the deviation. Record requested profiles separately from actual model
  telemetry; unavailable telemetry stays unknown.
- Review every fix commit independently. Fixes can create bugs.

## 2. How to Review: Execute Before You Read

- Before reading the implementation, write down the inputs you will test.
  Inputs chosen after reading the code tend to inherit the code's assumptions
  and miss the cases that the implementation misses.
- Run the changed code with those inputs in a throwaway environment. For a
  non-executable change, run the nearest applicable link, format, schema,
  rendering, or workflow checks instead. Where possible, perform read-only
  probes against a copy of real data.
- Compare results with the base commit every time, including when the head
  commit's test suite is green.

## 3. What to Probe

- Every alternative form of an input, including forms that combine two
  alternatives.
- Every unit, plus the same input with no unit.
- Two or more entities in one input, in every order.
- Every consumer of a changed enum, status, or constant.
- Every reason or error code, traced through to the message the user actually
  sees.
- Every place where the same fact is stored, computed, or rendered—including
  server and client—to confirm that they still agree.
- Boundaries: empty, one, exactly at the threshold, and past the upper bound.
- Dates: leap days, year boundaries, and time zones.

## 4. Honesty

- Unknown stays unknown. The UI must never invent, guess, or silently resolve
  data it does not have.
- Every label must be true for every record on which it appears.

## 5. Design Principles

Report on every principle below with either `OK` or a violation with
`file:line`:

1. Separation of concerns
2. Programming by intention
3. Encapsulation
4. High cohesion
5. Low coupling

The following are blocking violations: a domain with two owners, duplicated
logic, or business logic in templates or UI code. A review that leaves any
principle unmentioned is incomplete.

## 6. Gates and Tooling

- Attack every new check, hook, or gate with inputs it should reject, including
  inputs phrased differently to evade it.
- Prefer platform primitives, such as Git's `pre-push` hook, over clever command
  parsing.
- Confirm that every exemption still points to something that exists.
- Confirm that no exemption can be used to sneak code through.
- Installing a hook must not disable hooks already in place.
- Changes to the review rules themselves are never exempt from review.

## 7. Documentation and Refactors

- After any search-and-replace or move, reread every edited sentence and ask:
  is this still true?
- Dated documentation must retain its historical paths and facts.

## 8. Reporting

- Classify every finding as `BLOCKING` or `NIT`. Blocking findings must be
  fixed; nits are optional.
- Give every finding a reproduction with the input, expected result, and actual
  result.
- Label each finding `N of the K found so far.`, where `N` is that finding's
  ordinal and `K` is the number of findings discovered by reporting time. `K`
  is not the size of the possible finding pool; that pool is never complete.
- Green tests alone are not evidence. State exactly what you executed.
- End the review with exactly one of the following lines, with no text after it:

`VERDICT: APPROVE`

`VERDICT: CHANGES`
