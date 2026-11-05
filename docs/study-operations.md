# Study Operations

Study Operations is an agent skill for planning a Terac study and explaining
observed progress. It supplies guidance, not a new recipe, server tool, polling
process or advertising integration. Read the
[skill](../.agents/skills/study-operations/SKILL.md) and [skills guide](agent-skills.md).

## Start with a question

- `Use $study-operations to help plan a one-reviewer study; do not create or launch it.`
- `Explain the observed progress of opportunity OPPORTUNITY_ID and propose one next step. Do not change it.`
- `Explain these saved study observations; label them as a saved snapshot.`

Establish the goal; ask missing count and audience before draft creation, plus
duration where the user owns an external task. Propose method, screener and other
mechanics for confirmation instead of asking for every missing detail. The
selected recipe's schema determines its inputs, not a universal study payload.

## Pricing, draft creation and management

Read the skill's [lifecycle reference](../.agents/skills/study-operations/references/study-lifecycle.md)
for the verified end-to-end routes. Feasibility produces a Terac-confirmed CPI;
it may be priced automatically or by a person, and the caller cannot choose or
observe which. Draft-first creation without a feasibility ID returns a machine
estimate. Neither route skips draft review and separate launch confirmation.
Pending, priced, consumed and terminal requests have different next steps; match
the priced brief and ID before attaching a quote. A draft is not recruitment.

The reference maps supported draft edits, launch/pause/resume/stop, invitation
queues, work review/payment and role-specific website gaps. It does not add these
actions to Cookbook or authorize them. Current tool schemas own exact inputs.

A recipe owns its business purpose and result. This skill owns guidance and
explanation; it adds no shared study-lifecycle implementation. The existing
Oracle study-creation workflow remains the implemented local route described in
the [Oracle guide](recipes/oracle.md#explicit-study-creation-and-launch). Recipes
do not need to call this skill as a parent workflow, and it adds no automatic
recipe-composition framework.

For a local recipe, follow [terac-cookbook](../.agents/skills/terac-cookbook/SKILL.md).
For the hosted Terac connector, follow its context-first instructions and actual
schemas. These are separate MCP connections. A credential does not select live
mode or authorize effects.

## Evidence the agent can use

Use fields actually returned by the current provider interface: study status and
target, task definitions, pricing and dashboard links; aggregate submission
progress; a separate applicant invite queue; available screening/quota progress;
and selected per-task progress where needed.

Keep page coverage explicit. A partial list does not establish a total, and
applicants still screening may be absent from submission reads. A branching
screener's rejection rate concerns answers graded at that question, not everyone
who saw an ad. Quota minimum, maximum and exact targets differ. Missing or stale
values stay missing or stale; one snapshot cannot establish an ETA or trend.

The skill permits a first pass of at most four reads: context, exact opportunity,
one applicant page and one submission page. One selected-detail read may be added.
Skip unnecessary calls and use up to 25 rows per page where supported. Further
pages need another requested bounded pass. This is a skill policy, not a provider
rate limit. There is no background watcher.

## Synthetic example: active but no report

This example is invented, not the current organization's state. Suppose the exact
study has status active, target three, one submission in progress, zero awaiting
review and zero approved. A complete separate applicant listing shows two people
awaiting invitation. No ad-delivery metrics were supplied.

Explain that recruitment is active, one person is working, and two applicants
await invitation decisions. That queue alone does not establish qualification.
Propose reviewing the queue and include a returned dashboard link if available;
otherwise state that no link was supplied. Do not invite anyone
automatically, infer ad reach or promise a delivery time.

If a submission instead awaits review, explain that a customer decision is due.
Do not approve/pay it merely to obtain a report. Task completion and approval
counts are not report content. Native expert-report ingestion remains a separate
unimplemented integration: it needs a supported content shape and task/brief
association. Native upload and a caller-hosted collection page are distinct routes.

## Proposals and effects

This skill observes and proposes. It does not change targeting/pricing,
launch/pause/stop recruitment, invite/decline applicants, approve/reject/pay work
or manage ads. It adds none of those actions to the Cookbook MCP.

Actual changes belong to separately requested, supported recipe/provider
workflows under declared authority. Reuse authority already supplied for the same
action/scope; do not invent it from credentials, a recommendation or website
access. Definition, feasibility/quote, draft review and paid launch are separate.
Slow progress does not justify creating a replacement study.

Local run inspect is a pure saved-state read. Recipe resume advances that run
within its mode. Terac opportunity resume restarts paused recruitment. Keep these
identities and actions distinct.

## Panels, advertising and website assistance

The [recruitment guide](https://terac.com/docs/researchers/recruitment) assigns
sourcing-channel selection and pacing to Terac. Public
[opportunity](https://terac.com/docs/developers/reference/getOpportunity),
[applicant](https://terac.com/docs/developers/reference/listApplicants) and
[submission](https://terac.com/docs/developers/reference/listSubmissions) reads
provide operational progress. The reviewed public interface does not expose Meta
campaign controls, impressions, reach, clicks or channel spend.

Some authenticated internal roles expose an Ads Manager with creative review and
publication controls; these are not promised to normal customer accounts or the
public MCP. Configured daily/lifetime caps and a Review label prove neither a
live campaign nor spend. Verify publication, delivery and freshness separately.
Dashboard features are not proof of an API or permission to operate campaigns.
Ask the Terac contact for missing metrics or
give a returned dashboard link. Optional computer use can assist an explicitly
requested dashboard task when supported by the host; read that host's relevant
skill first. It grants no extra editing, recruiting, advertising or payment
authority. Minimize participant data in summaries and keep it out of Git.

## Repository packaging

The skill is repository instruction material. It installs no transport or tool.
The current Python wheel and source distribution do not include `.agents/skills`;
use a checkout for these instructions. See [discovery and package contents](agent-skills.md).
A later saved/replayable operations-brief recipe may be useful, but this change
does not implement or register one.
