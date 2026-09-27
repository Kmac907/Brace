# Brace: clean-slate rewrite plan

Status: specification only, as confirmed by the user. Implementation has not started.

## 1. Authority and scope

This document is sufficient to start in an empty source tree. No previous Brace implementation, test, prompt, schema, state, roadmap, issue, agent conversation, or development guideline is an input to the rewrite.

The user requirements are:

1. Move toward the supervision experience of Untrivial-ai/agent-orchestrator.
2. Deliver the CLI foundation first, then a native terminal UI (TUI) as the primary interactive application.
3. Make individual agents visible and steerable.
4. Remove unnecessary waiting, repeated reviews, and fragile recovery.
5. Make planning and focused tasks easy to start.
6. Maximize useful throughput while prioritizing accuracy.
7. Rewrite everything; do not migrate or reuse the old product.

All architectural choices below are new proposals. Python and uv are selected by the latest user instruction; this does not reinstate the legacy implementation or its guidelines. There is no inherited requirement for three global stages, JSON ledgers, an integration branch, or avoiding a supervisor. Agent-orchestrator is a product reference, not code to copy. Its task sessions and project orchestrator are relevant precedents; feature-for-feature parity is not the initial scope. [Reference overview](https://github.com/Untrivial-ai/agent-orchestrator)

### Proposed defaults

| Decision | Proposal |
|---|---|
| Runtime | Python 3.12+; one uv-installed package exposing CLI, supervisor, and internal session-host modes |
| Deployment | One local user; Windows, macOS, Linux; no Brace cloud account |
| State | Local SQLite database and bounded local artifacts |
| First harness | Codex native app-server protocol |
| Additional harness | Deferred; Claude Code is a candidate subject to demand and capability testing; not a CLI/TUI release gate |
| First Git provider | GitHub; add others when required |
| Integration | Task-owned branch/worktree and PR to the configured target |
| Merge authority | Explicit human merge by default; project policy can authorize automatic merge |
| Interactive application | Python Textual TUI after the CLI release gate; primary interactive entrypoint |
| Compatibility | No legacy imports, migration, aliases, or recovery |

The user selected Python with uv packaging to simplify implementation. This supersedes the earlier Go implementation choice. Keep the local supervisor, persistent sessions, SQLite, and TUI. Go or Rust may replace a specific component only when a required capability or measured performance limit justifies it. M0 pins supported versions and validates dependencies; this plan does not claim a tested compatibility matrix.

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
- Support Codex fully with explicit capability limits and conformance tests. Additional harnesses are not a V1 requirement; do not claim harness portability before a second integration passes those tests.

### V2: terminal application

A native terminal application with project board, worker detail, orchestrator conversation, attention queue, and PR/evidence inspection. After this release, `brace` opens the TUI in an interactive terminal; `brace tui` opens it explicitly. CLI subcommands remain available for automation. All actions use the same API and rules as the CLI; the TUI owns no orchestration.

### Deferred

Additional harness integrations, cloud workers, teams/tenancy, billing, mobile apps, remote exposure, plugins, arbitrary workflow designers, embedded browsers, terminal emulation, automatic harness switching, and cross-harness transcript translation. No scaffolding is needed for these features in V1. A second harness does not block either the CLI release or the TUI.

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
Python CLI foundation / Textual TUI as the interactive app
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

The supervisor allows work to continue after a client exits and gives mutations one owner. Session hosts retain native protocol connections and pending interactions across supervisor restarts. A host is an internal mode of the same installed Python package, launched with that installation's interpreter; it is not another installed service. SQLite provides transactions. The API keeps CLI and TUI behavior consistent.

AO similarly separates its local backend, clients, sessions, and derived display status. This design uses that separation as a precedent without porting AO code. [AO architecture](https://github.com/Untrivial-ai/agent-orchestrator/blob/main/docs/architecture.md)

Start with one Python distribution in a fresh `src/brace/` layout. Use small modules for actual boundaries: commands, persistence, scheduling, session runtime, harness integration, Git/provider integration, verification. Do not create an interface per entity or a package per class. Introduce protocols where boundary tests or a second implementation need them.

Use Python's standard library for CLI parsing (`argparse`), JSON, subprocess/process primitives, asynchronous coordination (`asyncio`), SQLite (`sqlite3`), filesystem operations, and tests (`unittest`). Use `aiohttp` for the authenticated local HTTP client/server and event streams, `jsonschema` for structured trust-boundary validation, and Textual for the TUI at M9. Do not implement a custom HTTP parser or terminal framework. Add platform bindings only when necessary to implement verified process ownership. Pin a tested dependency set; selecting a familiar library does not authorize copying any old implementation. [Python SQLite](https://docs.python.org/3/library/sqlite3.html), [aiohttp](https://docs.aiohttp.org/en/stable/)

### Python execution model and selective native ports

Use `asyncio` tasks for model/protocol streams, provider observation, and scheduling. Run installed coding harnesses as separate processes. These are largely external/I/O-bound operations; increased agent concurrency does not require parallel Python bytecode execution. Profile CPU-heavy work before changing runtimes. [Python asynchronous subprocesses](https://docs.python.org/3/library/asyncio-subprocess.html)

Keep blocking SQLite operations on a dedicated, bounded database executor with a connection owned by that thread; route state transactions through the supervisor. Await results without blocking the event loop and do not share a connection between arbitrary threads. Keep transactions short and coordinate cancellation so an accepted write is reconciled even when its waiting client disconnects. Use bounded workers for other blocking local operations. Do not introduce multiprocessing for ordinary network/model orchestration.

Launch supervisor and session-host modes using the installation's `sys.executable` and module entrypoints, not whichever `python` happens to be on PATH. Include package version, protocol version, interpreter/environment identity, and process generation in ownership handshakes. Detach from the launching terminal and redirect standard streams intentionally; Windows background launches must not open unwanted console windows. Resolve packaged support resources with `importlib.resources`, independent of the caller's working directory.

Python does not remove native process-lifecycle requirements. M0/M2 must prove safe child creation, Job Object association, handle ownership, cancellation, and orphan reconciliation on Windows. Use a maintained platform binding when it supplies the necessary guarantees; a process group or `Popen.kill()` alone is not proof that Windows descendants stopped. Keep equivalent real-process tests on POSIX.

Only propose a Go or Rust port when:

1. A reproducible acceptance case establishes a required capability that supported Python/library integration cannot safely provide; or
2. Profiling shows a specific Python component prevents the agreed latency/throughput/resource targets after straightforward fixes.

Record the failing requirement, measured evidence, Python alternatives considered, and proposed smallest replacement. Port that component behind the existing versioned process/API boundary and run the same conformance tests. Examples may include an OS-specific process host or a measured CPU-heavy operation; neither is assumed necessary now. Preserve CLI behavior, task identity, data, and recovery semantics. A full-language rewrite requires its own evidence-backed decision. Add no native build pipeline, FFI, or compatibility framework in anticipation of a future port.

### Local transport and ownership

Use authenticated loopback HTTP on an OS-selected port. A private discovery file contains port, supervisor identity, API version, and startup generation. Keep a random local authentication secret in user-restricted storage; never put it in process arguments or URLs.

Authenticate application routes and validate Host. Reject browser-origin requests; expose no browser sessions, static frontend assets, or CORS-enabled application endpoints. Both native clients read the private discovery credentials. Localhost alone is not authentication. These controls protect against unrelated browser origins, not a compromised local user account.

An OS exclusive lock prevents two supervisors from owning the same data directory. Never use a PID alone as proof of ownership. Verify endpoint identity and generation before adoption or replacement.

| Component | Responsibility | Prohibited shortcut |
|---|---|---|
| CLI/TUI | Input, rendering, subscriptions | Direct database writes |
| Supervisor | Policy, commands, scheduling, reconciliation | Treating agent claims as verified facts |
| Session host | Native transport, owned processes, replay journal | Changing scope or merging PRs |
| Orchestrator | Semantic planning and proposals | Bypassing user policy or budgets |
| Worker | Implementation and repair in its owned workspace | Approving itself, creating commits, changing branches/history, pushing, managing PRs, or editing supervisor records |
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

When demand justifies another harness, first test its documented structured integration against the existing permission, session, recovery, and control conformance cases. Claude Code and/or ACP are candidates, not initial dependencies. ACP defines sessions and permission interactions; that does not prove any particular implementation offers durable recovery. Advertise only tested capabilities and document unsupported operations; a generic shell runner is not equivalent support. No second-harness spike is required for M0 or either release. [ACP overview](https://agentclientprotocol.com/protocol/overview)

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
- Shared-interface contract references and revisions when tasks interact through an interface.
- Risk category and review policy.
- Size/resource estimates clearly marked as estimates.
- Agent/model selection, permission profile, and budgets within user policy.

Mechanically validate dependency existence/cycles, scope syntax, executable/argument shape, and budgets. The orchestrator decides semantics; deterministic software validates and enforces the resulting contract.

Prefer small vertical changes that can be accepted independently. Shared interfaces and migrations may need prerequisites. Do not split by arbitrary file count or maximize parallelism at the expense of integration risk.

Before dispatching tasks that consume or implement a shared interface, the orchestrator supplies the same approved interface contract in each affected worker's context. Specify the relevant signatures or schemas, error behavior, invariants, ownership, and contract revision. Record these as part of the task contracts; no separate contract service is needed. Software validates matching references/revisions, while the orchestrator owns their meaning. If the interface cannot yet be settled, create a prerequisite task and wait for its integration rather than asking parallel workers to invent incompatible assumptions. Unrelated tasks need no interface artifact.

An interface amendment identifies all affected tasks and evidence and follows the contract approval flow below. Running workers cannot silently adopt different interface revisions; dependent work resumes only with consistent approved context.

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

### Candidate ownership

Workers leave implementation and repair changes uncommitted in their isolated task worktrees. The supervisor's Git integration owns candidate commits, branch/history changes, pushes, and PR mutations. Record the expected HEAD and branch before each worker turn; unexpected history or branch changes stop candidate publication and preserve the work for inspection. Enforce these operation boundaries through the harness permission profile and supervisor checks, not solely through prompt instructions.

At a settled turn boundary, stop writes to the workspace, inspect the intended tracked and untracked changes against the approved scope, and create an immutable candidate through the supervisor. Persist its parent/head/tree identities and contract revision before releasing it to checks or reviewers. Do not blindly stage every file or include unexplained artifacts. A worker-reported blocker remains unresolved even when the process exits successfully; neither a summary nor a successful exit establishes completion.

Run validation and review in isolated checkouts of that frozen candidate. Generated files or edits in verification workspaces never silently enter the candidate. An intended additional change returns through the worker and becomes a new supervisor-created candidate with the required gates. Repair preserves ancestry and useful work rather than resetting the implementation.

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

### Base updates and conflict resolution

Use native Git for ordinary base updates. A provider error or unknown mergeability is not evidence of a code conflict. Start an agent resolution action only after Git establishes an actual conflict against captured candidate and target commits.

1. Record durable action intent, candidate/target identities, contract revision, owned workspace, deadline, and budget reservation. Hold the task's single-writer reservation; keep model work and CI waiting outside the short target integration lock.
2. Prepare an isolated merge workspace from the recorded candidate and target. Supply the resolver with the task and shared-interface contracts, both sides' relevant intent and changes, conflicting paths, and Git diagnostics. Resume the owning worker conversation where supported.
3. Permit edits and staging needed to resolve those conflicts, within the approved scope. The supervisor owns starting, completing, or aborting the merge. The resolver cannot commit, reset, rebase, switch branches, push, or change the merge inputs. A scope or interface decision goes to the orchestrator/user rather than being guessed by the resolver.
4. Before committing, verify the expected HEAD and merge target, no unresolved index entries, and the intended changed paths. Preserve unexpected or unresolved work for inspection. The supervisor freezes the resolution as a new candidate; run mandatory integration checks and the review required by section 11. Previous whole-candidate approval does not authorize the changed tree.
5. Refresh provider and target facts before publication/merge and apply the exact-input merge gate below. Further target movement invalidates affected evidence and may require another bounded update; do not force-push over unexplained changes.

Resolution turns consume the task's persisted repair budget; Git/provider retries consume their external-operation budgets. Do not allocate a fresh allowance for each conflict workspace or supervisor restart. Exhaustion, unresolved conflict, or ambiguous operation outcome preserves the candidate/workspace and raises attention; it cannot trigger an unbounded new agent loop. Recovery reconciles recorded identities and actual Git state before resuming.

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
brace tui [--project ID]    # Added at M9; bare brace also opens TUI in a terminal
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

Expose a versioned local API for projects, plans, tasks, sessions, decisions, evidence, and PRs. Define request/response structures once in Python and share one typed client between the CLI and TUI. Document the wire contract; do not introduce a separate frontend schema generator or multiple SDKs.

Responses distinguish accepted from completed and include request/operation IDs, revision, state, and typed error. Queuing input must not claim the agent has acted.

Events commit with state changes. SSE clients reconnect with a cursor and replay retained records. Expired cursors receive explicit resync instructions and a current snapshot cursor. Snapshot-plus-subscribe must not lose records between requests.

Write events directly in supervisor transactions. No CDC triggers or general event bus initially. Bound subscriber queues and disconnect slow readers with a replay cursor; they cannot block scheduling.

Derive display state in one coherent read projection from facts. Categories include queued, working, needs input, blocked, checking, in review, ready to merge, integrated, cancelled, and failed. Always include precise reason and observation time. CLI and TUI share that projection.

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

Prove overlapping execution with controlled start/release barriers: at least two independent workers must start before either is allowed to finish when capacity permits. Include bounded timeouts so accidental serialization fails the test instead of hanging. Also prove the configured concurrency ceiling and progress of unrelated work while another task is blocked; elapsed-time speedups alone are not concurrency evidence.

Assert exact permitted model invocation counts in deterministic success, review repair, conflict resolution, provider failure, and recovery scenarios. Record calls by task, candidate, and action purpose. Duplicate observations cannot repeat a completed review or repair; provider/cleanup failures cannot launch code repair; exhausted or ambiguous actions cannot relaunch without the specified reconciliation or authorization. Restart preserves consumed and reserved budgets. These are scenario-specific assertions, not a blanket prohibition on authorized bounded repair.

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
16. Concurrent CLI/TUI decisions reject stale revisions instead of double-applying changes.
17. Parallel producer/consumer tasks receive matching shared-interface revisions; an unsettled interface creates an integrated prerequisite, and an amendment invalidates affected context/evidence.
18. Unexpected worker commits/branch changes block publication; verification-generated files cannot enter the frozen candidate or acquire its approval.
19. A real Git conflict is resolved against recorded head/base identities, receives fresh required gates, and retains unresolved work without exceeding its budget; target movement and restart cannot bypass those rules.

Run fast core tests on every change, affected boundary tests during development, and the full cross-platform gate before release. Use `unittest` and `IsolatedAsyncioTestCase` with fake clocks and controlled interleavings. Enable asyncio debug checks in CI, check leaked tasks/processes/resources, and exercise real concurrency boundaries; passing tests do not establish an absence of races. Live model/provider checks are opt-in with expense/credential boundaries; mocks do not establish live compatibility.

## 19. Milestones and dependency order

Each milestone delivers a runnable slice and an exit proof. Directories, mocks, and screenshots alone do not satisfy a milestone.

### M0 — Empty-tree foundation

Deliver:

- Remove legacy source, tests, templates, prompts, schemas, packaging/lockfiles, CI/release/site workflows, and product documentation from the rewrite tree.
- Retain legal/license obligations and Git history, without deriving implementation guidance from them.
- Create fresh `pyproject.toml`, `uv.lock`, Python package, console entrypoint, tests, and cross-platform build/install matrix from scratch. Use `uv_build` as the initial build backend, subject to a pinned compatible version.
- Pin supported Python/uv, dependency, Git, and harness versions after actual compatibility checks. Verify the Python runtime includes a compatible SQLite library on every supported platform.
- Settle command syntax, public errors/events, and essential dependency choices.
- Prove Codex structured interaction and OS process ownership feasibility. Additional harness feasibility work is deferred until that integration is requested.

Exit: fresh wheel/sdist builds and isolated `uv tool install` plus help/version smoke on Windows, macOS, Linux; no references to legacy runtime paths; no model/provider mutation from installation/help; a recorded capability matrix and resolved blocking architecture questions.

### M1 — Durable local commands

Depends on M0. Deliver authenticated supervisor discovery, exclusive ownership, database records, request deduplication, decisions, event replay, status projection, and daemon/project commands. Use fake sessions to establish semantics.

Exit: interrupt every command persistence boundary; repeat requests; reject stale revisions; replay committed facts after restart; prevent a second writer.

### M2 — One real persistent worker

Depends on M1. Deliver the session host, Codex adapter, native conversation persistence, streaming activity, permission requests, messages, pause/cancel/resume, and diagnostics. Use a disposable workspace without automatic publication.

Exit: real coding turn and continuation, supported steering, one-time permission answer, CLI reconnect, supervisor restart mid-turn, host failure recovery, and process cleanup on all supported OSes.

### M3 — Focused task to verified PR

Depends on M2. Deliver contracts/approval, task worktrees, candidates, deterministic checks, independent review, retained repair, PR publication/observation, and explicit merge. Implement review/evidence/PR inspection.

Exit: a real task reaches verified merge; supervisor-owned candidate snapshots exclude verification artifacts; an injected defect is repaired in the same session; bounded conflict resolution, lost replies, and target advancement cannot bypass gates.

### M4 — Continuous concurrency

Depends on M3. Deliver bounded scheduling, dependencies, ownership/conflicts, fairness, reviewer/check scheduling, provider backoff, and target integration reservations. Show queue reasons/utilization.

Exit: simulated utilization/refill targets; controlled barriers prove overlapping workers within the configured ceiling; a blocked task does not hold unrelated work; limits and reservations hold under races; model/network execution never holds shared mutation locks.

### M5 — Project orchestration and audit

Depends on M3; integrate with M4 before qualification. Deliver persistent orchestrator conversation, focused-task clarification, task-graph proposals, subset approval, contract revision, delegation, scope decisions, and bounded audits.

Exit: ambiguous outcome becomes approved tasks; independent work proceeds with matching shared-interface contracts or explicit prerequisites; scope changes preserve history; findings become ordinary bounded repair tasks.

### M6 — Recovery and accuracy qualification

Depends on M4 and M5. Basic recovery is already required in earlier milestones; this completes the matrix. Deliver fault injection, evidence reuse, seeded defects, scoped/full-review comparison, backup/restore, cleanup and security tests.

Exit: all critical/high required defect and failure scenarios pass; invocation-count assertions prove retries, reviews, and conflict repair stay within durable budgets across restart; uncertainty stays visible; no weakened review policy without evidence; cancellation/cleanup verified cross-platform.

### M7 — Complete CLI

Depends on M6. Deliver explicit Codex capability limits, stable JSON/NDJSON, completion, noninteractive decisions, diagnostics, pagination, exit codes, and complete usage examples.

Exit: conformance tests pass for the supported Codex integration; capability limitations are truthful; every required workflow can be performed through CLI without editing storage or requiring the TUI.

Additional harness support is a separate follow-up when required, using the existing conformance cases. Neither that work nor a portability claim is a prerequisite for M8 or M9.

### M8 — Throughput qualification and CLI release

Depends on M7. Deliver scheduler benchmarks, repeated live evaluation, fresh-install testing, wheel/sdist artifacts and checksums, uv installation/upgrade/uninstall smoke tests, registry/release publishing, a safe stopped-process upgrade procedure, new-format upgrade policy, complete CLI documentation, and limitations.

Exit: targets pass or have evidence-backed explicit revisions; accuracy gates stay intact; release artifacts pass cross-platform smoke tests. Ship the CLI foundation before beginning TUI implementation.

### M9 — Native terminal application

Begins only after M8 acceptance. Deliver the native terminal interface in the same Python distribution using Textual for widgets, layout, input, and asynchronous view updates. Pin compatible versions at M9 and include the TUI in the normal installation. Reuse the shared authenticated local API client; do not build a browser frontend or add a Node.js toolchain.

Exit: CLI/TUI parity, complete keyboard workflows, terminal resize/restoration, stream reconnect/replay, large-history handling, and stale-decision safety on Windows, macOS, and Linux. `brace` launches the TUI interactively; noninteractive commands remain stable. No TUI-owned orchestration state.

### Implementation work breakdown

| Work package | Deliverable | First milestone | Proof |
|---|---|---|---|
| Platform/build | Python package, uv install, version, CI matrix | M0 | Fresh-platform smoke |
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
| CLI | Automation, completion, Codex capability reporting | M7 | Codex conformance and CLI goldens |
| Performance/release | Benchmarks, packaging, docs | M8 | Reproducible release evidence |
| TUI | Terminal board, conversation, attention, evidence | M9 | Shared API/TUI and terminal acceptance |

Work packages may be developed independently after their contracts exist, but milestone acceptance follows the dependency order. More simultaneous implementation agents is not a substitute for a coherent vertical slice.

## 20. Terminal application specification

The TUI is the primary interactive product. The CLI supplies automation and the underlying operational foundation. Both ship in the same Python distribution installed with uv; the supervisor and session hosts remain independent of terminal lifetime.

Use [Textual](https://textual.textualize.io/guide/workers/) for terminal widgets, layout, input, rendering, and background UI operations. Use its supported APIs rather than writing a terminal renderer. Textual belongs in the normal package dependencies once the TUI ships. Add component dependencies only for controls actually needed; no custom UI framework, browser frontend, or JavaScript runtime.

### Launch and lifecycle

```text
brace                         # Open the TUI when stdin/stdout are terminals
brace tui                     # Explicit terminal application
brace tui --project ID        # Open a selected project
brace status --json           # Automation remains a normal CLI command
```

Before M9, bare `brace` prints CLI help. At M9, bare `brace` opens the TUI only when both stdin and stdout are terminals; otherwise it prints plain help without starting work. Explicit `brace tui` without a usable terminal exits with a concise diagnostic and points to CLI commands.

Opening the TUI may start the local supervisor, with visible connecting/starting state; it never starts agents merely by opening a project. Reopening attaches to current sessions and persisted events. Closing the TUI detaches; coding work continues under the supervisor. Cancelling a worker or stopping the supervisor is always an explicit separate action.

Restore terminal modes, cursor visibility, and alternate-screen state on normal exit, handled errors, and interrupts. A terminal disconnect must not cancel worker execution. Surface an actionable reconnect message if the supervisor disappears; reconnect from the last durable event cursor and resync when necessary.

### Layout

Wide terminals show project/task navigation on the left, selected content in the center, and contextual details when space permits. Narrow terminals show one pane at a time with clear navigation and a persistent project/session label. Never require a wide Kanban layout to operate the product.

The header shows project, connection health, active-agent capacity, and attention count. The footer shows context-sensitive keyboard actions. Detailed budgets, exact commits, and validation artifacts belong in detail views, not every row.

Provide stable selection by object ID while events update rows. Do not jump focus or reorder the user's selected item unexpectedly. Explicit sort/filter changes may reorder results; background updates must preserve the selection and scroll anchor where possible.

### Project overview

Show tasks grouped or filtered by queued, working, needs attention, in review, ready to merge, and completed. Rows/cards identify task, agent, active action, branch/PR, and precise waiting reason. Counts and display categories use the shared read projection.

Allow project selection, focused-task creation, plan inspection, priority changes, and worker navigation. In an empty installation, guide the user through registering a repository and checking readiness. Preserve unrelated dirty files and do not initialize or rewrite repositories merely to populate the screen.

### Worker detail

Tabs or focusable sections provide conversation, activity, diff, acceptance criteria, findings, checks, budgets, and PR state. Live conversation streams distinguish queued messages, harness-accepted input, active work, and final outcomes. The compose area supports multiline input and paste without automatically sending pasted text.

Provide send, capability-supported steer, pause, cancel, and resume. Show the affected worker and operation before cancellation. Scope-changing input enters the same contract proposal/approval flow as the CLI. Agent permission requests appear as explicit decision controls; the TUI never simulates approval by injecting text into an agent terminal.

Follow live output only while the user is at the end. Scrolling upward freezes that view and shows a new-output count. Paginate transcript history and cap rendered content; the terminal screen does not hold the full lifetime transcript in memory.

### Project orchestrator

A persistent conversation with plan proposals, dependency summaries, acceptance criteria, and contract diffs. Users can approve a subset of work or request revision. Approval clearly states the work and authority it grants; displaying a suggestion does not authorize execution.

### Attention queue

Collect permission requests, semantic questions, failed checks, exhausted budgets, unknown outcomes, and provider blockers. Every item identifies its project, worker, candidate/revision where relevant, reason, and permitted actions. A stale answer refreshes the decision instead of applying it elsewhere.

Do not steal input focus when attention arrives. Update the attention count and offer a navigation shortcut. Critical connection or persistence failure receives a visible banner while preserving unsent input.

### PR and evidence inspection

Show current head/base, required gates, observation freshness, findings, check results/artifacts, merge policy, and observed integration result. Provide a copyable provider URL; opening the user's browser is optional and is not an application dependency.

The supervisor revalidates merge authority and gates when the user submits a merge action. A formerly enabled control is not sufficient authorization after state changes. Rendering a diff or artifact never executes its contents.

### Keyboard and accessibility

- Arrow keys navigate lists; Tab/Shift+Tab move focus; Enter opens or activates the focused control; Escape returns or dismisses.
- `?` opens contextual help outside text input; shortcuts do not consume ordinary compose-field characters.
- `q` exits only from navigation mode. Ctrl+C requests client exit, never worker cancellation. Nonempty unsent input prompts discard/stay before ordinary exit; forced process termination cannot guarantee draft preservation.
- Multiline input uses an explicit Send control, plus a documented shortcut with a terminal-compatible fallback. Enter in the editor inserts a newline.
- Every operation is keyboard-accessible. Mouse support is optional; scrolling/clicking cannot be the only way to reach an action.
- Status uses text and symbols as well as color. Honor no-color/reduced-animation settings and offer ASCII rendering where glyph widths are unreliable.
- Test real terminal behavior for Unicode, wide/combining characters, bracketed paste, resize, 80-column layouts, and monochrome output. Degrade to a focused single-pane view on small terminals.
- Do not claim universal screen-reader accessibility for a full-screen terminal UI. Keep the plain CLI and stream output complete, test available assistive-terminal workflows, and document limitations.

### Event handling and performance

Textual screens/widgets store view state, not authoritative orchestration state. Use asynchronous workers and UI messages for API requests and streamed events. Blocking work uses a bounded thread worker when necessary; worker threads cannot mutate widgets directly. Git commands, model calls, and database work never run in the renderer or block keyboard processing.

Coalesce high-frequency repaint notifications without dropping durable decisions or action outcomes. Bound log buffers and event queues. Slow rendering cannot apply backpressure to scheduling. On overflow, reconnect/resync using the shared cursor contract rather than silently losing state.

### TUI acceptance

Test screens and interactions with injected API events, fake clocks, and Textual's headless `run_test`/Pilot facilities. Add real terminal integration tests for input, resize, signals, terminal restoration, and reconnect on Windows/macOS/Linux. Test concurrent CLI/TUI decisions, lost responses, stale revisions, slow consumers, and large histories. [Textual testing](https://textual.textualize.io/guide/testing/)

Required user journey: register repository, start approved work, inspect two concurrent workers, send feedback, answer a decision, cancel only one worker, detach/reopen, inspect a failed check and repair, and complete an authorized PR merge. The entire journey is possible without opening a web application or editing internal files.

### Packaging and distribution

Ship one Python distribution exposing the `brace` console command through `[project.scripts]`. The CLI, supervisor, internal session-host modes, and eventual Textual TUI are installed together. Use fresh `pyproject.toml`, `uv.lock`, `src/brace/`, and `uv_build` configuration. Build a wheel and source distribution with `uv build`; do not ship a frozen executable or reuse legacy packaging files. [uv build backend](https://docs.astral.sh/uv/concepts/build-backend/)

Use uv as the primary installer on Windows, macOS, and Linux. It creates an isolated tool environment and places the command on PATH; it can select/download a compatible Python when needed. Users install uv, Git, and their selected agent harnesses; they do not need Go, Rust, a compiler, or a manually managed virtual environment for supported wheel-based installations. [uv tools](https://docs.astral.sh/uv/guides/tools/), [uv Python management](https://docs.astral.sh/uv/concepts/python-versions/)

Reserve/verify a distribution name before publishing. The console command remains `brace` even if the registry distribution needs another name. Publish versioned wheels/sdists to the selected Python package registry and attach the same artifacts, checksums, and release notes to GitHub Releases. A version-pinned Git URL or release wheel is a supported installation source while registry publication is unavailable. All command examples below are templates until a rewritten release is published:

```text
uv tool install <distribution-name>
brace --help
brace version

# Local development in the fresh rewrite checkout
uv sync --locked
uv run brace --help
uv run python -m unittest discover -s tests
uv build

# Install a built release artifact into an isolated tool environment
uv tool install ./dist/<release-wheel>.whl
```

The lockfile makes the development/CI environment reproducible. Installed tools resolve the wheel's published dependency metadata; do not claim that a source `uv.lock` automatically fixes every dependency in a consumer installation. Pin/constraint dependencies deliberately and test a fresh installation from the actual artifacts, including all package resources and conditional platform dependencies.

### Upgrades and process lifetime

A uv-managed environment can be replaced during upgrade. Live Python processes may still lazily import modules from that environment, so supervisor-restart resilience is not permission to upgrade code underneath living hosts.

The supported initial update procedure is:

1. Persist active state and stop new dispatch.
2. Drain current turns, or explicitly cancel when the user chooses; resolve pending decisions or leave the upgrade pending rather than granting approval.
3. Shut down the supervisor and every owned host using the environment; verify they have stopped.
4. Back up the new-format database/artifact references.
5. Run `uv tool upgrade <distribution-name>` for registry installations, or reinstall the selected pinned Git/wheel release as appropriate.
6. Start the new supervisor, check schema/protocol compatibility, and reconcile retained conversations/workspaces before resuming approved work.

Document that directly invoking uv upgrade/uninstall cannot be intercepted by an already running app; callers must follow this procedure. A new client refuses unsafe interactions with incompatible living processes. Do not promise zero-downtime upgrades or use an old package to open a database it cannot read. Restore a compatible backup for rollback when required.

Use `uv tool install` for persistent supervisors and hosts. `uvx` cache environments are not a supported launch location for long-lived background work; reserve them for short-lived version/help/diagnostic use. The application must prevent persistent dispatch from unsupported transient installations, with detection/behavior proved in M0 packaging tests.

M8 tests install, upgrade, version pinning, uninstall, data preservation, artifact completeness, and PATH behavior on all supported operating systems. M9 adds Textual to the same package and installation path. Package-manager installation must require no manual virtualenv work or native compilation on supported release platforms. WinGet/Homebrew wrappers and custom self-update are deferred because uv already supplies the requested installation workflow.

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
| TUI delays core delivery | M8 CLI foundation release is a prerequisite for M9 |

Decisions confirmed by the user: full rewrite, no reuse/migration, CLI foundation before the interactive interface, a native TUI as the application, accuracy, maximum useful throughput, and a Python CLI packaged with uv. Keep the local supervisor; consider Go or Rust only for demonstrated capability/performance requirements.

M0 must settle exact Python/uv/dependency/Codex protocol versions, published distribution name, and supported process/isolation capabilities. Additional harness integration is deferred and cannot block CLI or TUI delivery. These technical checks do not reopen permission to reuse legacy code.

## 23. Repository analysis and retirement record

The repository was inspected to identify retirement scope, not to extract design requirements. It contains a Python command-line implementation, tests, bundled agent instructions/prompts/schemas/templates, packaging, release/site workflows, and a product website. None is a dependency of this specification.

Observed categories and their disposition:

| Existing category | Disposition |
|---|---|
| Runtime source and command handlers | Remove at M0; implement the new Python product from scratch |
| Tests and support fixtures | Remove at M0; derive new tests from this document |
| Agent prompts, instruction files, schemas | Remove at M0; author new contracts/context from scratch |
| Package configuration and lockfile | Remove at M0; author fresh Python project metadata and uv lockfile |
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
| CLI first, TUI application | Complete command/API foundation, then native terminal interface | M8 release before M9; terminal acceptance at M9 |
| See and steer agents | Live events, delivery states, messages/control | M2/M7 conformance and CLI scenarios |
| Slow runs/repeated reviews | Continuous scheduling, retained repair, evidence reuse | M4 utilization and M6 review experiment |
| Recovery | Host journal, durable intents, uncertain outcome handling | M6 fault matrix |
| Easier planning/focused work | Compact task contracts, proposals, subset approval | M3/M5 user scenarios |
| Accuracy | Layered gates, exact evidence, seeded defect corpus | M6/M8 correctness report |
| Maximum throughput | Useful-work metric, resources/fairness, integration concurrency | M8 benchmark report |
| Full rewrite/no reuse | Empty-tree M0, no imports, fresh tests/contracts | M0 source/dependency audit |
| Python/uv and local supervisor | One Python distribution, uv tool installation, local API, host modes | M0/M1 platform and installation tests |

Final checklist:

- [ ] Fresh uv tool installation/database; no legacy reads, imports, or resumed campaigns.
- [ ] CLI/TUI/supervisor/hosts launch through the installed Python environment; upgrade tests prove safe shutdown and resumption.
- [ ] Go/Rust components are introduced only for a demonstrated requirement, with the same conformance and recovery gates.
- [ ] No legacy code, tests, prompts, schemas, build rules, or guidelines reused.
- [ ] Focused task reaches verified integration entirely through CLI.
- [ ] Persistent orchestrator proposes and delegates approved task graphs.
- [ ] Individual sessions are observable and steerable with truthful delivery status.
- [ ] Independent tasks progress continuously under declared limits.
- [ ] Parallel workers use matching approved shared-interface contracts; unsettled interfaces become prerequisites.
- [ ] The supervisor owns candidate commits; checks/reviews inspect immutable snapshots and cannot silently add generated artifacts.
- [ ] Conflict resolution preserves exact input identities, required gates, and durable repair limits.
- [ ] Controlled concurrency and model invocation-count tests catch serialization, duplicate work, and budget resets.
- [ ] Required correctness and acceptance-review gates hold.
- [ ] Repairs preserve work and invalidate changed evidence correctly.
- [ ] Human/provider waiting does not monopolize model slots or cause repeated review.
- [ ] Client exit, supervisor restart, host death, and unknown external outcomes have tested behavior.
- [ ] Provider merge gates bind evidence to relevant candidate/integration identities.
- [ ] Cancellation and cleanup cannot affect unrelated processes/resources.
- [ ] Accuracy and performance reports are reproducible and state limitations.
- [ ] CLI foundation release is accepted before TUI implementation.
- [ ] Codex satisfies the supported capability contract; a second harness is not required for CLI or TUI release.
- [ ] Native TUI calls the same operations and enforces the same rules as CLI.

When implementation is requested, begin at M0 with this document and fresh files. Do not port or incrementally refactor the old product.
