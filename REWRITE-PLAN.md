# Brace: clean-slate rewrite plan

Status: design proposal. Implementation has not started.

## 1. Authority and scope

This document is sufficient to start in an empty source tree. No previous Brace implementation, test, prompt, schema, state, roadmap, issue, agent conversation, or development guideline is an input to the rewrite.

The user requirements are:

1. Move toward the supervision experience of Untrivial-ai/agent-orchestrator.
2. Deliver a complete CLI before developing the UI.
3. Make individual agents visible and steerable.
4. Remove unnecessary waiting, repeated reviews, and fragile recovery.
5. Make planning and focused tasks easy to start.
6. Maximize useful throughput while prioritizing accuracy.
7. Rewrite everything; do not migrate or reuse the old product.

All architectural choices below are new proposals. There is no inherited requirement for Python, three global stages, JSON ledgers, an integration branch, or avoiding a supervisor. Agent-orchestrator is a product reference, not code to copy. Its task sessions and project orchestrator are relevant precedents; feature-for-feature parity is not the initial scope. [Reference overview](https://github.com/Untrivial-ai/agent-orchestrator)

### Proposed defaults

| Decision | Proposal |
|---|---|
| Runtime | Go; one executable containing CLI, supervisor, and internal session-host modes |
| Deployment | One local user; Windows, macOS, Linux; no Brace cloud account |
| State | Local SQLite database and bounded local artifacts |
| First harness | Codex native app-server protocol |
| Second harness | Claude Code through a documented structured integration, subject to capability testing |
| First Git provider | GitHub; add others when required |
| Integration | Task-owned branch/worktree and PR to the configured target |
| Merge authority | Explicit human merge by default; project policy can authorize automatic merge |
| UI | Local browser application after the CLI release gate |
| Compatibility | No legacy imports, migration, aliases, or recovery |

The user confirmed Go CLI and local supervisor during planning. This is a firm architecture decision. M0 pins supported versions and validates dependencies; this plan does not claim a tested compatibility matrix.

## 2. Product and release boundaries

Brace supervises focused coding sessions. A persistent project orchestrator helps define outcomes, proposes tasks, and coordinates progress. Workers implement tasks. Deterministic software owns scheduling, permissions, durable facts, observations, and integration gates. Humans can inspect and intervene without editing internal files.

The normal loop is:

```text
describe outcome -> inspect proposed work -> authorize execution
                 -> observe and steer workers
                 -> verify candidates -> review PRs -> integrate
```

Planning, implementation, review, and repair can coexist across different tasks. There is no mandatory project-wide stage barrier. Audits are explicit campaigns against selected revisions, not an automatic recursive loop after every change.

### V1: complete CLI product

- Register an existing repository without changing its checkout.
- Start a focused task or discuss a larger outcome with the project orchestrator.
- Approve concrete task contracts and dependency graphs.
- Inspect, message, pause, cancel, resume, and archive sessions.
- Stream activity; inspect changes, transcripts, findings, checks, and PRs.
- Continuously schedule independent work within resource limits.
- Preserve useful implementation through review and repair.
- Observe provider checks/reviews and integrate under explicit policy.
- Recover from client, supervisor, host, agent, and provider failures honestly.
- Support JSON, replayable events, noninteractive commands, completion, and diagnostics.
- Support Codex fully and validate a second harness before claiming harness portability.

### V2: UI

Project board, worker detail, orchestrator conversation, attention queue, and PR/evidence inspection. All actions use the same API and rules as the CLI. No UI-only orchestration.

### Deferred

Cloud workers, teams/tenancy, billing, mobile apps, remote exposure, plugins, arbitrary workflow designers, embedded browsers, terminal emulation, automatic harness switching, and cross-harness transcript translation. No scaffolding is needed for these features in V1.

## 3. Success and accuracy contract

Optimize verified integrated work per hour under fixed correctness and resource constraints. Agent count, raw commit count, and model-call count are secondary diagnostics.

A coding task is complete only when:

1. Its accepted outcome and acceptance criteria have a recorded revision.
2. Required criteria have evidence; unresolved required decisions block completion.
3. Required deterministic checks passed on the identified candidate/integration inputs.
4. Independent review has no unresolved blocking finding.
5. Required provider checks/reviews satisfy policy on the relevant commit.
6. The exact PR is merged and its result is observed in target history.

A successful process exit, confident agent summary, clean worktree, created PR, or green subset of checks is insufficient. Planning/research tasks may complete with approved artifacts, but coding tasks cannot use that disposition to bypass integration.

Accuracy comes from explicit acceptance criteria, executable checks, independent review, integration validation, and provider/human authorization. No finite test suite or model review proves zero defects. Report measured detection and actual limitations.

## 4. User workflows

### Focused task

1. Register repository and target branch.
2. Submit desired behavior, relevant files, and optional acceptance criteria.
3. Ask only questions that materially affect implementation or acceptance.
4. Present a compact contract: outcome, non-goals, criteria/checks, dependencies, scope, risk, budget.
5. Record approval and start a worker within that authority.
6. Keep conversation, workspace, candidate history, findings, and PR attached to the worker.
7. Route actionable review/CI feedback to the same owner.
8. Integrate under the configured merge policy.

### Larger outcome

The project orchestrator proposes a small dependency graph. Users approve all or a subset, request revisions, or defer it. Unapproved proposals launch no implementation. Planning history persists.

Initially, a prerequisite must integrate before dependent implementation begins. Stacked PRs are deferred until measurements show this restriction dominates throughput. Independent work proceeds immediately.

### Steering

Messages normally queue for the next safe turn boundary. Explicit steering targets the exact current turn only when supported. Scope changes create a contract proposal and decision; they do not silently alter acceptance criteria.

Pause prevents future turns after the current turn settles. Cancel interrupts active work and stops automatic continuation, preserving files/history/PRs. Resume reconciles actual state before continuing. Archive changes visibility, not retention. Cleanup is separate.

### Audit

An audit names scope, revision, checks, and budget. Accepted findings become ordinary repair tasks linked to that audit. Closing an audit resolves or explicitly dispositions its accepted findings. New findings cannot create unlimited automatic audit rounds.

## 5. Architecture

```text
CLI now / browser UI later
            |
authenticated local command API + event stream
            |
one supervisor per local user
    |               |                   |
scheduler        SQLite           Git/provider observations
    |
session hosts, one per active execution session
    |
installed coding harnesses / validation processes
    |
task worktrees and exact-candidate verification workspaces
```

The supervisor allows work to continue after a client exits and gives mutations one owner. Session hosts retain native protocol connections and pending interactions across supervisor restarts. A host is an internal mode of the same binary, not another installed service. SQLite provides transactions. The API prevents CLI/UI divergence.

AO similarly separates its local backend, clients, sessions, and derived display status. This design uses that separation as a precedent without porting AO code. [AO architecture](https://github.com/Untrivial-ai/agent-orchestrator/blob/main/docs/architecture.md)

Start with one Go module and packages for real boundaries: commands, persistence, scheduling, session runtime, harness integration, Git/provider integration, verification. Do not create an interface per entity. Introduce interfaces where boundary tests or a second implementation need them.

Use Go's standard library for HTTP, JSON, subprocesses, concurrency, filesystem work, and tests. Use a maintained SQLite driver; `modernc.org/sqlite` is a candidate CGo-free driver, pending platform/performance validation. Evaluate a CLI library only if it materially reduces command/completion code. [Driver documentation](https://pkg.go.dev/modernc.org/sqlite)

### Local transport and ownership

Use authenticated loopback HTTP on an OS-selected port. A private discovery file contains port, supervisor identity, API version, and startup generation. Keep a random local authentication secret in user-restricted storage; never put it in process arguments or URLs.

Validate Host and Origin, prohibit permissive CORS, and authenticate application routes. The later UI uses a one-use bootstrap exchange and scoped browser session. Localhost alone is not authentication. These controls protect against unrelated browser origins, not a compromised local user account.

An OS exclusive lock prevents two supervisors from owning the same data directory. Never use a PID alone as proof of ownership. Verify endpoint identity and generation before adoption or replacement.

| Component | Responsibility | Prohibited shortcut |
|---|---|---|
| CLI/UI | Input, rendering, subscriptions | Direct database writes |
| Supervisor | Policy, commands, scheduling, reconciliation | Treating agent claims as verified facts |
| Session host | Native transport, owned processes, replay journal | Changing scope or merging PRs |
| Orchestrator | Semantic planning and proposals | Bypassing user policy or budgets |
| Worker | Implementation and repair | Approving itself or editing supervisor records |
| Reviewer | Independent evidence-backed findings | Editing the reviewed candidate |
| Git/provider integration | Facts and authorized operations | Treating unknown/stale observations as success |

## 6. Fresh storage and data model

Use the platform's per-user application-data directory, with an explicit `--data-dir` override. Require a local filesystem; network filesystem deployment is unsupported initially.

```text
<application-data>/brace/
  state.db
  runtime/      private discovery and host endpoints
  sessions/     bounded host journals/transcript artifacts
  evidence/     content-addressed validation artifacts
  workspaces/   project/session worktrees
```

Registration is inert with respect to repository files. An optional explicitly created `.brace/project.json` contains portable, non-secret project policy. Repository identity and local canonical paths live in the database.

No legacy Brace files are parsed or imported. Exclude known legacy orchestration artifacts from automatic task context collection during the rewrite; M0 removes them from the new source tree.

Use SQLite WAL, foreign keys, short explicit transactions, a bounded busy timeout, and one supervisor write path. Never hold a transaction over model, Git, or network work. WAL allows readers alongside a writer but retains single-writer and local-filesystem constraints. [SQLite WAL](https://www.sqlite.org/wal.html)

| Record | Essential information |
|---|---|
| Project | ID, canonical checkout, provider/repository identity, target, policy revision |
| Task | Outcome, contract revision, criteria, dependencies, priority, risk, limits, terminal result |
| Session | Task/project, role, harness, native conversation, desired state, host generation |
| Action | Kind, exact inputs, operation key, reservation, deadline, attempt, outcome |
| Message | Target, content reference, request ID, delivery state, native turn ID |
| Decision | Question, choices, bound revisions/candidate, invalidation condition, answer |
| Candidate | Task, base/head/tree identities, contract revision, previous candidate, workspace |
| Evidence | Exact inputs, check/reviewer specification, environment inputs, result, artifact digest |
| Finding | Severity, claim, reproduction/evidence, criterion, candidate, disposition |
| Provider observation | PR identity, head/base, checks, reviews, mergeability, freshness |
| Event | Monotonic cursor, object/revision, type, bounded safe payload |

Use columns for identities and scheduling queries; bounded JSON for provider-specific payloads. Current records are authoritative. A transactional event table provides history/subscriptions; do not build full event sourcing.

A command transaction validates expected revision, reserves operation/budget, updates state, and inserts an event. External execution follows outside the transaction. A second transaction records the observed result. Pending intent remains recoverable if execution fails between them.

Write artifacts to temporary files, flush and rename before committing references. Garbage-collect old unreferenced files. Missing referenced evidence blocks its gate.

Hosts maintain their own bounded durable protocol journals; only the supervisor writes the central database. Acknowledged sequence numbers permit safe compaction. Unacknowledged critical decisions/turn outcomes cannot be dropped. At capacity, stop accepting turns and raise attention. Truncated diagnostic text receives an explicit gap marker.

## 7. State and control semantics

Persist desired control state separately from observed process facts and quality evidence. An alive process can be waiting for approval; a locally verified task can be waiting for provider CI.

```text
draft -> approved -> queued -> implementing -> verifying
                                  ^              |
                                  |--- repairing-|
                                                 v
                                          ready_for_pr
                                                 v
                                          awaiting_merge
                                                 v
                                            integrated
```

Waiting reasons attach to the phase: user, provider, dependency, quota, resource, or recovery. Cancelled and failed are terminal alternatives. Attempts do not erase previous failures or create replacement task identities.

An execution action moves through `queued -> reserved -> running -> succeeded | failed | cancelled | outcome_unknown`. Only the current controller generation can advance a session. Late events from old generations are retained diagnostically and rejected as transitions.

One writer owns each task workspace. Reviews execute against isolated immutable candidates. Scope changes, candidate mutation, and evidence invalidation are serialized against the task revision.

Ctrl+C on status/log watching detaches the client. Closing an interactive client also detaches unless an explicit cancel operation is sent. Command help must make this clear. Cancellation never deletes work.

## 8. Harnesses and process supervision

Use Codex app-server for native threads, events, approvals, and turn control. Its documented operations include thread start/resume, turn start/steer/interrupt, and streamed notifications. Pin and test a supported subset; do not assume all installed versions have identical behavior. [Codex app-server](https://developers.openai.com/codex/app-server/)

Never scrape terminal output to infer approvals or task completion. Silence is not proof of inactivity.

The adapter must discover executable/version, report authentication readiness safely, create/resume conversations, deliver turns with recorded identities, normalize events/decisions, interrupt exact turns, and reconcile native state after uncertain delivery. Usage is reported when supplied, otherwise unknown.

Capabilities are explicit: resume, steer, approval delivery, history reconciliation, usage, sandbox controls, interruption. Unsupported operations return typed errors; they cannot silently create a fresh conversation or broaden permissions.

The second-harness spike tests Claude Code's documented integration and/or ACP. ACP defines sessions and permission interactions; that does not prove any particular implementation offers durable recovery. Advertise only tested capabilities. [ACP overview](https://agentclientprotocol.com/protocol/overview)

### Session host

The host owns the native process/transport and authenticates supervisor connections with a generation fence. On supervisor loss, its active turn may finish and be journaled. New turns and approval answers wait for reconnection. The host never independently schedules follow-up work.

On Windows, associate the child with an owned Job Object before allowing execution and verify descendant cleanup. On POSIX, use owned process groups and reap descendants; never terminate processes by name. Job Objects provide native group management and termination. [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

Host failure requires proving old execution stopped before relaunch, preserving dirty files, and reconciling native conversation/history. A PID match alone cannot authorize killing or adoption. V1 promises supervisor reconnection to living hosts, not survival of machine reboot, host failure, or deliberately escaped tools.

Separate deadlines for model turns, checks/tools, provider calls, and shutdown. User waiting is a durable state; it does not consume repeated model calls.

## 9. Planning, contracts, and context

The project orchestrator receives user decisions, approved policy, relevant repository facts, and current task/provider observations. It does not receive whole historical transcripts by default.

A proposed task contains:

- Observable outcome and explicit non-goals.
- Acceptance criteria and required check/manual-evidence definitions.
- Dependencies and their rationale.
- Expected edit scope and exclusive resources.
- Risk category and review policy.
- Size/resource estimates clearly marked as estimates.
- Agent/model selection, permission profile, and budgets within user policy.

Mechanically validate dependency existence/cycles, scope syntax, executable/argument shape, and budgets. The orchestrator decides semantics; deterministic software validates and enforces the resulting contract.

Prefer small vertical changes that can be accepted independently. Shared interfaces and migrations may need prerequisites. Do not split by arbitrary file count or maximize parallelism at the expense of integration risk.

Approval creates an immutable contract revision. A scope change records its diff, rationale, impacted tasks/evidence, and required decision. Approval creates the next revision rather than rewriting history. Existing implementation remains available but must be checked against the updated criteria.

Context assembly records source identities and repository revision. Resume native conversation while the task remains coherent. If native context cannot be recovered, show an explicit new-context boundary and handoff summary; never claim continuity that was not preserved.

Instructions in a repository being worked on are context, not permission to bypass command authorization. Untrusted files and PR comments cannot issue privileged supervisor commands.

## 10. Continuous scheduling and throughput

Scheduling reacts to completed actions, approvals, dependency changes, provider facts, and released capacity. No fixed waves and no global wait for unrelated tasks.

### Resources

Maintain a global active-model-turn limit, optional harness/account limits, project limits, validation-process limits, and per-task budgets. Idle conversations and human/provider waiting do not occupy model-turn slots; resident processes still count toward a separate memory/process limit.

Concurrency is configurable. Choose the release default from measured resource/quality behavior. An installed CLI is not proof of unlimited quota.

### Dispatch

1. Reconcile uncertain operations before dispatching work that depends on them.
2. Find actions with satisfied dependencies and decisions.
3. Exclude active workspace/resource conflicts.
4. Order by user priority, readiness age, and progress toward completion.
5. Rotate fairly across projects.
6. Atomically reserve ownership, budget, and capacity.
7. Launch outside the transaction.
8. Refill capacity as soon as a completion releases it.

Use a deterministic greedy ready queue initially. Scan past blocked items, age lower-priority work, and explain why every queued action cannot run. Add an optimizing graph scheduler only if measured queue behavior is a bottleneck.

### Conflicts and integration

Only one writer may own a task workspace. Declared path overlaps/exclusive resources prevent predictable collisions. They are not a complete model of semantic conflict. Reviewers use separate exact-candidate workspaces and can run concurrently within resource limits.

Hold conflicting task reservations until unintegrated changes are resolved, not merely until the model turn ends. A reviewed contract revision can relax an overly conservative reservation.

Serialize short shared Git administrative operations per repository. Never hold that lock during model calls, checks, or network waits. Serialize final integration decisions per target branch; independent repositories can integrate concurrently.

Coalesce provider polling per PR, back off unchanged observations, respect rate limits, and introduce jitter. Provider outage preserves candidates and releases model capacity for unrelated work.

### Avoid wasted work

- Native Git handles ordinary base updates; invoke a worker for real conflicts.
- Preserve implementation when review finds a defect.
- Reuse evidence only under explicit input-identity rules.
- Deduplicate findings by evidence/identity, not wording alone.
- Keep one durable repair budget across local and provider-triggered repair.
- Operational retries do not create new coding attempts.
- Reconcile unknown delivery before another model invocation.

## 11. Verification and independent review

### Executable checks

A check specifies executable, argument array, working directory, timeout, permitted environment, and expected outcome. Shell execution requires an explicit shell definition. Never concatenate model text into shell code.

Run checks in an isolated checkout of the candidate. Capture candidate tree, command, relevant tool/environment inputs, exit code, duration, and bounded artifacts. The worker cannot weaken required check definitions to approve its own work; policy changes need separate authorization.

Checks may be task-local or integration-wide. Missing tools, inaccessible environments, and incomplete results block their gates. Flaky failures remain visible; repeated reruns do not silently convert an unexplained failure to a clean pass. Quarantine requires an explicit independent policy decision.

### Review policy

Default code-changing tasks receive two independent perspectives: implementation correctness and acceptance/test adequacy. Both inspect the same immutable candidate; they can run concurrently. Reviewers receive the contract, relevant base/candidate code, and check evidence. They do not receive persuasive worker self-assessments or each other's findings before submitting their own results.

Explicitly classified documentation-only work may use a narrower policy. Policy is selected before execution and cannot be downgraded by the worker. Runtime, authorization, data migration, concurrency, and recovery changes always receive the full gate.

Blocking findings identify a concrete defect or unmet requirement, candidate, affected criterion, severity, and evidence/reproduction when feasible. Speculative enhancements are nonblocking. Semantic disputes become user/orchestrator decisions; the scheduler cannot redefine scope.

### Repair and budgets

Return accepted findings to the existing worker conversation. Keep the candidate and produce a descendant candidate; multiple commits per task are allowed. Do not reset useful implementation to begin another attempt.

Rerun affected and mandatory regression checks. Scoped follow-up review is allowed when repairs stay within the original contract and established impact boundary. Changes to public interfaces, dependencies, security assumptions, or acceptance scope broaden verification. Uncertain impact cannot justify skipping review.

Initial proposed defaults are three repair turns per task and three transient retries per external operation, with bounded backoff. These are configurable policy values, not accuracy claims. Persist consumed/reserved budgets, deadlines, and authorized extensions. Exhaustion raises a decision with the best retained candidate; restart does not refresh capacity.

### Evidence reuse

Evidence identity includes relevant input trees, contract revision, check/review specification, configuration, and required environment inputs. Identical Git trees do not imply identical environments. Unknown inputs prevent reuse when they affect the result.

Never copy whole-candidate approval onto a changed tree. Narrow evidence may remain valid for unchanged inputs, but final approval requires new evidence covering changed behavior. Record the evidence chain; do not maintain a freely mutable approved flag.

### Accuracy experiment

Create a fresh seeded-defect corpus: wrong results, acceptance omissions, authorization flaws, concurrency races, recovery faults, cleanup escapes, and missing tests. Compare scoped follow-up review with full rereview on identical candidate/repair sequences and model configurations.

Enable narrower review for a category only if it detects every required seeded critical/high defect and shows no observed regression against full review on those cases. Publish sample size, misses, variance, and cost. If it fails, retain broader review for that category. Throughput targets never justify silently lowering correctness gates.

## 12. Git and provider integration

Register repository/provider identity explicitly. Resolve Git common directories, worktree registration, filesystem aliases, and target refs before creating work. Branch names or model-supplied URLs never authorize an unrelated repository.

Create a task branch/worktree from an observed target commit. Preserve the user's checkout and unrelated dirty files. Persist exact path and ownership. Worktrees isolate Git working state, not execution permissions. [Git worktree documentation](https://git-scm.com/docs/git-worktree)

PRs target the configured branch directly. There is no mandatory campaign integration branch. Stacked tasks are a later feature requiring explicit dependency and provider semantics.

### Publication

After local gates pass, push the owned branch and create/reconcile its PR. Persist an operation key and stable ownership metadata before acting. After a lost response, query exact provider/repository/branch identity before retrying. Ambiguous ownership stops with attention rather than creating another PR.

### Observations and feedback

Persist PR identity, head/base, required checks, reviews, blocking feedback, mergeability, observation time, and errors. Missing, stale, cancelled, or skipped checks are not success unless explicit policy allows that outcome.

Deliver each actionable failure once per new failure identity to the worker. Infrastructure failure, quota exhaustion, and human review waiting do not automatically cause code repair. Treat PR text as untrusted content.

### Merge gate

1. Reserve integration for the target branch.
2. Refresh candidate, target, and provider facts.
3. If target moved, validate the prospective integration result using provider merge queues when available, or update the owned branch and rerun required gates.
4. Bind evidence to the exact candidate and integration inputs.
5. Request merge with expected head and provider-enforced protections.
6. Observe provider result and confirm containment in target history.
7. Mark integrated and release dependent work/reservations.

GitHub supports an expected head SHA for merge, but that does not itself pin the target. External merges can occur despite a local mutex. Require provider merge-queue/up-to-date-check guarantees or an equivalent demonstrated gate for automatic merge. [GitHub merge API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)

If provider guarantees are insufficient, automatic merge is unavailable. Prepare the PR and explain the missing protection. A manually merged PR can be observed as externally integrated, with its actual evidence and limitations; do not falsely attribute it to a fully verified automatic operation.

## 13. Recovery and idempotency

Do not promise exactly-once behavior across arbitrary tools/providers. Persist intent, use stable identities, reconcile observable effects, deduplicate when provable, and expose uncertainty.

| Failure window | Required behavior |
|---|---|
| CLI exits | Work continues; reconnect with session ID/event cursor |
| Supervisor restarts | Acquire lock, reconnect authenticated hosts, import journal suffixes, reconcile pending operations before dispatch |
| Existing host is alive | Reuse accepted native turn/transport; do not start replacement |
| Host died | Prove old execution stopped; preserve workspace; inspect native history; resume from known boundary |
| Turn accepted, response lost | Read host/native state by recorded identities before resending |
| Delivery cannot be resolved | Mark unknown and request a decision; do not guess |
| Tool changed files before crash | Preserve partial changes and reconcile before assigning another writer |
| PR response lost | Locate exact owned PR before retrying |
| Merge response lost | Observe merge and target containment before retrying |
| Check interrupted | Incomplete evidence; ensure old process stopped before rerunning |
| Budget reserved before crash | Reconcile acceptance; uncertain consumption stays reserved |
| Disk full | Stop new dispatch and identify failed writes; never acknowledge undurable decisions |
| Database corruption | Stop mutations, diagnose, restore validated backup |

Message states distinguish queued, host-accepted, harness-accepted, completed, rejected, and unknown. Host acceptance does not prove harness execution. Each adapter documents its acknowledgement boundary.

A decision binds question/choices to object revision and candidate where relevant. Stale answers fail as conflicts and show the current decision. Old approval cannot authorize changed scope, command, or code.

Backups use SQLite-supported backup mechanisms and include referenced artifacts or explicitly report excluded evidence. Test restoring and opening a copy. Backup/upgrade support applies only to new-format data; no legacy migration exists.

## 14. Security, permissions, and cleanup

Use installed harness authentication and supported provider credential facilities. No credentials in task text, command arguments, shipped defaults, logs, or project config. Hosts/workers must not inherit supervisor authentication capabilities.

Repository files, agent results, check output, and PR comments are untrusted. Validate structured inputs with size/depth limits. Worker capability is scoped to its assignment; it cannot approve itself, extend budgets, register other repositories, or merge.

Each run selects an explicit execution permission profile. Untrusted code requires a supported isolation environment. Worktrees alone do not sandbox agents or validation commands. Do not silently replace a requested sandbox with unrestricted host execution.

Render output safely, including terminal-control sanitization. Restrict raw artifacts locally. Redact known credential patterns and sensitive argument fields, while acknowledging arbitrary project content cannot be perfectly scrubbed. Normal status contains summaries; raw transcript/log access is explicit.

### Cleanup requirements

Cleanup is distinct from cancel/archive. Preview exact paths/resources. Resolve canonical paths, reject junction/symlink escapes, match ownership markers and registered worktrees, and confirm branch/candidate identity. Preserve dirty or unexplained work. Never recursively delete a path constructed from an unchecked identifier.

Removing one project cannot delete another project's data. Do not delete the only reachable copy of unpushed work. Unknown resources are retained with a reason. Evidence retention is explicit and independently configurable from workspace retention.

## 15. CLI command contract

All product commands call supervisor operations. Read-only commands neither start model work nor repair data. Settle exact positional syntax in M0 and test it as a public contract.

```text
brace version
brace completion <shell>
brace doctor [--project ID] [--json]
brace daemon start|status|stop

brace project add <path> [--target BRANCH]
brace project list
brace project show <id>
brace project configure <id> ...

brace plan --project ID "desired outcome"
brace plan show <plan-id>
brace plan approve <plan-id> --revision N
brace task create --project ID "focused outcome"
brace task list [--project ID]
brace task show <id>
brace task approve <id> --revision N
brace task start <id>
brace task priority <id> <priority>

brace session list [--project ID] [--json]
brace session show <id> [--json]
brace session watch <id>
brace session logs <id> [--follow] [--since CURSOR]
brace session send <id> --file MESSAGE [--steer --turn ID]
brace session pause|cancel|resume|archive <id>

brace decision list [--project ID]
brace decision answer <id> --choice VALUE --revision N
brace review show <task-id>
brace audit start --project ID --ref REF --scope SCOPE
brace pr show <task-id>
brace pr merge <task-id> --head SHA

brace status [--project ID] [--watch | --json]
brace events [--project ID] [--since CURSOR]
brace cleanup --project ID [--apply]
```

This is the target surface, not a requirement to scaffold every command before the first working slice. Final help lists each subcommand separately.

Creation/planning is inert until approval/start. An explicit shortcut may combine creation and execution when the contract is concrete and policy authorizes it. Do not ask the user to repeat already granted authority.

### Output and automation

- Human output begins with state, blocker, and next useful action.
- JSON is one versioned document on stdout; diagnostics use stderr.
- Event output is versioned NDJSON with durable cursors.
- Paginate lists; return references for large artifacts.
- Noninteractive mode never infers approval or hangs on prompts.
- Mutations accept request IDs; duplicate IDs with identical input return the original operation.
- Expected revisions prevent stale updates where needed.
- Status includes freshness, agent action, candidate, PR, waiting reason, budget, and allowed next actions.
- Human text is not an automation contract.

Proposed exits: 0 success; 1 operational/internal failure; 2 invalid usage; 3 user decision required; 4 external prerequisite waiting for synchronous requests; 5 stale revision/conflict; 130 client interrupted. Successful status reads return 0 even when describing blocked work.

Start the supervisor explicitly or via the first mutation with a clear startup message. Offline reads/diagnostics do not start it silently. Daemon stop drains by default; explicit cancellation interrupts turns. Both preserve workspaces.

## 16. API, events, and status

Expose a versioned local API for projects, plans, tasks, sessions, decisions, evidence, and PRs. Define request/response structures once and generate the later UI's API schema. No multiple SDKs in V1.

Responses distinguish accepted from completed and include request/operation IDs, revision, state, and typed error. Queuing input must not claim the agent has acted.

Events commit with state changes. SSE clients reconnect with a cursor and replay retained records. Expired cursors receive explicit resync instructions and a current snapshot cursor. Snapshot-plus-subscribe must not lose records between requests.

Write events directly in supervisor transactions. No CDC triggers or general event bus initially. Bound subscriber queues and disconnect slow readers with a replay cursor; they cannot block scheduling.

Derive display state in one coherent read projection from facts. Categories include queued, working, needs input, blocked, checking, in review, ready to merge, integrated, cancelled, and failed. Always include precise reason and observation time. CLI and UI share that projection.

## 17. Observability and release targets

Record queued, ready, reserved, launched, acknowledged, completed, and persisted timestamps where applicable. Link each action to agent/model, candidate, policy, retry category, usage when available, and wait reason. Missing usage/cost is unknown, not zero.

Measure:

- Accepted integrated tasks per hour on a fixed workload.
- Seeded defect detection by severity and escaped defects found afterward.
- First-pass acceptance, repair turns, and discarded work.
- Ready-queue delay and idle capacity while eligible work exists.
- Waiting for humans, provider, dependency, quota, or local resource.
- Duplicate deliveries/PRs, lost decisions, and recovery duration.
- Evidence reuse, including the identity justifying each reuse.

| Measure | Proposed acceptance target |
|---|---|
| Simulated utilization | At least 95% slot utilization while enough independent actions are eligible |
| Slot refill | Reserve eligible work within 250 ms p95 of released capacity on a documented benchmark machine |
| Activity delivery | Locally received agent event visible to CLI within 500 ms p95 under benchmark load |
| Cached status | Under 200 ms p95 with 100 active sessions and 10,000 task summaries, using pagination |
| Event reconnect | No missing committed records; identifiable duplicates are allowed |
| Recovery | No duplicate external mutation in the deterministic fault matrix; unresolvable cases stop visibly |
| Accuracy | All required seeded critical/high defects caught; no unsafe merge in integration-race tests |
| Useful throughput | Beat a newly authored sequential baseline on independent workloads with identical quality gates |

These are targets, not measured claims. Record hardware, workload, concurrency, harness versions, repetitions, medians/tails, and uncertainty. Use fresh sequential/concurrent test modes; do not import the old product as a benchmark baseline.

Live evaluation uses at least three representative repositories and repeated task runs with fixed acceptance tests and comparable model budgets. Include coupled tasks and conflict-heavy workloads. Report where parallelism slows work down instead of hiding those cases behind independent-edit benchmarks.

Only tasks satisfying the accuracy contract count as accepted throughput. Externally merged or overridden tasks retain separate integration facts and cannot inflate that metric when required evidence is absent.

## 18. Test strategy

All tests are newly authored from this plan. No legacy fixtures, expectations, tests, state formats, or bug-ledger cases are imported.

### Deterministic core

Use a fake clock, controlled completion queue, fake harness, and fake provider. Avoid sleeps/model calls for state and scheduling tests. Cover terminal states, dependencies/cycles, conflicts, fairness, approvals, quota/resource ownership, budgets, and stale generations.

Inject failure at each external boundary: before intent commit, after intent commit, after launch, after external acceptance, before result persistence, and after persistence before response. Repeat client request IDs and replay out-of-order host/provider events.

### Real boundaries

- Temporary Git repositories: worktrees, dirty checkout preservation, candidate ancestry, conflicts, merges, containment, cleanup escapes.
- SQLite: transactions, concurrent reads, exclusive writer ownership, backup/restore, replay, failed writes.
- OS processes on Windows/macOS/Linux: host detachment, timeout, cancellation, descendant ownership, inherited handles, reconnect/adoption.
- Harness protocols: recorded interaction fixtures and optional live start/resume/steer/approval/interrupt tests.
- Disposable GitHub repository: real PR/check/review/merge races, isolated from normal offline testing.

### Required end-to-end scenarios

1. Focused task through approval, independent review, PR, and verified merge.
2. Independent tasks progress while one waits for input.
3. Dependent task begins only after prerequisite integration.
4. Failed review repairs retained code in the same worker conversation.
5. Changed criteria invalidate relevant evidence and request a decision.
6. CI failure is delivered once; provider outage does not initiate code repair.
7. CLI exit preserves work; supervisor restart preserves a living host's active turn.
8. Host death with dirty files preserves work and prevents duplicate writers.
9. Lost PR/merge replies reconcile without duplicate publication.
10. External target advancement prevents stale evidence from authorizing unsafe automatic merge.
11. Cancelling one session stops its owned execution without affecting unrelated work.
12. Exhausted budget stays exhausted after restart; extension is explicit and durable.
13. Agent output, repository instructions, and PR text cannot issue privileged commands.
14. Cleanup rejects other-project paths, symlink/junction escapes, and unexplained unpushed work.
15. Slow subscribers cannot stall scheduling or silently lose critical decisions.
16. Concurrent CLI/browser decisions reject stale revisions instead of double-applying changes.

Run fast core tests on every change, affected boundary tests during development, and the full cross-platform gate before release. Use Go's race detector on supported CI targets. Live model/provider checks are opt-in with expense/credential boundaries; mocks do not establish live compatibility.

## 19. Milestones and dependency order

Each milestone delivers a runnable slice and an exit proof. Directories, mocks, and screenshots alone do not satisfy a milestone.

### M0 — Empty-tree foundation

Deliver:

- Remove legacy source, tests, templates, prompts, schemas, packaging/lockfiles, CI/release/site workflows, and product documentation from the rewrite tree.
- Retain legal/license obligations and Git history, without deriving implementation guidance from them.
- Create the Go module, minimal CLI, test entrypoint, and cross-platform build matrix from scratch.
- Pin supported Go, SQLite-driver, Git, and harness versions after actual compatibility checks.
- Settle command syntax, public errors/events, and essential dependency choices.
- Prove Codex structured interaction and OS process ownership feasibility; spike the second harness.

Exit: clean build/help/version on Windows, macOS, Linux; no references to legacy runtime paths; no model/provider mutation from installation/help; a recorded capability matrix and resolved blocking architecture questions.

### M1 — Durable local commands

Depends on M0. Deliver authenticated supervisor discovery, exclusive ownership, database records, request deduplication, decisions, event replay, status projection, and daemon/project commands. Use fake sessions to establish semantics.

Exit: interrupt every command persistence boundary; repeat requests; reject stale revisions; replay committed facts after restart; prevent a second writer.

### M2 — One real persistent worker

Depends on M1. Deliver the session host, Codex adapter, native conversation persistence, streaming activity, permission requests, messages, pause/cancel/resume, and diagnostics. Use a disposable workspace without automatic publication.

Exit: real coding turn and continuation, supported steering, one-time permission answer, CLI reconnect, supervisor restart mid-turn, host failure recovery, and process cleanup on all supported OSes.

### M3 — Focused task to verified PR

Depends on M2. Deliver contracts/approval, task worktrees, candidates, deterministic checks, independent review, retained repair, PR publication/observation, and explicit merge. Implement review/evidence/PR inspection.

Exit: a real task reaches verified merge; an injected defect is repaired in the same session; lost replies and target advancement cannot bypass gates.

### M4 — Continuous concurrency

Depends on M3. Deliver bounded scheduling, dependencies, ownership/conflicts, fairness, reviewer/check scheduling, provider backoff, and target integration reservations. Show queue reasons/utilization.

Exit: simulated utilization/refill targets; a blocked task does not hold unrelated work; limits and reservations hold under races; model/network execution never holds shared mutation locks.

### M5 — Project orchestration and audit

Depends on M3; integrate with M4 before qualification. Deliver persistent orchestrator conversation, focused-task clarification, task-graph proposals, subset approval, contract revision, delegation, scope decisions, and bounded audits.

Exit: ambiguous outcome becomes approved tasks; independent work proceeds; scope changes preserve history; findings become ordinary bounded repair tasks.

### M6 — Recovery and accuracy qualification

Depends on M4 and M5. Basic recovery is already required in earlier milestones; this completes the matrix. Deliver fault injection, evidence reuse, seeded defects, scoped/full-review comparison, backup/restore, cleanup and security tests.

Exit: all critical/high required defect and failure scenarios pass; uncertainty stays visible; no weakened review policy without evidence; cancellation/cleanup verified cross-platform.

### M7 — Second harness and complete CLI

Depends on M6; harness feasibility was spiked in M0. Deliver the second real integration, explicit capability limits, stable JSON/NDJSON, completion, noninteractive decisions, diagnostics, pagination, exit codes, and complete usage examples.

Exit: shared conformance tests run against both integrations; capability limitations are truthful; every required workflow can be performed through CLI without editing storage or using a UI.

If the second harness cannot satisfy required permission/recovery semantics, record the exact unmet criteria and resolve scope explicitly. Do not quietly substitute a generic shell runner and claim parity.

### M8 — Throughput qualification and CLI release

Depends on M7. Deliver scheduler benchmarks, repeated live evaluation, fresh-install testing, release artifacts/checksums, new-format upgrade policy, complete CLI documentation, and limitations.

Exit: targets pass or have evidence-backed explicit revisions; accuracy gates stay intact; release artifacts pass cross-platform smoke tests. Ship the CLI before beginning UI implementation.

### M9 — UI

Begins only after M8 acceptance. Deliver the local browser interface, served by the same binary and authenticated API. A small TypeScript/React application is the proposed implementation; select/pin UI tooling at M9, not during M0.

Exit: CLI/UI parity, keyboard-accessible workflows, stream reconnect/replay, large-history handling, and stale-decision safety. No UI-owned orchestration state.

### Implementation work breakdown

| Work package | Deliverable | First milestone | Proof |
|---|---|---|---|
| Platform/build | Binary, install, version, CI matrix | M0 | Fresh-platform smoke |
| State/commands | Transactions, identities, decisions, revisions | M1 | Crash/replay tests |
| Local API | Authentication, request IDs, reads/events | M1 | Unauthorized/stale/repeated request tests |
| Host/runtime | Child ownership, journal, reconnect | M2 | OS process tests |
| Codex | Native conversation/turn adapter | M2 | Live protocol conformance |
| Workspace | Owned branches/worktrees, candidates | M3 | Real Git tests |
| Quality | Checks, findings, review/repair | M3 | Seeded-defect cases |
| GitHub | PR/check/review/merge observation | M3 | Provider race tests |
| Scheduler | Continuous dispatch, conflicts, fairness | M4 | Fake-clock resource tests |
| Orchestrator | Proposals, approval, delegation, audit | M5 | Outcome-to-task acceptance |
| Qualification | Full recovery/security/accuracy matrix | M6 | Recorded gate results |
| Portability/CLI | Second harness, automation, completion | M7 | Shared conformance and CLI goldens |
| Performance/release | Benchmarks, packaging, docs | M8 | Reproducible release evidence |
| UI | Board, conversation, attention, evidence | M9 | Shared API/UI acceptance |

Work packages may be developed independently after their contracts exist, but milestone acceptance follows the dependency order. More simultaneous implementation agents is not a substitute for a coherent vertical slice.

## 20. UI specification after CLI release

### Project board

Show queued, working, needs attention, in review, ready to merge, and completed tasks. Cards include task, harness, current action, branch/PR, and precise wait reason. Categories/counts come from the shared read projection.

### Worker detail

Conversation, live actions, changes, acceptance criteria, findings, check artifacts, budget/resource use, and PR state. Provide send, supported steer, pause, cancel, resume. Distinguish queued versus accepted input and terminal outcomes.

### Project orchestrator

Persistent project conversation with concrete plan proposals and contract diffs. Approval shows exactly what work/authority is granted. Rendering a suggestion never authorizes execution.

### Attention queue

Pending permissions, semantic questions, failed checks, exhausted budgets, unknown outcomes, and provider blockers. Each item identifies the affected session/candidate, why it is waiting, and available actions. Stale answers refresh rather than apply elsewhere.

### PR/evidence view

Required gates, current head/base, check freshness, findings, merge policy, and integration result. Link to the provider. The supervisor reevaluates permission/gates when a merge action is submitted, even if the UI button was previously enabled.

### Accessibility and behavior

Keyboard navigation, focus management, semantic controls, contrast, screen-reader labels, reduced motion, and status beyond color are required. Test disconnect/replay, slow clients, paginated histories, and concurrent CLI/UI control. No terminal emulator or browser automation in the first UI release.

## 21. Configuration and permissions

Use a versioned configuration containing only needed controls:

- Default harness/model and allowed execution permission profiles.
- Global/project active-turn and resident-process/check limits.
- Turn/check/provider timeouts and retry/repair budgets.
- Repository target/provider identity.
- Acceptance/check definitions and review policy.
- Publication and merge authorization policy.
- Artifact retention and local resource locations.

Validate/revision changes. Active actions retain the policy revision they started under. Tightening permissions can stop future dispatch immediately; broadening permissions/budgets requires authorized change. Do not change permissions underneath a running tool call.

Ship no credentials, user-specific absolute paths, repository names, or remote identities as defaults. Discover/configure them at runtime. Committed project configuration is not inherently trusted.

Readiness output distinguishes installed, authenticated, compatible, permission-capable, and quota-unknown states. Do not report ready merely because an executable exists. Credentials remain managed by the native tools/OS.

## 22. Risks and decisions

| Risk | Response |
|---|---|
| Harness protocol drift | Pin supported matrix, handshake, protocol fixtures, live smoke before upgrades |
| Turn delivery cannot be deduplicated | Preserve unknown state; reconcile or ask; no exactly-once claim |
| OS process behavior differs | Real containment/interruption tests before supported-platform claims |
| More workers increase conflicts | Measure overlap/integration delay; improve decomposition and ownership |
| Scoped review misses defects | Seeded comparison and broader-review fallback by category |
| Review consumes most capacity | Measure queue/cost/defects found; optimize context and evidence reuse without dropping required checks |
| Provider cannot protect integration inputs | Disable automatic merge until required guarantees exist |
| Database/event pressure | Bound logs, batch noncritical events, measure before adding storage infrastructure |
| Context grows without bound | Native compaction plus explicit decisions and task context; show resets |
| Unsafe repository execution | Explicit permission/isolation profile for both agents and tests |
| UI delays core delivery | M8 release is a prerequisite for M9 |

Decisions confirmed by the user: full rewrite, no reuse/migration, CLI before UI, accuracy, maximum useful throughput, and Go CLI with a local supervisor.

M0 must settle exact dependency/protocol versions, second-harness integration, CLI library choice, and supported execution-isolation capabilities. These technical checks do not reopen permission to reuse legacy code.

## 23. Repository analysis and retirement record

The repository was inspected to identify retirement scope, not to extract design requirements. It contains a Python command-line implementation, tests, bundled agent instructions/prompts/schemas/templates, packaging, release/site workflows, and a product website. None is a dependency of this specification.

Observed categories and their disposition:

| Existing category | Disposition |
|---|---|
| Runtime source and command handlers | Remove at M0; implement Go product from scratch |
| Tests and support fixtures | Remove at M0; derive new tests from this document |
| Agent prompts, instruction files, schemas | Remove at M0; author new contracts/context from scratch |
| Package configuration and lockfile | Remove at M0; create a new Go module/dependency lock |
| Release/site automation and website | Remove at M0; new release process/docs follow new product |
| Runtime state, task/bug ledgers, old conversations | Never import or resume |
| Roadmaps and issue implementation drafts | Remove during this planning pass |
| Root development instructions | Replace with a pointer to this plan and the no-reuse boundary |
| Git history/legal material | Preserve obligations/history; not implementation guidance |

Removed planning artifacts:

- `update-plan.md`
- `tui-update-plan.md`
- `tasks.md`
- `bugs.md`
- `.codex/issue-51-body.md`

The root `AGENTS.md` is replaced. Its previous requirements do not carry forward. Nested instruction files are legacy implementation assets pending removal at M0, not instructions for the rewrite.

This planning pass does not delete implementation files, branches, remote issues/PRs, external worktrees, or user repositories. That is not required to deliver the requested plan. M0 removes the source-tree categories above; it does not resume prior campaigns or adopt old issue bodies as acceptance criteria.

Later filesystem deletion must verify exact paths/ownership and protect unrelated material. A clean rewrite does not require erasing Git history, deleting remote repositories, or discarding legal obligations.

## 24. Requirement traceability and final acceptance

| User requirement | Specification | Release proof |
|---|---|---|
| Closer to agent-orchestrator | Sessions, project orchestrator, PR feedback, shared clients | M2/M3/M5/M9 workflows |
| CLI first | Complete command/API contract; hard UI prerequisite | M8 release before M9 begins |
| See and steer agents | Live events, delivery states, messages/control | M2/M7 conformance and CLI scenarios |
| Slow runs/repeated reviews | Continuous scheduling, retained repair, evidence reuse | M4 utilization and M6 review experiment |
| Recovery | Host journal, durable intents, uncertain outcome handling | M6 fault matrix |
| Easier planning/focused work | Compact task contracts, proposals, subset approval | M3/M5 user scenarios |
| Accuracy | Layered gates, exact evidence, seeded defect corpus | M6/M8 correctness report |
| Maximum throughput | Useful-work metric, resources/fairness, integration concurrency | M8 benchmark report |
| Full rewrite/no reuse | Empty-tree M0, no imports, fresh tests/contracts | M0 source/dependency audit |
| Go/local supervisor | One binary, local API, host modes | M0/M1 platform tests |

Final checklist:

- [ ] Fresh installation/database; no legacy reads, imports, or resumed campaigns.
- [ ] No legacy code, tests, prompts, schemas, build rules, or guidelines reused.
- [ ] Focused task reaches verified integration entirely through CLI.
- [ ] Persistent orchestrator proposes and delegates approved task graphs.
- [ ] Individual sessions are observable and steerable with truthful delivery status.
- [ ] Independent tasks progress continuously under declared limits.
- [ ] Required correctness and acceptance-review gates hold.
- [ ] Repairs preserve work and invalidate changed evidence correctly.
- [ ] Human/provider waiting does not monopolize model slots or cause repeated review.
- [ ] Client exit, supervisor restart, host death, and unknown external outcomes have tested behavior.
- [ ] Provider merge gates bind evidence to relevant candidate/integration identities.
- [ ] Cancellation and cleanup cannot affect unrelated processes/resources.
- [ ] Accuracy and performance reports are reproducible and state limitations.
- [ ] CLI release is accepted before UI implementation.
- [ ] UI calls the same operations and enforces the same rules as CLI.

When implementation is requested, begin at M0 with this document and fresh files. Do not port or incrementally refactor the old product.
