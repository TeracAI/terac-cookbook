# Study lifecycle and supported routes

Read this for creation, pricing or management questions. This is a workflow map,
not a second schema: inspect the connected tool's current description and inputs
before acting. A hosted Terac tool, a Cookbook recipe action and a dashboard
control are distinct capabilities. Never claim one automatically exposes another.

## Choose a route

1. Read organization context/policies and discover existing projects, studies and
   feasibility requests as needed. Resolve exact identities; prefer continuing
   the matching draft over creating a duplicate. A quotation is not a study.
2. Establish the desired output, count and audience. Empty targeting is not
   worldwide: the tool's base audience is US adults, any language. Resolve actual
   filter slugs/options through the filter tools, not guessed names or codes.
   Propose screening criteria, collection route, duration where agent-designed,
   review/payment mode and quotas, then disclose assumptions for confirmation.
3. Choose feasibility-confirmed or draft-first estimated pricing below. Honor
   the user's selected route. Budget limits are not quotes or account balances.
4. Build/update a draft only when that action is requested and preconditions are
   satisfied. Show the returned draft-editor link and compact confirmation of
   count, literal audience/unrestricted dimensions, screening criteria, task and
   duration, payment/review mode and cost provenance/total. Disclose assumed
   recruitment window, device/business setup and provider screening gates.
5. Launch is separate: re-read current scope, price/funding and any changed
   draft, then obtain explicit launch authority for that setup. Never convert a
   feasibility request, draft review or sufficient balance into launch permission.
   Cookbook/provider cap limitations remain those of the actual selected action.

The inspected draft builder has four steps: Setup, Audience, Tasks and Review.
Inspect required-field messages before expecting later steps to open; an
API-created draft can still need a website-required field such as an internal
name. Report the missing value rather than silently filling it. Step labels and
controls can vary by role/version. An available-looking field is not evidence
it was saved or is writable by MCP; draft review is still separate from launch.

## Pricing provenance and feasibility

**Feasibility-confirmed:** `terac_request_feasibility` sends task/panel descriptions,
count and requested turnaround. Include paid-task duration and pay basis in the
brief; turnaround hours and participant task minutes are different. Terac may
price automatically or through a person; the caller cannot select or observe
that route. Never promise human pricing or a turnaround.

Read `terac_get_feasibility_request` by exact request ID:

- `RECEIVED`: unpriced; CPI is null. No figure/range/total is a quote yet.
- `RESPONDED`: a confirmed CPI is available. Match task, audience, count and
  duration to the priced brief before passing its `feasibility_request_id` to
  draft creation. Check pay basis and returned draft total; do not invent missing
  denomination or add platform fees twice.
- `WON`: already used for a launched study; do not reuse for another draft.
- `LOST` / `NOT_PURSUED`: terminal without a usable quote. Stop polling, do not
  infer why. Offer a separately requested estimate draft, rescope/requote, or
  Terac follow-up. Terminal does not mean another linked study failed or stopped.

A request can stay RESPONDED after attaching to a draft; WON follows launch.
Duplicate/similarly worded requests require content/ID comparison, not picking
the newest price. A material scope change needs fresh pricing. Use the returned
feasibility-list link; there need not be an individual request web page.

**Draft-first estimate:** `terac_create_opportunity` without a feasibility ID
returns an autonomous machine estimate from the supplied tasks/targeting. Label
it estimated; do not call it feasibility-confirmed or use it to fill a pending
request's price. With a matching usable feasibility ID, the confirmed CPI is
honored. Both routes finish at a reviewable draft before a separately authorized
launch. Read the actual funding/shortfall and cost breakdown at that time.

No autonomous polling loop: read once, report pending state and the returned link.
If the user asks to monitor, use a finite check window and current provider cadence;
recruitment and feasibility continue while the agent is stopped.

## Collection and review

- `file_upload`: native file delivery; put readable material/context in its
  description and upload instructions/limits in their actual fields. A stored
  task URL is not proof the upload screen displays it.
- `interview`: requires a caller-hosted page where work is both performed and
  submitted; a document/homepage is not that page. It does not book a human call.
- `activity`: for genuinely no deliverable. A completion button is not a report.

Keep unpaid eligibility screening separate from paid research questions. Read
back filters, screener logic, quota type and applicant-review mode. Auto-invite
does not eliminate applicants flagged for manual review; inspect that queue too.
Manual work approval gates payment; self-report can pay immediately. Neither
completion nor payment establishes report content, verdict or replay rights.
Native upload support is documented, but Cookbook native acquisition still needs
verified content/file retrieval and study/task/brief binding; never invent it.

## Management map

- Discover/read: context, projects, opportunities, feasibility lists/details,
  exact opportunity details, applicants and submission list/detail.
- Prepare: project creation/rename, draft create/update; compare existing work
  before writing. Draft deletion is separate and only valid in supported states.
- Recruit: launch draft; pause ACTIVE; resume PAUSED; stop only the states the
  current provider allows. STOPPED is irreversible; FULFILLED/COMPLETED are not
  ordinary pause/stop candidates. An old ending date plus ACTIVE is conflicting
  evidence to clarify, not permission to silently rewrite the schedule.
- Queue decisions: invite/decline the selected applicant only under declared
  authority. An aggregate count or recommendation cannot choose a participant.
- Work decisions: approve/reject a reviewable submission only under declared
  authority with the actual work/reason; approval pays and rejection affects labor.

This map grants no effect authority and adds no tools to Cookbook. Read-only
inspection is the default; explicit requested effects use the supported workflow
and its confirmation rules. Reuse existing authority only for the same action,
scope and conditions. Missing capability remains a disclosed gap, not a guessed
endpoint or browser workaround.

## Website, recruitment and advertising visibility

Inspect the selected study's returned links. Record whether the page is a normal
customer view or an elevated internal role; internal controls are not a feature
promise to every Cookbook user. Exclude staff/participant identities, private
account payloads and campaign details from repository fixtures/docs.

Recruitment views can expose targeting, screeners, quota progress and optional
role-specific gates. Compare dated provider observations with website evidence;
retain contradictions and distinguish freshness/schema differences.

An ads page may show creative drafts, review/approval state, configured budgets,
publication controls or actual metrics. Verify each visibly. A draft or configured
daily/lifetime limit is neither a live campaign nor money spent; Review is not
published, and missing metrics are not zero. Do not infer reach/clicks from study
screening counts or advertise a dashboard-only control as a public MCP tool.
Generation, approval, publication and budget edits are separate effects; study
budget authority does not authorize an advertising budget. Propose a Terac
recruitment review for stalled work before suggesting more spending.

Optional computer use can inspect an explicitly requested page with the host's
supported tooling. Confirm role/study, avoid changing inputs, and stop at effect
controls unless specifically authorized. Do not bypass an unavailable capability
or access gate through another route.

## Evidence and maintenance

Verified against hosted Terac context, feasibility, create/get opportunity and
submission tool descriptions on 2 November 2026. These current descriptions are
the source for exact schemas and pricing rules. Public entry points:
[MCP](https://terac.com/mcp), [developer docs](https://terac.com/docs/developers)
and [recruitment guide](https://terac.com/docs/researchers/recruitment).
Live account observations are not bundled as examples or proof of public support.
