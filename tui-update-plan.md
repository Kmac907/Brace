# Brace TUI follow-on plan

## 1. Product decision

Implement a full-screen Textual interface after every prerequisite in `update-plan.md` is implemented, migrated, tested, and released.

```text
brace tui [repository]
brace --no-color tui [repository]
```

Brace without a subcommand continues to display CLI help.

The two interfaces serve different purposes:

| Interface | Purpose |
|---|---|
| Existing argparse CLI | Concise commands, scripts, CI, redirected logs, and JSON snapshots |
| Textual TUI | Workflow navigation, detailed state inspection, live activity, and user decisions |

Both frontends use the same coordinator, schemas, state machine, locks, budgets, provider operations, recovery rules, and exact-SHA protections. The TUI must not shell out to `brace`, duplicate orchestration, or edit `.codex/*.json` directly.

Use Textual as a core dependency:

```toml
textual>=8.2,<9
```

Retain Rich for normal CLI output. Do not add another UI, state-management, event, or log-viewing dependency. Textual supplies the application, layouts, styling, tables, logs, Markdown rendering, forms, modals, key bindings, workers, and headless tests. See the [Textual documentation](https://textual.textualize.io/guide/).

## 2. Prerequisites and reconciliation

Do not begin this plan until `update-plan.md` has delivered:

- The durable forward-only coordinator.
- Finite task, repair, review-call, provider-attempt, and audit-round budgets.
- The unified task/bug assignment lifecycle.
- Work-conserving scheduling and active-action records.
- Exact review base, candidate, integration base, published head, and tree identities.
- `.codex/results/` immutable evidence and ignored `.codex/logs/`.
- Coherent status and doctor snapshot builders.
- Durable `awaiting_user`, `waiting_provider`, blocker, deadline, terminal-reason, and next-action fields.
- Workflow locking, interruption, crash recovery, and schema migration.
- Stable CLI output, structured status, and exit-code contracts.
- Passing Ubuntu and Windows CI.

Before implementation:

1. Reinspect the released code and schemas instead of assuming the draft plan matches the final implementation.
2. Record the released Brace and schema versions used as the TUI baseline.
3. Map every displayed value in this plan to the coherent status or doctor projection.
4. Add missing read-only projection fields only when the data already exists in authoritative state.
5. Do not introduce TUI-specific mutable workflow state.
6. Preserve all active migrated workflows and consumed budgets.
7. Amend the completed documentation that deferred dashboards to identify this TUI as a separate follow-on release.
8. Keep issue #24 deferred: a local interface for one repository is not multi-project orchestration.

## 3. Visual and interaction system

### Application frame

Use one consistent shell after initialization:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ BRACE  Example                                   Build · Running here      │
│ C:\Code\Projects\Example                         GitHub · main              │
├───────────────────┬────────────────────────────────────────────────────────┤
│ WORKFLOW          │ Build                                                  │
│   Overview        │ 5 of 8 tasks integrated                                │
│   Plan            │                                                        │
│   Build         ● │ [█████████████████████─────────────] 62%               │
│   Audit           │                                                        │
│                   │ Main screen content                                    │
│ OPERATIONS        │                                                        │
│   Activity        │                                                        │
│   Attention     1 │                                                        │
│   Diagnostics     │                                                        │
├───────────────────┴────────────────────────────────────────────────────────┤
│ ↑↓ Navigate   Enter Open   p Primary action   r Refresh   ? Help   q Close │
└────────────────────────────────────────────────────────────────────────────┘
```

The shell contains:

- A two-line header with product, repository, stage, ownership, provider, and target branch.
- A grouped navigation sidebar.
- One selected screen in the content area.
- A contextual footer containing only currently valid bindings.
- An Attention badge showing unresolved durable decisions.
- A stale-data indicator when the last successful snapshot is older than five seconds.
- A modal layer for confirmations, forms, help, and focused details.

Do not combine content from different screens. Build does not embed the Activity dashboard; Overview does not duplicate the Build task table.

### Information hierarchy

Each page follows the same order:

1. Page title and one-sentence state.
2. High-value summary metrics.
3. Primary table, document, or form.
4. Selected-item detail.
5. Current blocker or next action.
6. Contextual action buttons.

Use restrained presentation:

- One primary accent color.
- Green only for successful terminal results.
- Yellow for waiting or attention.
- Red for failure or unsafe state.
- Cyan or the primary accent for active work.
- Dim neutral text for queued or unavailable values.
- Every colored state also has a text label.
- No gradients, logos, animations, per-agent spinners, or decorative panels.
- Use borders only to separate major regions.
- Align labels and numeric values.
- Display durations consistently as `4m 12s`.
- Display budgets consistently as `1 / 3`.
- Use `—` for unavailable values.
- Abbreviate SHAs in tables and show exact SHAs in details.
- Never truncate the distinguishing portion of an assignment ID.

### Responsive layouts

| Terminal size | Behavior |
|---|---|
| At least 120×32 | Sidebar, summary row, table, and persistent detail pane |
| 100–119 columns | Sidebar and table remain; detail opens as a full content view |
| 80–99 columns | Compact sidebar, lower-priority columns hidden, details open separately |
| Below 80×24 | Resize screen with Help, Diagnostics, and Close still available |

Columns are removed in this order on narrow terminals:

1. Elapsed time.
2. Secondary identity.
3. Agent role where phase already identifies it.
4. Non-active budget columns.

State, assignment, blocker, and primary action must remain visible.

### Input and accessibility

Global bindings:

| Key | Action |
|---|---|
| `↑` / `↓` | Move through navigation, table rows, or choices |
| `Tab` / `Shift+Tab` | Move focus between visible regions |
| `Enter` | Open the selected item or activate the focused control |
| `Esc` | Close the current modal or detail view |
| `p` | Run the page's legal primary action |
| `r` | Refresh the coherent snapshot |
| `/` | Focus the current page's filter when one exists |
| `?` | Open contextual keyboard help |
| `q` | Request application close when an input field does not own focus |
| `Ctrl+C` | Use the same safe interruption flow as Close |

Requirements:

- Complete keyboard operation.
- Mouse support without mouse-only actions.
- Visible focus in color and monochrome modes.
- Text labels in addition to symbols and color.
- Scrollable tables, documents, details, and logs.
- Confirmation dialogs must name the consequence of the action.
- Do not use a command palette or hidden command language in the first release.

## 4. Command lifecycle

### Startup

On `brace tui [repository]`:

1. Validate that stdin and stdout support an interactive terminal.
2. Resolve the repository using the same rules as `status` and `doctor`.
3. Load a coherent read-only snapshot.
4. If no Brace project exists, open Setup.
5. If state is valid, open Overview.
6. If another process owns the workflow lock, enter observer mode.
7. If state is invalid or unrecoverable, open Diagnostics with mutating actions disabled.
8. If decisions are pending, show the Attention badge and an Overview callout without forcing navigation.

Opening the TUI does not take the workflow lock.

### Ownership indicators

The header must always show one of:

- `Ready`
- `Running here`
- `Observing external run`
- `Waiting for user`
- `Waiting for provider`
- `Complete`
- `Invalid state`

Observer mode:

- Refreshes snapshots and logs.
- Allows navigation and evidence inspection.
- Disables mutating actions.
- Explains that another Brace process owns execution.
- Never attempts lock takeover.
- Exits without affecting the external process.

### Exit behavior

| Outcome | Exit code |
|---|---:|
| Normal close | 0 |
| Startup, configuration, schema, or fatal TUI failure | 1 |
| Invalid arguments or incompatible flags | 2 |
| User interrupts a TUI-owned workflow and closes | 130 |

Workflow failure does not automatically close the TUI. The user remains able to inspect Attention, Diagnostics, and evidence.

## 5. Complete page designs

### 5.1 Setup

Setup is the only page shown before initialization. It uses a two-column form and preflight layout rather than a multi-step wizard.

```text
Initialize Brace

┌ Project setup ─────────────────────────┐  ┌ Environment ────────────────────┐
│ Repository                             │  │ PASS  Python 3.11+              │
│ C:\Code\Projects\Example               │  │ PASS  Git available             │
│                                        │  │ PASS  Codex available           │
│ Provider       [ GitHub             ▼ ]│  │ FAIL  GitHub authentication     │
│ Visibility     [ Private            ▼ ]│  │ PASS  Repository path writable  │
│ Target branch  [ main                 ]│  │                                 │
│ Agent limit    [ 3                    ]│  │ Selected problem                │
│                                        │  │ Run: gh auth login              │
│ Configuration will be stored in        │  │                                 │
│ .codex/workflow.json                    │  │ [ Refresh checks ]              │
└────────────────────────────────────────┘  └─────────────────────────────────┘

Initialization creates repository-local workflow files and provider resources.
No credentials are stored by Brace.

                                              [ Initialize Brace ]
```

The form exposes exactly the same supported fields, values, defaults, and validation as `brace init`. It must not create TUI-only settings.

UX behavior:

- Validate individual fields after editing and again on submission.
- Display validation beside the relevant field.
- Disable Initialize for blocking preflight failures.
- Warnings require acknowledgement only when the CLI requires it.
- Never request provider tokens or passwords.
- Show a confirmation summarizing repository, provider, visibility, and target before creation.
- During initialization, replace the form actions with the current verified step.
- On partial failure, show Completed, Failed, Reason, Durable state, and Safe next action.
- On success, show a concise result and navigate to Plan.
- Rerunning after partial failure uses the same idempotent coordinator behavior as the CLI.

### 5.2 Overview

Overview answers: “What is Brace doing, is anything wrong, and what should I do next?”

```text
Overview

Build is running normally.                                 Updated 2s ago

┌ Progress ────────────────────────────┐  ┌ Execution ────────────────────────┐
│ Tasks       5 / 8 integrated        │  │ Agents         4 / 6 active      │
│ [█████████████████████────────] 62% │  │ Queue          3                 │
│ Waiting     1                       │  │ Calls          18 / 54            │
│ Attention   0                       │  │ Repair rounds   2 consumed        │
└─────────────────────────────────────┘  └───────────────────────────────────┘

┌ Repository state ──────────────────────────────────────────────────────────┐
│ Provider          GitHub                  Target branch       main         │
│ Integration       a17c0492                Integration tree    924a…        │
│ Current stage     Build                   Next deadline       26m 51s      │
│ Workflow          <workflow identifier>   Ownership           Running here │
└────────────────────────────────────────────────────────────────────────────┘

Recent outcomes
  Merged       task-001   8bf31a2c
  Approved     task-003   awaiting integration
  Waiting      task-004   provider checks

Next
  Build continues automatically. Open Activity for current agent work.

[ Open activity ]
```

Display:

- Overall stage and health sentence.
- Stage-specific progress.
- Active, queued, waiting, and attention counts.
- Aggregate calls and repair consumption.
- Provider and target.
- Exact integration identity in the detail action.
- Nearest persisted deadline.
- Workflow ownership.
- Up to five recent durable outcomes derived from task/bug ledgers, not console events.
- Current blocker and safe next action.

Primary action mapping:

| State | Primary action |
|---|---|
| Not initialized | Open Setup |
| Initialized, no plan | Open Plan |
| Plan available | Begin Build |
| Build running | Open Activity |
| Build awaiting user | Open Attention |
| Build complete | Begin Audit |
| Audit running | Open Audit or Activity |
| Waiting provider | Show provider action and Refresh |
| Workflow complete | Open completion details |
| Invalid state | Open Diagnostics |

### 5.3 Plan

Plan provides both a readable contract and an operational summary.

```text
Plan                                      Revision 2 · Amendment pending

┌ Summary ───────────────────────────────────────────────────────────────────┐
│ Tasks  8     Dependencies  3     Parallelizable  5     Required checks  2 │
│ Contract hash  718c…       Previous revision  1                           │
└────────────────────────────────────────────────────────────────────────────┘

[ Contract ]  [ Task graph ]  [ Checks & constraints ]  [ Amendment changes ]

┌ Contract ──────────────────────────────────────────────────────────────────┐
│ # Approved project contract                                                │
│                                                                            │
│ Actual repository Markdown is rendered here.                               │
│ …                                                                          │
└────────────────────────────────────────────────────────────────────────────┘

Amendment requires a decision.
Integrated tasks are unchanged.

[ Reject amendment ]                              [ Approve amendment ]
```

Use Textual's `TabbedContent` and `MarkdownViewer`.

Tabs:

1. **Contract**
   - Actual generated or approved Markdown.
   - Revision, contract hash, and approval state.
   - External links are not opened automatically.

2. **Task graph**
   - Task ID, title, dependencies, allowed paths/resources, and state.
   - Sort by dependency readiness, then task ID.
   - Selecting a task opens its approved contract detail.
   - Do not add a graphical dependency renderer.

3. **Checks & constraints**
   - Required deterministic commands.
   - Working directories.
   - Scope inclusions and exclusions.
   - Repository/provider constraints.
   - Declared risks.

4. **Amendment changes**
   - Added tasks.
   - Superseded untouched tasks.
   - Changed constraints or checks.
   - Unchanged integrated tasks.
   - Previous and proposed revision/hash.
   - Empty when no amendment exists.

States and actions:

- Before generation: requirements path, readiness checks, and Generate plan.
- While generating: verified coordinator phase and elapsed time.
- Initial plan ready: View contract and Begin build.
- Do not add a separate durable initial approval state.
- Amendment pending: Approve or Reject with a semantic-diff confirmation.
- Amendment applied: show revision and provider publication result.
- Plan failure: show reason, preserved state, and safe retry.
- Invalid contract: disable Build and link to Diagnostics.

“Begin build” is the TUI equivalent of reviewing the output of `brace plan` and then invoking `brace build`.

### 5.4 Build

Build answers: “What is the state of every task, and why is any task not advancing?”

```text
Build                                                   5 / 8 integrated

┌ Summary ───────────────────────────────────────────────────────────────────┐
│ Active 4/6   Queued 2   Waiting 1   Attention 1   Calls 18/54   Repairs 2 │
└────────────────────────────────────────────────────────────────────────────┘

Filter [ All states ▼ ]   Search [                              ]

┌ Tasks ─────────────────────────────────────────────────────────────────────┐
│ Task      Contract                 Phase                 Budget      State │
│ task-001  Coordinator core         Merged                —           Done  │
│ task-002  Provider reconciliation  Implementation        try 1/3     Active│
│ task-003  Review sessions          Initial review        calls 2/9   Active│
│ task-004  Exact-head merge         Provider checks       try 1/3     Waiting│
│ task-005  Audit state              Dependency wait       —           Queued│
│ task-006  Recovery                 Repair exhausted      fixes 2/2   Attention│
└────────────────────────────────────────────────────────────────────────────┘

Selected: task-004 · Exact-head merge
Provider checks are pending. Approved candidate evidence remains valid.

Review base       7ac9…       Approved head      1ab4…
Integration base  a17c…       Published head     991d…
PR                #61         Deadline           26m 51s

[ Open task details ]  [ Open related activity ]
```

Summary:

- Integrated/total.
- Active agent capacity.
- Queue, waiting, and attention counts.
- Aggregate call and repair consumption.

Table:

- Task ID.
- Short contract title.
- Exact state-machine phase.
- Phase-relevant budget.
- Stable state label.

Filters:

- All.
- Active.
- Queued.
- Waiting.
- Attention.
- Complete.

Search covers task ID and contract title locally; it does not query agents or providers.

Task detail uses tabs:

1. **Contract**
   - Approved contract revision/hash.
   - Acceptance criteria.
   - Dependencies.
   - Allowed paths and exclusive resources.

2. **Execution**
   - Current phase.
   - Attempts, calls, repair rounds, and deadlines.
   - Worktree and branch.
   - Active action identity.
   - Terminal reason and safe next action.

3. **Review**
   - Initial reviewer roles and results.
   - PM triage disposition.
   - Accepted blockers.
   - Repair and delta-verification evidence.
   - Explicit review scope.

4. **Validation**
   - Command, working directory, exact SHA/tree, result, and timestamp.
   - Initial, repair, and integration verification kept distinct.

5. **Provider**
   - Provider/repository identity.
   - PR number and URL as selectable text.
   - Expected and observed base/head.
   - Checks state, attempt, and deadline.
   - Post-merge containment/tree evidence.

Build does not embed raw live logs. “Open related activity” selects the corresponding action in Activity.

Primary actions:

- Begin or resume Build when legal.
- Open Attention when user action is required.
- Open Activity while work runs.
- Begin Audit after all required tasks are merged.
- No retry, repair, merge, or force buttons outside coordinator-approved transitions.

### 5.5 Audit

Audit makes the distinction between whole-project inspection and scoped repair unmistakable.

```text
Audit                                      Whole-project closure · Round 2/3

┌ Frozen audit target ────────────────────────────────────────────────────────┐
│ Commit  41bc81e2       Tree  924a…       State  Repairing blockers         │
└────────────────────────────────────────────────────────────────────────────┘

┌ Findings ────────────────────────┐  ┌ Campaign ────────────────────────────┐
│ P0  0    P1  2    P2  1        │  │ Rounds used       2 / 3             │
│ Accepted  2    Rejected  1      │  │ Bugs resolved     4                 │
│ Untriaged  0                     │  │ Closure available Yes               │
└─────────────────────────────────┘  └────────────────────────────────────────┘

[ Audit rounds ]  [ Findings ]  [ Bug fixes ]  [ Final evidence ]

Bug       Severity  Review scope                  Phase              State
bug-014   P1        Finding + proposed fix        Repair             Active
bug-015   P1        Fix diff + affected behavior  Delta verification Active
```

Tabs:

1. **Audit rounds**
   - Round number.
   - Frozen commit and tree.
   - Started/completed timestamps.
   - Finding counts.
   - Outcome: clean, findings, interrupted, malformed, or exhausted.
   - Selecting a round opens its immutable auditor result.

2. **Findings**
   - Finding ID, severity, summary, source round, PM disposition, and evidence.
   - Filters for severity and disposition.
   - Selecting a finding shows affected paths, claimed behavior, evidence, and triage reason.

3. **Bug fixes**
   - Bug ID, linked finding, scope, phase, budget, and state.
   - Selected detail shows bug contract, allowed paths, candidate diff identity, repair history, validation, and provider state.

4. **Final evidence**
   - Exact clean audit commit/tree.
   - Final deterministic-validation evidence at that tree.
   - Project PR identity.
   - Provider checks and final merge verification.
   - Empty explanatory state until closure exists.

Scope labels must use these exact meanings:

| Label | Meaning |
|---|---|
| Whole-project audit | Adversarial inspection of one exact frozen integration tree |
| Bug candidate review | Accepted finding, bug contract, proposed fix, and affected behavior |
| Repair delta verification | Accepted blocker, repair diff, affected behavior, and required checks |
| Closure audit | Next bounded whole-project audit of the changed integration tree |

A bug reviewer or repair verifier must never appear as performing a whole-project audit.

Audit exhaustion opens Attention with the final findings already persisted. No fixes begin until closure capacity exists.

### 5.6 Activity

Activity is the detailed operational dashboard and the only page with live process output.

```text
Activity                                            Build · Running here

┌ Capacity ──────────────────────────────────────────────────────────────────┐
│ Agents 4 / 6   Queue 3   Reviews 2   Repairs 1   Provider waits 1         │
│ Elapsed 18m 42s                                  Next deadline 26m 51s     │
└────────────────────────────────────────────────────────────────────────────┘

Show [ Active + recent ▼ ]   Assignment [ All ▼ ]

┌ Active work ───────────────────────────────────────────────────────────────┐
│ Assignment  Actor               Phase                 Elapsed  Budget State│
│ task-002    Worker              Implementation        4m 12s   1/3    Run │
│ task-004    Contract reviewer   Initial review        1m 43s   2/9    Run │
│ task-004    Risk reviewer       Initial review        1m 41s   2/9    Run │
│ bug-017     Verifier            Delta verification    38s      1/2    Run │
│ task-007    Provider            Checks pending        3m 09s   1/3    Wait│
└────────────────────────────────────────────────────────────────────────────┘

┌ task-004 · Risk reviewer ──────────────────────────────────────────────────┐
│ Started 20:09:10 · call 2/9 · sandbox read-only · follow on               │
│                                                                            │
│ 20:10:13  <actual emitted output>                                          │
│ 20:10:21  <actual emitted output>                                          │
│ 20:10:53  <actual emitted output>                                          │
│                                                                            │
│ No newer output has been emitted.                                          │
└────────────────────────────────────────────────────────────────────────────┘

↑↓ Select action   Tab Change pane   f Follow/pause   g End   / Filter
```

Capacity panel:

- Active/allowed agents.
- Queued actions.
- Active review and repair counts.
- Provider waits.
- Stage elapsed time.
- Nearest persisted deadline.

Table rows represent atomic coordinator actions, not assignments:

- Implementation.
- Initial reviewers as independent rows.
- PM triage.
- Repair.
- Delta verification.
- Integration verification.
- Whole-project auditor.
- Provider observation.

Provider operations are labeled `Provider`, not `Agent`.

Filters:

- Active only.
- Active plus recently completed.
- Waiting.
- Failed.
- One assignment.

Selected-action header:

- Assignment/campaign.
- Role.
- Action identity.
- Started time.
- Reserved call or attempt.
- Sandbox mode when recorded.
- Follow state.
- Completion/termination state.

Log behavior:

- Follow selected active output by default.
- `f` pauses or resumes following.
- `g` jumps to the newest line.
- Preserve selection across snapshot refreshes.
- Load only a bounded tail into memory.
- Load older log chunks when scrolling upward.
- Display sanitized plain text without Rich markup processing.
- Keep raw bounded logs repository-local.
- If no output was emitted, state that explicitly.
- Never invent semantic activity from elapsed time or process state.
- Completed actions remain available through “Active + recent” and assignment evidence.

### 5.7 Attention

Attention is a durable decision inbox with a list on the left and decision detail on the right.

```text
Attention                                                     2 unresolved

┌ Decisions ─────────────────────────┐  ┌ Audit capacity ────────────────────┐
│ ● Audit capacity        Audit      │  │ Round 3 of 3 found 3 blockers.    │
│   Provider approval     task-004   │  │ Findings are preserved.           │
│                                   │  │ No repairs have started.           │
│                                   │  │                                   │
│                                   │  │ Current limit       3              │
│                                   │  │ Additional rounds   [ 2 ]          │
│                                   │  │ New limit           5              │
│                                   │  │                                   │
│                                   │  │ [ Leave unresolved ]              │
│                                   │  │ [ Authorize 2 rounds ]            │
└───────────────────────────────────┘  └─────────────────────────────────────┘
```

Supported attention types:

- Amendment approval/rejection.
- Audit-round extension.
- Repair-budget exhaustion.
- Provider approval or infrastructure wait.
- Exact identity drift.
- Unrecoverable state.
- Cleanup failure.

Every detail panel shows:

- Subject.
- Durable reason.
- Relevant identities and evidence.
- What Brace has and has not done.
- Consequence of each option.
- Safe next action.

Decision behavior:

- Audit extension accepts 1–32 rounds.
- Show previous, additional, and resulting limits.
- Require confirmation.
- Persist authorization before fixes begin.
- Leaving unresolved preserves findings and performs no fix.
- Repair exhaustion offers the established amendment path, not a counter reset.
- Amendment approval shows the semantic diff before confirmation.
- Provider approval is performed outside Brace unless the implemented provider abstraction explicitly supports an authorized operation.
- Identity drift never offers Ignore, Force, or Merge anyway.
- Remove an item only after a refreshed coherent snapshot confirms resolution.

### 5.8 Diagnostics

Diagnostics uses the same check engine as `brace doctor`, with summary, table, and selected-check remediation.

```text
Diagnostics                                  1 failure · 1 warning

Filter [ Problems ▼ ]                                  [ Refresh checks ]

Check                         Result  Detail
Python version                PASS    Python 3.12.4
Git                           PASS    Available
Codex                         PASS    Available
GitHub authentication         FAIL    Authentication required
Repository identity           PASS    Matches workflow state
Schema compatibility          PASS    Supported
Workflow lock                 WARN    Owned by another process
Recovery                      PASS    State is coherent
Support files                 PASS    Installed

Selected: GitHub authentication

Brace cannot query or merge provider pull requests.
No credentials were changed.

Fix
  gh auth login

Then select Refresh checks.
```

Checks include exactly the post-update `doctor` contract:

- Python and Brace version.
- Git and Codex availability.
- Provider CLI availability and authentication.
- Repository, remote, provider, target, and integration identity.
- Schema compatibility.
- Required support files.
- Worktree-root accessibility.
- Workflow lock.
- Interrupted-workflow recovery.

Behavior:

- Default filter is Problems when any FAIL/WARN exists, otherwise All.
- FAIL sorts before WARN, then PASS.
- Selected details show observed fact, impact, and remediation.
- Refresh remains read-only.
- Do not install, authenticate, repair, fetch destructively, edit configuration, or migrate state from this page.
- Mutating workflow actions remain disabled while a blocking diagnostic failure exists.

### 5.9 Help modal

`?` opens a contextual modal containing:

- Current page purpose.
- Available page actions.
- Global navigation keys.
- Ownership mode explanation.
- Where data comes from.
- Exact CLI alternatives such as `brace status --output json`.

Do not create a documentation browser. Link to packaged documentation as selectable text without automatically opening a browser.

## 6. Coordinator and data flow

### Shared internal operations

The CLI and TUI must call the same internal operations:

- Initialize.
- Generate or amend plan.
- Advance build.
- Advance audit.
- Submit a permitted decision.
- Request interruption.
- Build coherent status snapshot.
- Run doctor checks.

If post-update command handlers still combine parsing, prompts, output, and orchestration, extract only these internal callables. Do not create RPC, plugins, a public SDK, a service layer, or a second coordinator.

### Snapshot projection

The TUI consumes the same validated in-memory projection used before `status --output json` serialization.

The projection must provide:

- Repository/workflow/provider identity.
- Stage, phase, ownership, and health.
- Integration and provider identities.
- Task and bug summaries.
- Active atomic actions.
- Budgets and deadlines.
- Review/audit scope.
- Findings and dispositions.
- Validation evidence.
- Attention items.
- Terminal reason and safe next action.

TUI rendering must not independently join multiple JSON files into an assumed coherent state.

### Refresh model

Use a fixed one-second Textual timer:

1. Read one coherent snapshot.
2. Validate it.
3. Diff it against the last rendered snapshot.
4. Update only changed widgets.
5. Preserve focus, selection, scroll position, and paused-log state.
6. Keep the last valid snapshot if a new read fails.
7. Show a stale/error indicator and retry.

This is screen refresh, not workflow polling. It does not consume provider attempts or agent slots.

Do not add an event bus, database, daemon, socket, or filesystem watcher.

### Coordinator worker

Run a mutating coordinator invocation in one Textual thread worker:

```text
Textual UI thread
  ├─ input and rendering
  ├─ coherent snapshot refresh
  └─ one coordinator thread worker
       └─ existing coordinator
            ├─ sole ledger writer
            ├─ workflow lock owner
            └─ existing bounded action executor
```

Requirements:

- One mutating worker per TUI.
- No nested TUI executor.
- UI updates use Textual thread-safe messages or `call_from_thread`.
- The coordinator validates lock ownership and current state again.
- Stale controls cannot authorize illegal transitions.
- Worker completion immediately refreshes the snapshot.
- Exceptions become actionable Attention or Diagnostics content without corrupting the alternate screen.

### Live log streaming

Current Brace writes agent output after completion. Activity requires an explicit post-update enhancement:

- Create the existing per-action log before process launch.
- Stream subprocess output to it while the action runs.
- Retain existing timeout and process-tree cleanup.
- Preserve final structured results in `.codex/results/`.
- Preserve existing log size and redaction limits.
- Resolve log paths through validated repository-local action identities.
- Reject paths outside `.codex/logs/`.
- Sanitize terminal control sequences only for display.
- Do not place raw logs, prompts, or repository contents in public JSON status.
- Do not require or invent a new agent event protocol.

No workflow schema change is needed if the active-action identity already determines the existing log path. If the released implementation cannot make that association safely, add the smallest optional log reference to the read-only projection rather than creating TUI state.

## 7. Decisions, locking, cancellation, and recovery

### Durable decisions

The coordinator must not block while waiting for a TUI form.

1. Persist `awaiting_user` and its decision contract.
2. Stop at the durable boundary.
3. Let Attention collect the response.
4. Submit the response through the shared coordinator operation.
5. Persist it before new work starts.
6. Refresh and resume through a new legal coordinator invocation.

This applies equally to CLI and TUI decisions.

### Locking

- Viewing never takes the workflow lock.
- Mutating actions acquire the existing lock through the coordinator.
- Lock failure changes the TUI to observer mode.
- The UI never edits a stale lock or offers takeover.
- All state and Git mutations remain coordinator-owned.

### Closing owned work

When TUI-owned work is active:

```text
Brace is still building.

Interrupting will terminate active agent processes.
Reserved calls remain consumed.
Durable completed work will be preserved.

[ Keep running ]   [ Interrupt and close ]
```

Interrupt and close must:

1. Signal the coordinator's established cancellation mechanism.
2. Terminate active process trees.
3. Persist recoverable interruption state.
4. Preserve consumed counters.
5. Perform only validated cleanup.
6. Release the workflow lock.
7. Restore the terminal.
8. Exit `130`.

Cancelling only the Textual worker is insufficient.

If safe cleanup cannot be confirmed, keep the TUI open and show the cleanup failure rather than claiming successful interruption.

### Recovery

After reopening:

- Do not restore any consumed budget.
- Reconcile state, immutable results, worktree HEADs, branches, remote PRs, and exact identities through the coordinator.
- Display interrupted actions and partial logs accurately.
- Do not label an abruptly ended log as failed unless authoritative state confirms failure.
- Do not duplicate decisions, calls, pushes, PRs, or merges.

## 8. Explicit interaction boundary

The first release supports:

- Contextual initialization.
- Plan generation and inspection.
- Begin Build.
- Build and Audit start/resume.
- Amendment approval/rejection.
- Audit-round authorization.
- Provider waiting guidance and refresh.
- Campaign interruption.
- Detailed state/evidence inspection.
- Exact active-action and log observation.
- Read-only diagnostics.

It excludes:

- Free-form messages to running agents.
- Persistent agent chat sessions.
- Per-agent cancellation.
- Worktree editing.
- Embedded terminal or shell.
- Arbitrary task reassignment.
- Manual phase changes.
- Validation/review bypass.
- Counter reset.
- Force merge.
- Configuration editor.
- Plugin system.
- Theme system.
- Telemetry.
- Background daemon.
- Browser dashboard.
- Remote TUI attachment.
- TUI JSON or streaming automation output.

## 9. Issue mapping

Existing update-plan issues remain prerequisites rather than being reopened:

| Issue | TUI dependency |
|---|---|
| #25 | Three-stage finite coordinator |
| #28 | Status, diagnostics, blockers, deadlines, and next actions |
| #29 | Shared scheduling and active-action visibility |
| #38 | Durable identities, counters, evidence, and recovery |
| #39 | Reviewer roles and scoped repair verification |
| #40 | Whole-project audit rounds and extension decisions |
| #41 | Exact provider, PR, head, base, and tree identity |
| #51 | Ubuntu/Windows CI and release readiness |
| #24 | Unrelated and still deferred |

Create one follow-on TUI umbrella issue after those deliverables land, with four ordered implementation tasks:

1. Shared frontend operations and safe live-log streaming.
2. Complete read-only Textual shell and page designs.
3. Controlled setup, workflow actions, decisions, ownership, and interruption.
4. Accessibility, cross-platform testing, packaging, documentation, and release.

Do not make the earlier orchestration issues wait for the TUI.

## 10. Implementation sequence

### Phase 1 — Released-baseline reconciliation

- Verify the released post-update coordinator and schemas.
- Map all required screen data to authoritative projections.
- Add Textual and update the uv lock.
- Add parser, TTY validation, exit handling, and packaged styles.
- Extract only the minimum shared internal operations still trapped in CLI handlers.

### Phase 2 — Observability foundation

- Add safe live action-log streaming.
- Implement coherent one-second snapshot refresh.
- Implement observer mode and ownership indicators.
- Add stale, invalid, loading, empty, and recovery states.
- Prove normal CLI behavior remains unchanged.

### Phase 3 — Complete read-only TUI

- Implement the application shell and responsive layout.
- Implement Setup preflight, Overview, Plan, Build, Audit, Activity, Attention inspection, Diagnostics, and Help.
- Implement all tables, filters, selected-item details, tabs, and scope labels.
- Implement keyboard, mouse, focus, resize, monochrome, and log-follow behavior.

### Phase 4 — Controlled mutations

- Connect Setup to shared initialization.
- Connect Generate plan and Begin build.
- Connect Build and Audit start/resume.
- Connect amendment and audit-extension decisions.
- Add contextual primary actions and confirmations.
- Add lock acquisition, observer fallback, interruption, and recovery.

### Phase 5 — Release hardening

- Run deterministic headless Textual tests.
- Test actual Windows and Ubuntu terminals.
- Validate wheel and source distributions.
- Dogfood all three stages in disposable repositories.
- Release separately from the coordinator rewrite.

## 11. Test and acceptance criteria

### Packaging and command behavior

- `brace tui --help` is complete.
- Bare `brace` still displays help.
- TUI requires a TTY and suggests CLI alternatives otherwise.
- Incompatible options return usage code `2`.
- Textual styles and resources work from wheel and source distributions.
- Existing CLI commands and output remain unchanged.

### Page completeness

- Every page has loading, empty, normal, waiting, blocked, failure, and completed states where applicable.
- Every metric maps to an authoritative field.
- Every action has an explicit legal-state rule.
- Selected rows expose sufficient details to diagnose why work is or is not advancing.
- Build and Audit do not duplicate Activity.
- Audit scope labels remain correct throughout repairs and closure.

### UX and accessibility

- All functions work with keyboard alone.
- Mouse and keyboard produce identical actions.
- Focus is always visible.
- Color is never the only state signal.
- 120-column, 100-column, 80-column, and below-minimum layouts behave as specified.
- Runtime resize preserves state and selection.
- Long values scroll or open in details without hiding critical identity.
- Modals return focus to their originating control.
- Terminal state is restored after every exit path.

### Data and concurrency

- The TUI reads one coherent snapshot, never partially joined ledgers.
- Snapshot refresh does not acquire the workflow lock.
- Snapshot errors retain the last valid display.
- Observer mode performs no mutation.
- Repeated button activation cannot create duplicate operations.
- Stale UI controls fail safely under coordinator validation.
- Pool size one cannot deadlock the TUI or coordinator.
- Provider waiting leaves agent capacity available.

### Log safety

- Only actual emitted output is displayed.
- Silent actions are described as silent.
- ANSI and control sequences cannot alter the interface.
- Markup is not interpreted.
- Log paths outside `.codex/logs/` are rejected.
- The in-memory view remains bounded.
- Raw logs never enter status JSON.
- Streaming preserves structured results, timeout handling, redaction, and process-tree cleanup.

### Decisions and recovery

- Initial plan review followed by Begin build matches CLI semantics.
- Amendment decisions remain durable and append-only.
- Audit extension accepts only 1–32 and persists before fixes.
- Declining extension preserves findings and starts no repair.
- Provider waiting exposes no invented approval capability.
- Identity drift exposes no bypass.
- Closing an observer does not affect external work.
- Closing an owner requires confirmation.
- Interruption consumes reserved calls and preserves completed work.
- One hundred TUI restarts cannot restore budgets.
- Crashes cannot duplicate calls, decisions, pushes, PRs, or merges.

## 12. Fixed assumptions

- `update-plan.md` is completed and released first.
- Textual is a core Brace dependency.
- Setup is available inside the TUI.
- Begin build is the initial-plan approval boundary.
- The TUI is full-screen, local, foreground, and single-repository.
- Snapshot refresh is fixed at one second.
- The UI displays exact state and actual output, never inferred agent narration.
- The CLI remains the automation and JSON interface.
- The TUI adds presentation and controlled access to existing operations, not another orchestration architecture.
