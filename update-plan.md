# Brace Update Plan: High-Throughput, Finite Orchestration

## 1. Decision and outcome

Brace remains a repository-local, uv-packaged Python 3.11+ CLI with three stages: planning, build, and audit/bug-fix. Retain the unreleased branch's exact-SHA, worktree, provider, amendment, recovery, and closure-audit safeguards, but replace wave barriers and restart loops with a small forward-only coordinator.

The governing invariant is:

> A changed candidate may require bounded verification, but it must never create a new review session, reset a budget, or return to initial review.

The target behavior is:

- One durable review session per task or bug.
- Two scoped adversarial reviews once per initial candidate.
- One PM triage when those reviews report findings.
- One shared finite repair budget and delta-only verification after repairs.
- Mechanical base refresh instead of rebuilding correct candidates.
- One bounded global agent pool with work-conserving scheduling.
- Serialized state, shared Git mutation, publication, and merge operations.
- Campaign-wide audit limits that survive restarts.
- Whole-project reviews only in the audit stage.
- Internal review before publishing task or bug branches.
- No service, database, orchestration framework, agent SDK, general validation cache, or new production dependency.

For `N` independent successful assignments:

- With no base movement: `N` implementation calls plus `2N` initial reviews.
- With serialized merges advancing the base: at most `N-1` additional integration-verifier calls.
- Harmless base movement causes no builder, fixer, PM-triage, or full-review call.

For `N=3`, this is at most 11 model calls instead of the currently possible 18, before genuine defect repair. This is a structural bound, not a claim of measured end-to-end speedup.

## 2. Current-state reconciliation

### Verified starting state

- Released `main`: `ff5350c59a5dedc406549dc4140e524939dd500d`, Brace v0.2.8.
- Unreleased `integration/review-integrity-roadmap`: `fcb97b43aa895bac120c84bc5fcd5e6254e6732f`, 33 commits above `main`.
- PRs #42-#50 supplied useful CI, review-record, dual-review, closure, status, concurrency, and provider-integrity work.
- The same branch contains stale-review amplification, serial candidate review, per-invocation audit restarts, and redundant final reviews that this plan supersedes.
- Draft PR #52 is not mergeable: remote head `3a046bf2788788c2625b35747871f65af21ece9f`; Ubuntu failed from a temporary-repository teardown race and Windows was cancelled.
- Local `task/0051-test-performance` contains unpushed commit `16ee31492b9a8a4b6fcf6a9aff76bb6de913d41c`.
- Local `fix/0038-007-baseline-schema-upgrade` remains at rejected candidate `a2c964048c49936a42f94166d6f57d99bb0a1b54`.
- `tasks.md`, `bugs.md`, `.codex/issue-51-body.md`, branches, and registered worktrees are coordinator evidence and must be preserved.

### Reconciliation procedure

Before runtime implementation:

1. Keep automatic review, repair, publication, and cleanup stopped.
2. Record and verify every branch, worktree, detached-review SHA, PR, remote head, and local-only commit.
3. Commit this file through an isolated worktree and PR targeting the existing integration branch.
4. Replace the GitHub issue contracts described below before assigning new implementation work.
5. Append a reconciliation entry to coordinator-owned `tasks.md` and `bugs.md`; do not rewrite history.
6. Continue from `fcb97b4` on `integration/review-integrity-roadmap`; do not merge it to `main` until this plan passes.
7. Close PR #52 as superseded without deleting its remote branch, local branch, or worktree.
8. Do not cherry-pick the PR #52 stack wholesale. Port only generic fixture/teardown improvements after the new state-machine tests exist. Rewrite tests enforcing stale requeue, fresh full re-review, wave barriers, per-run audit reset, or final dual review.
9. Do not merge `a2c9640`. Carry its released-baseline test into generalized migration work and replace tracked-schema overwrites with recognized-version bundled-schema fallback.
10. Preserve historical branches/worktrees until useful changes and tests are ported. Cleanup must use existing validated coordinator paths only after every preserved SHA is reachable from a named branch.
11. Keep #25, #28, #29, and #37-#41 open until the final integration head reaches `main` and the release is verified.

### Existing task and bug disposition

| Existing work | Disposition |
|---|---|
| `TASK-PERF` | Defer until runtime semantics stabilize; port only still-valid fixture and teardown improvements. |
| `TASK-0029-UTILIZATION` | Supersede with the global work-conserving scheduler. |
| `TASK-0039-PROMPTS` | Use the existing verifier role with dynamic code/test scope; do not add another permanent role. |
| `TASK-0039-INVOCATION-ISOLATION` | Retain exact-head isolation and conditional detached-base worktrees. |
| `TASK-0039-SCOPE` | Retain with explicit read, blocking, and edit scopes. |
| `TASK-0039-VALIDATION` | Supersede early validation-PR publication; review before publication. |
| `TASK-0039-WAVES` | Supersede waves with independently scheduled reviewer actions. |
| `TASK-0040-AUDIT-APPEND` | Retain append-only finding history and exact audit evidence. |
| `TASK-0040-ROUNDS` | Replace per-invocation limits with a durable campaign limit. |
| `TASK-0040-VALIDATION` | Retain sandboxed exact-tree final validation. |
| `TASK-0040-FINAL-WAVE` | Remove final unrestricted reviews; retain audit, validation, CI, and merge gates. |
| `TASK-0025-DOCS` | Rewrite for this workflow. |
| Previously integrated tasks | Preserve as history and reverify retained safety behavior before release. |

Carry forward documentation, invocation/isolation, bug-disposition, detached-base, concurrency, audit-bound, exact-validation, migration, shallow-checkout, teardown, recovery, worktree, and evidence-retention defects. Supersede only findings whose sole requirement was fresh full reviews, stale-base rebuilding, per-run audit reset, final dual review, or early unreviewed PRs.

Specific local findings:

- Retain and rewrite `BUG-0051-001`, `002`, `005`, `006`, `007`, `008`, and `011` for the new transitions.
- Retain fixer-concurrency coverage from `BUG-0051-003`; remove stale-requeue assertions.
- Resolve `BUG-0051-004` and `009` by deleting the unnecessary executable-resolution cache.
- Replace `BUG-0051-010`'s mocked stale requeue with a real mechanical-refresh boundary test.
- Resolve `BUG-0039-010` through `013` through verifier reuse, complete bug context, conditional base worktrees, and shared scheduling.
- Resolve `BUG-0040-016` and `017` through finite audits and exact-tree validation.
- Supersede `BUG-0040-015`'s prohibition on schema changes but retain its compatibility concern in migration tests.
- Retain `BUG-0037-001`, `BUG-0038-007`, and `BUG-0038-008` as migration and shallow-checkout requirements.

## 3. Coordinator and scheduling

Reuse the existing Python modules and standard library:

- One coordinator event loop and one `ThreadPoolExecutor` using `wait(..., FIRST_COMPLETED)`.
- One shared `advance_assignment()` implementation for task and bug lifecycles.
- Each future is one atomic implementation, review, triage, repair, verification, audit, final-validation, or provider-observation action.
- No nested executors, action framework, plugin interface, event bus, or custom scheduler.

The coordinator is the only `.codex/*.json` writer. It creates/removes worktrees, applies results, performs local ref changes, mechanical merges, pushes, and serialized integration merges. It never runs arbitrary project commands or invents semantic changes. Agents may commit only in their assigned worktrees and return immutable results.

For `maximumConcurrentAgents = M`:

- At most `M` agent processes run at once.
- Executor capacity is `2M`.
- At most `M` short provider observations are in flight; they do not acquire agent permits.
- The coordinator never blocks on provider polling.

Dispatch priority is delta verification, repair, integration verification, initial review, new implementation, then due provider observation. A fast candidate begins review without waiting for sibling builders.

Reserve `allowedPaths` and `exclusiveResources` from assignment dispatch until merge, no-change resolution, approved supersession, safe discard, or terminal cleanup. Builder or reviewer completion does not release the reservation. Shared Git mutation and merges remain serialized.

## 4. Configuration and budgets

New workflow schema `1.1` adds:

```json
{
  "maximumConcurrentAgents": 3,
  "maximumTaskAttempts": 3,
  "maximumBugAttempts": 3,
  "maximumRepairRounds": 2,
  "maximumAuditRounds": 3,
  "maximumProviderAttempts": 3,
  "providerCheckTimeoutMinutes": 60
}
```

Retain existing planning, amendment, agent-timeout, cleanup, provider, remote, target, and worktree settings. New installations default to three agents. Existing workflow `1.0` remains readable without rewriting: derive the global ceiling from the larger builder/fixer ceiling and use the new finite defaults when absent. Remove role-specific ceilings from new templates.

Use fixed constants rather than more configuration:

- Two malformed-result replacement calls per review or audit campaign.
- Fifteen-second provider polling interval.
- Two-minute timeout for each provider command.

Persist every counter and absolute deadline before launch. Crash, timeout, malformed output, or cancellation consumes the reservation. Restart never restores capacity.

```text
reviewCallLimit = 2 initial reviews + 1 triage + 2 * repair rounds + 2 replacements
```

With two repairs, the limit is nine. Audit defaults are three semantic rounds and five total auditor calls. Integration-verifier calls and provider publish/refresh cycles are separately bounded by `maximumProviderAttempts`.

## 5. Assignment state and exact identities

Use forward-only phases:

```text
pending -> implementing -> initial-review -> triage -> repair-N
-> delta-verification-N -> internally-approved -> integration-refresh
-> integration-validation -> publish-pending -> provider-pending
-> merge-ready -> merged/resolved
```

`needs-user`, `blocked`, and `superseded` are terminal outcomes for an invocation. `internally-approved` is not terminal. No path returns to initial review or triage.

Persist:

- `reviewSessionId`: stable for the assignment lifetime.
- `reviewBaseSha`: base used by both initial reviewers.
- `initialCandidateSha`: exact initial reviewed candidate.
- `approvedHeadSha`: current head approved by the review/delta chain.
- `integrationBaseSha`: latest integration head used for refresh.
- `publishedHeadSha` and `publishedTreeId`: exact provider inputs.
- `activeAction`: reserved action identity, phase, inputs, call number, and timestamp.

A repair changes `approvedHeadSha`, not `reviewSessionId`. Every result binds its session, phase, input SHA, output SHA where applicable, and reserved call. Final approval is the immutable chain from initial review through every delta verification.

## 6. Review and repair scope

Every review has three boundaries:

- **Read scope:** enough callers, siblings, tests, configuration, and surrounding code to understand the assignment or defect class.
- **Blocking scope:** the assignment contract, its direct consequences, and candidate-caused regressions.
- **Edit scope:** immutable `allowedPaths`; reviewers remain read-only.

An unrelated pre-existing defect cannot block a candidate or create repair work. Preserve it as nonblocking audit input if useful. A candidate-caused problem outside `allowedPaths` is a scope conflict handled through the existing user-approved PM amendment workflow.

Each initial task or bug candidate gets two independent verifier invocations at the same base/head:

- The code adversary reviews the contract, diff, owning layer, relevant callers/sibling variants, error paths, and candidate-caused regressions. It does not run a whole-project audit or duplicate the full suite.
- The test adversary reviews acceptance criteria, production-path coverage, negative cases, and applicable variants, and runs the declared checks in its sandbox. It creates an exact detached-base worktree only when red/green or differential proof requires it.

Bug reviewers also receive the claimed `fixed` or `not_reproducible` outcome, original reproduction, required behavior, defect class, audit SHA, and acceptance test. They determine whether the recorded defect class is resolved; they do not hunt unrelated project defects.

When both initial reviews are clean, skip triage. Otherwise:

1. Complete both independent reports.
2. Deduplicate only identical reproduced defects.
3. Invoke PM triage once with the complete batch.
4. Reject speculative, stylistic, unsupported, and unrelated findings.
5. Give one repair worker all accepted blockers.
6. Consume one repair round.
7. Run required sandboxed checks and one delta verifier.

The delta verifier sees only the original assignment, accepted blockers, previous/new SHAs, repair diff, acceptance criteria, and required checks. It can block an unresolved accepted finding, repair-introduced defect, or scope violation. It cannot reopen initial review/triage or block on an unrelated pre-existing defect. Exhaustion produces `needs-user`.

## 7. Base advancement, publication, and providers

Base advancement alone never invalidates initial review. Before publication:

1. Fetch and authenticate the latest integration head.
2. Require it to descend from `reviewBaseSha`; rewritten history blocks.
3. Confirm assignment head/worktree identities and resource reservations.
4. Mechanically merge the latest integration head into the assignment branch without manual resolution.
5. Record exact parents and the result tree.
6. Route conflicts through the existing repair budget.
7. Run one sandboxed integration verifier against the exact refreshed head.
8. Publish only after it passes.

The integration verifier checks the mechanical refresh, interactions with newly integrated changes, assignment checks, and targeted cross-assignment behavior. It is not a whole-project audit and cannot reopen initial review.

Repeated base movement is handled by another fast-forwarding mechanical merge and bounded integration verification. It consumes a provider attempt but no builder, fixer, triage, or full-review call. Never force-push.

Do not push task/bug branches or open PRs until internal review and integration verification pass. Provider CI for one candidate may overlap internal work on other candidates; do not claim same-candidate review/CI overlap.

Provider observations return `passed`, `failed`, `pending`, `identity-drift`, `approval-required`, or `infrastructure-error`. Pending checks are observed until the persisted deadline. Network failure, timeout, cancellation, or human approval enters `waiting-provider`/`needs-user` without invalidating approval or triggering AI repair. A concrete code/test failure with captured evidence consumes the existing repair budget.

Immediately before merge, revalidate provider, repository, PR, source/base refs, exact head/base SHAs, policies, checks, and approvals. GitHub uses `gh pr merge --match-head-commit`. After merge, fetch and require remote containment, expected first parent, and a tree equal to `publishedTreeId`.

## 8. Finite audit and closure

Only the audit stage performs a whole-project review. For root-scoped Brace it covers all tracked source, tests, packaging, CLI, Git/worktree safety, providers, tracked prompts/schemas/configuration, documentation, CI/workflows, cross-platform interactions, requirements, and approved plan at one frozen integration tree.

It excludes `.git`, ignored mutable `.codex/*.json`, external worktrees, caches, temporary files, untracked coordinator notes, and sibling repositories.

```text
freeze exact integration SHA/tree
-> reserve round and auditor call
-> whole-project audit
   -> clean: final sandboxed validation -> exact project PR/provider merge
   -> findings: persist batch -> PM triage once -> parallel scoped bug fixes
                -> scoped reviews/repairs -> serialized merges -> next audit round
```

No whole-project audit runs after each task, individual bug fix, repair, provider retry, or merge attempt. One closure audit covers the complete preceding fix batch.

`maximumAuditRounds` is campaign-wide. Reserve a semantic round before its first auditor call. A malformed/crashed/timed-out auditor can use the shared two-call replacement allowance against the same frozen tree without creating another semantic round. Restarts, amendments, or returns to build never reset history.

If the final permitted round reports findings:

1. Persist them.
2. Do not triage or fix them.
3. Set `awaiting_user`.
4. Interactively ask whether to add rounds and, if approved, ask for 1-32.
5. Persist `previousLimit`, `additionalRounds`, `newLimit`, and `approvedAt` before triage/fixing.
6. If declined, exit without code changes.
7. Noninteractive execution persists the state and exits with a rerun instruction.

No PM agent is needed for a numeric budget extension.

Persist audited commit and tree IDs. Only a source-tree change invalidates a clean audit: bug/amendment merge, tree-changing reconciliation, manual integration movement, or tree-changing target refresh. Restart, provider/network/approval/status/cleanup failure, or metadata-only movement with the same tree does not.

After a clean audit, run sandboxed project-wide deterministic validation at the same tree. Do not run another unrestricted dual review. A final-validation code defect becomes audit evidence and requires closure capacity before fixing. If target `main` advances and changes the effective tree, refresh and consume another audit round; rewritten/unowned history blocks.

## 9. Schemas, migration, and recovery

Use next sequential versions:

- Workflow `1.1`.
- State `1.3`.
- Tasks `1.2`.
- Bugs `1.4`.
- Review record `1.1`.
- Closure record `1.1`.

Issue #24 must stop reserving literal future schema versions and increment from whatever exists when it is eventually implemented.

Task/bug records own phase, counters, SHAs, active action, PR, and terminal reason. Global state owns the three-stage state, integration/target identities, audit campaign, audit extensions, final evidence, and active PM amendment. Immutable outputs remain in `.codex/results/`; do not introduce another mutable source of truth.

Compatibility rules:

- Read released and integration-era schemas.
- Migrate ignored JSON atomically without resetting attempts, reviews, repairs, audits, provider attempts, or amendment history.
- Save one immutable pre-migration snapshot under `.codex/results/`.
- Use the bundled current schema without rewriting a tracked project schema only when the older file matches a recognized bundled hash.
- Fail closed for customized or unknown tracked schemas/prompts.
- Do not add an upgrade command.
- Map old `baseSha`/`resultSha` to the new identities based on the recovered phase.
- Preserve exact valid reviews; missing/mismatched/malformed reviews consume already bounded replacement calls.
- Convert prior repeated reviews into one finite session history rather than granting fresh capacity.

Persistence order:

1. Persist phase, action ID, and reserved counter.
2. Launch.
3. Atomically write the immutable validated result.
4. Advance the assignment ledger and clear the active action.
5. Update global state last when integration/audit state changes.

Recovery reconciles the ledger, immutable result, worktree HEAD, local/remote branch, exact PR identity, provider merge state, and integration/target containment. It reuses exact evidence or consumes the already reserved attempt; it never duplicates calls, commits, pushes, PRs, or merges.

## 10. GitHub issue amendments

Replace issue bodies instead of adding contradictory comments/addenda. Each issue links to the exact committed version of this file and owns only its slice.

| Issue | Amendment |
|---|---|
| #25 | Rename to **High-throughput finite review and audit orchestration**. Make this the umbrella contract; remove wave barriers, early validation PRs, fresh reviews after every SHA, per-run audit reset, final dual review, and no-new-schema restrictions. |
| #38 | Rename to **Persist review sessions, action budgets, and exact-SHA evidence**. Own identities, reservations, schemas, migration, write ordering, and recovery. |
| #39 | Rename to **Scoped dual adversarial review with finite repair sessions**. Own two initial reviews once, verifier reuse, triage, repairs, delta verification, detached-base proof, and cross-candidate scheduling. |
| #40 | Rename to **Campaign-bounded closure audits and exact-tree final validation**. Own campaign rounds/calls, exhaustion prompt, causal invalidation, append-only findings, and removal of final dual review. |
| #41 | Rename to **Exact-head merge protection with mechanical base refresh**. Own reviewed/approved/published identities, refresh, bounded provider cycles, review-before-publish, match-head merge, expected tree, and containment. |
| #29 | Rename to **Global work-conserving agent concurrency**. Replace builder/fixer ceilings and largest-wave scheduling with one global ceiling and atomic actions. |
| #28 | Add phases, queues, utilization, reservations, calls, repairs, audit rounds, deadlines, exact identities, terminal reason, and safe next command to coherent read-only status. |
| #37 | Retain Ubuntu/Windows CI; add shallow-checkout migration, exact-head, cleanup, and state-machine coverage. |
| #51 | Keep its test-runtime objective but measure after semantics stabilize. Replace PR #52 and remove tests for superseded behavior without weakening safety coverage. |
| #24 | Keep deferred. Remove reserved schema versions and build later from the then-current coordinator model. |

Issues remain open through exact-head integration-to-main verification. Do not claim an empirical speedup before comparative runs.

## 11. Implementation order

1. **Contracts and preservation:** commit this file, amend issues/ledgers, close but preserve PR #52, and freeze old loops.
2. **State foundation:** add schemas, phases, identities, reservations, migration, immutable evidence, and crash recovery.
3. **Finite scoped review:** reuse verifier contexts, add one review session, one triage, repair budgets, and delta verification; remove obsolete reviewer machinery when compatibility permits.
4. **Linear reconciliation/provider flow:** remove stale rebuilding, add mechanical refresh/integration verification, review-before-publish, bounded provider polling, and exact tree verification.
5. **Work-conserving scheduling:** replace build/fixer barriers and nested review pools; preserve lifecycle resource reservations and serialized Git mutation.
6. **Finite audit:** replace `_restart`/`while True`, add campaign limits/extensions and exact-tree validity, and remove final dual reviews.
7. **Status:** expose phases, queues, utilization, budgets, identities, deadlines, waiting states, and next action.
8. **Performance/docs/release:** port valid #52 fixture work, delete the executable cache, fix teardown, rewrite tests/docs, benchmark, pass both CI platforms, and merge/release.

Every phase lands through isolated worktrees and provider PRs into the integration branch. Do not begin the next phase while the preceding exact head is red.

## 12. Acceptance tests

### Liveness and scope

- Always-rejecting verification stops exactly at the repair limit.
- Changing reviewer findings cannot create new sessions or triage cycles.
- One hundred restarts restore no capacity.
- Crashes, timeouts, malformed output, and cancellation consume reservations.
- A perpetual auditor stops at the campaign limit.
- Declining extension preserves findings and starts no fix; approving `N` persists first.
- Task/bug reviewers cannot block on unrelated pre-existing defects.
- Bug review covers the defect class, relevant callers/variants, reproduction, and acceptance criteria.
- Delta review is limited to accepted blockers and repair-caused behavior.
- Only the audit-stage auditor can add unrelated whole-project findings.

### Throughput and scheduling

- Three independent tasks use three builders and six initial reviews.
- Serial base advancement causes zero rebuilds and zero full re-reviews.
- Three candidates require at most two merge-driven integration-verifier calls.
- Reviews overlap across assignments; fast candidates do not wait for slow builders.
- Pool size one cannot deadlock.
- Eligible work occupies available agent permits.
- Provider waits consume no agent permit.
- Conflicting resources never overlap across lifecycle phases.
- Concurrency tests use barriers/events, not wall-clock races.

### Trust, Git, and providers

- The coordinator never runs a project validation command.
- Commands run only in sandboxed exact-head worktrees.
- Worktree HEAD, cleanliness, and allowed paths are verified around each agent.
- Reviewers remain independent; detached bases are created only when required.
- Mechanical refresh records exact ancestry/tree; conflicts cannot be classified harmless.
- Movement between refresh and merge is boundedly refreshed or refused.
- PR identity drift always blocks.
- GitHub uses the approved exact head; Azure source/policies are reread after waiting.
- Provider squash results have expected first parent/tree and remote containment.
- Infrastructure failure never triggers repair or invalidates a clean audit.
- Cancellation terminates process trees and preserves recoverable state.

### Audit, migration, and recovery

- Whole-project auditing covers the complete tracked project at one frozen tree and does not run after each fix.
- One closure round covers the preceding complete fix batch.
- Audit history survives amendments and returns to build.
- Metadata-only movement preserves a clean tree; tree changes invalidate it.
- Final sandboxed validation runs every declared command at the audited tree and blocks publication on failure.
- No final dual-review loop remains.
- v0.2.8 and integration-era workflows migrate without resetting counters.
- Recognized old schemas use safe bundled fallback; customized schemas fail closed.
- A crash after every persistence boundary duplicates nothing.
- Shallow CI does not depend on unavailable history.
- Ubuntu and Windows CI pass on the exact final head.

Required final checks:

```text
uv run python -m compileall -q src tests
uv run python -m unittest discover -s tests -p test_core.py -v
uv run python -m unittest discover -s tests -p test_cli.py -v
uv run python -m unittest discover -s tests -p test_safety.py -v
uv run python -m unittest discover -s tests -p test_workflow.py -v
uv run python -m unittest discover -s tests -v
uv run brace --help
uv build --no-sources
```

Release also requires no tracked generated state, credentials, machine paths, temporary repositories, or worktrees; disposable GitHub dogfooding; mocked Azure boundaries and an authorized live smoke test when available; exact remote containment; and documentation/Pages verification.

## 13. Measurement, quality guard, and exclusions

Use existing timestamps and ledgers to record stage wall time, agent active/queue time, calls by action, repair rounds, refreshes, provider wait, audit rounds, and integrated assignments. Do not add a metrics subsystem.

Compare old/new coordinators on identical hermetic campaigns and, when available, identical live campaigns. One delta verifier's quality versus two fresh reviewers is not yet established. Seed repair-local P0/P1 defects before release. If one delta verifier misses a seeded P0/P1 that the old dual repair review catches, use two concurrent delta verifiers per repair and increase the finite call formula; never restore recursive full-review sessions.

Explicitly excluded:

- Temporal, LangGraph, Celery, OpenAI Agents SDK, or another orchestration framework.
- Service, daemon, database, dashboard, registry, or new production dependency.
- General validation caching, nested pools, reviewer debate/voting, or recursive review campaigns.
- Early unreviewed PRs, whole-project audits outside audit, automatic amendments/extensions, and force pushes.
- Multi-project orchestration from #24.

The design uses Relay's demonstrated forward-only budgets, Step Functions-style finite retry exits, GitHub Actions-style bounded concurrency/timeouts, Terraform-like immutable identities, explicit AI turn limits, and Aider-like responsibility separation. These are design precedents, not proof of speed. Brace's release claim remains limited to what its tests and comparative measurements establish.

## CLI, automation, completion, and terminal UX

This section completes the public CLI contract without changing the orchestration architecture above. Brace remains an `argparse` CLI using the already-installed Rich dependency. Do not add a CLI framework, TUI framework, completion dependency, daemon, service, or compiled component.

### Verified current surface

Released Brace exposes `init`, `plan`, `build`, and `audit`. The unreleased integration branch adds `status`. The parser uses `argparse`; the runtime dependencies are `jsonschema` and `rich`. Rich already supplies prompts, tables, progress rendering, and error presentation. Brace has no tab-completion implementation.

### Target command surface

```text
brace --help
brace --version
brace [--no-input] [--no-color] [--no-progress] [--verbose] init ...
brace [--no-input] [--no-color] [--no-progress] [--verbose] plan [repository] [--start-new-workflow]
brace [--no-input] [--no-color] [--no-progress] [--verbose] build [repository]
brace [--no-input] [--no-color] [--no-progress] [--verbose] audit [repository]
brace [--no-color] [--verbose] status [repository] [--output human|json]
brace [--no-color] [--verbose] doctor [repository] [--output human|json]
brace completion {bash,zsh,fish,powershell}
```

Planning, build, and audit remain the only workflow-mutating stages. Rerunning the applicable stage command resumes durable state. Do not expose public `review`, `repair`, `merge`, `resume`, `provider`, `worker`, or `audit-round` commands; they would expose internal transitions and create unsupported recovery paths.

Do not add `brace run`, `brace config`, `brace logs`, generic `--dry-run`, `--yes`, plugins, telemetry, a daemon, a dashboard, or self-update behavior without an observed requirement.

### Quiet terminal UX

Default output is quiet and aggregate. An interactive command shows one transient Rich `Status` line while work is active, followed by one compact result. It does not show a live table or print task-by-task events during successful operation.

The coordinator alone updates the status text when the aggregate phase or counts change. The status line may show project name, current major phase, integrated/fixed count, active count, provider wait, and audit round. It must not expose every implementation, review, retry, poll, or heartbeat.

Stop the status before prompts, errors, waiting outcomes, or the final summary. Transient progress writes to stderr; final human results write normally after the status is cleared. Brace disables animation when stderr is not an interactive terminal, and `--no-progress` disables it explicitly. Keep the status to one terminal row and truncate the least important right-hand detail with an ellipsis when the available width is too small; preserve the command phase and project name.

Default output uses short sentences rather than dashboards, boxes, tables, or event labels. Color reinforces rather than carries meaning. Exact identities, assignment phases, calls, budgets, worktrees, PRs, and deadlines remain available through `brace status`, `--verbose`, and JSON.

The following examples define information hierarchy, not literal repository values. Render only facts supported by validated state.

#### `brace init`

`init` prints one result and next action:

```text
Initialized Example in C:\Code\Projects\Example

Next: edit requirements.md, then run brace plan
```

Do not print a success checklist unless `--verbose` is supplied. For an existing repository, describe installation rather than creation. On partial failure, report only observed state:

```text
Initialization stopped

GitHub CLI authentication is required.
Local work was preserved.

Fix:    gh auth login
Resume: brace init
```

Any preservation or safe-rerun claim must be derived from recovery checks. Never print credentials, full provider responses, or irrelevant implementation details.

#### `brace plan`

Planning uses one transient status:

```text
⠋ Planning Example
```

On success:

```text
Planned Example in 42s

8 tasks · 5 parallelizable · 2 required checks
Next: review plan.md, then run brace build
```

Do not add a second approval prompt to initial planning: separate `plan` and `build` commands are the review boundary. A PM amendment remains an exception: before approval it must show added follow-up tasks, superseded untouched tasks, changed constraints, and unchanged integrated work.

#### `brace build`

`build` changes one aggregate status line as the campaign advances:

```text
⠋ Building Example · 3/8 integrated · 4 active
```

```text
⠋ Reviewing Example · 6/8 integrated · 2 active
```

```text
⠋ Waiting for provider checks · 7/8 integrated
```

Do not print per-task success lines in default output. On success:

```text
Built Example in 21m 18s

8 tasks integrated · 24 agent calls · 3 repairs
Next: brace audit
```

When operator action is required, clear the status and show only the actionable blocker:

```text
Build needs attention

task-006 exhausted its repair budget.
No approved work was discarded.

Inspect: brace status
Resume:  brace build
```

Never report `Built` while required work remains unintegrated.

#### `brace audit`

`audit` uses the same single status line and changes it only between major phases:

```text
⠋ Auditing Example · whole project · round 2/3
```

```text
⠋ Repairing 2 accepted blockers · round 2/3
```

```text
⠋ Verifying closure · round 3/3
```

Default output does not show each bug or reviewer. The durable state and verbose/status output preserve these scope distinctions:

- Whole-project adversarial inspection occurs only once per audit round.
- Initial bug review examines the bug contract, proposed change, affected behavior, and assigned scope.
- Repair verification examines the accepted finding, repair diff, affected behavior, and required checks.
- A bug review or repair verifier does not perform another whole-project audit.
- After the bounded fix batch merges, the next audit round examines the new frozen whole-project integration tree.

On success:

```text
Completed Example in 28m 09s

3 audit rounds · 4 bugs fixed · final merge verified
```

If the final permitted round has findings, clear the status before prompting:

```text
Audit needs attention

The final permitted round found 3 issues.
Findings were preserved; no repairs were started.

Add audit rounds? [y/N]
```

If the user declines, preserve findings and make no code change. Under `--no-input`, persist `awaiting_user`, return exit code 3, and provide the exact rerun instruction. Report `Completed` only after clean audit, same-tree validation, exact provider merge verification, containment, and required cleanup.

#### `brace status`

Default status is a concise, static snapshot:

```text
Example

Build · running
3/8 tasks integrated · 4 active · 1 waiting

Needs attention
  task-006  Repair budget exhausted

Next: brace build
```

`brace --verbose status` shows the full assignment table, calls, budgets, exact SHAs, worktrees, PRs, and deadlines. `status --output json` remains the complete automation interface.

#### `brace doctor`

When all checks pass:

```text
Environment ready.
```

Default failure output shows only problems:

```text
Doctor found 2 problems

FAIL  GitHub CLI is not authenticated
WARN  Provider approval is still pending

Fix: gh auth login
```

`brace --verbose doctor` also shows passing checks.

#### Verbose and other output

`--verbose` moves detailed orchestration out of the default experience:

```text
task-003  implementation started
task-003  candidate validated at 72ce98d1
task-003  initial review started · calls 2/9
task-003  repair 1/2 started
task-003  delta verification passed
task-003  provider checks pending
task-003  merged at 903dc021
```

Verbose output is diagnostic, not another durable event ledger. `completion` writes only its raw shell script. `--output json` never mixes human text into stdout. Every expected error states the actionable problem, durable preservation status when relevant, and the inspect/fix/resume command; omit details that do not truthfully apply.

### Plain and machine-readable output

When stderr is not a TTY, or `--no-progress` is supplied, workflow commands emit no transient status, cursor control, or animation. Plain output contains major aggregate phase changes, actionable waiting/failure output, and the final result; task-level transitions appear only with `--verbose`.

If no major phase, warning, prompt, or terminal result has been printed for five minutes, plain workflow output emits one timestamped aggregate heartbeat and restarts the silence interval. A state change may update the aggregate counts shown by the next heartbeat, but provider polls and individual agent heartbeats never produce their own lines. Five minutes is a fixed initial constant, not another workflow setting.

```text
12:10:00  Build running · 3/8 integrated · 4 active
12:15:00  Build running · 3/8 integrated · 4 active
```

`--no-color` controls color only; it does not select plain mode. `--no-progress` is the explicit interactive override for stable terminal history.

Do not add newline-delimited JSON events to `plan`, `build`, or `audit` in this update. Automation invokes a stage, reads its exit code, then calls `status --output json` for a coherent snapshot.

Structured output is limited to `brace status [repository] --output human|json` and `brace doctor [repository] --output human|json`.

JSON output emits one valid document on stdout with no Rich or ANSI decoration, uses a versioned public schema independent of internal `.codex/*.json` schemas, and sends diagnostics only to stderr. It reports exact stage, phase, commit and tree identities, queue and active actions, budgets, provider state, deadlines, blocker, terminal reason, and safe next command. It excludes credentials, provider tokens, raw prompts, raw agent output, and unrestricted logs. Human output is not a parsing contract.

### Rendering implementation and precedents

Use Rich `Status`, not `Live` or `Progress`, because the default UI represents one aggregate command state:

```python
progress_console = Console(stderr=True)

with progress_console.status("Building Example · 0/8 integrated") as status:
    for transition in coordinator:
        status.update(render_aggregate_status(snapshot))
```

Only the coordinator updates the status. Stop it before prompts, errors, waiting outcomes, or final output. Do not add a rendering thread, event bus, task display registry, or another dependency.

This follows the restrained behavior, not the complete feature sets, of established tools:

- [uv](https://docs.astral.sh/uv/reference/cli/) keeps normal output compact and exposes no-progress and verbose modes.
- [Cargo](https://doc.rust-lang.org/cargo/reference/config.html#term) separates automatic terminal progress, quiet output, and verbose output.
- [Docker BuildKit](https://docs.docker.com/reference/cli/docker/buildx/build/#set-type-of-progress-output---progress) selects interactive TTY progress or sequential plain output.
- [Pulumi](https://www.pulumi.com/docs/iac/cli/commands/pulumi_up) provides controls to suppress periodic progress and streamed logs.

These are presentation precedents, not evidence that their full output systems or dependencies belong in Brace.

### Noninteractive behavior and exit codes

Global `--no-input` never prompts or infers approval. It persists `awaiting_user` before exiting when planning information, a semantic decision, or additional audit capacity is required; persists `waiting_provider` for external state or approval; records the exact reason and safe next action; and never extends a budget automatically. Do not add `--yes`.

| Code | Meaning |
| ---: | --- |
| 0 | Command completed successfully. |
| 1 | Configuration, validation, workflow, provider, or internal failure. |
| 2 | Invalid CLI usage, owned by `argparse`. |
| 3 | Durable `awaiting_user`; operator decision required. |
| 4 | Durable `waiting_provider`; external state or approval required. |
| 130 | Interrupted by the operator. |

`brace status` returns 0 whenever it successfully reads and validates a coherent snapshot, even when that snapshot describes a blocked workflow.

`brace doctor` returns 0 when it completes with no `FAIL` result, including when only `WARN` results exist. It returns 1 when a required check fails or the diagnostic command itself cannot complete. Human and JSON modes use the same result and exit code.

### Diagnostics and read-only preflight

Add global `--verbose`. Expected errors remain concise. Unexpected exceptions are concise by default; `--verbose` enables traceback and underlying command detail.

Before an enterprise-readiness claim, tests must prove that there are no token/password CLI options; known credential-shaped arguments and provider output are redacted; tracebacks contain no locals; logs remain repository-local and ignored; status JSON never returns raw logs; documentation warns that agent logs can contain repository content; and diagnostics are never uploaded automatically. Do not add `--quiet` yet.

Add `brace doctor [repository] [--output human|json]`. Without mutation or prompts, it checks supported Python and Brace versions; Git and Codex availability; applicable GitHub or Azure CLI availability and authentication; repository, remote, and provider identity; target and integration branch visibility; configuration and schema compatibility; worktree-root accessibility; workflow-lock state; required packaged support files; and recoverability of interrupted state.

`doctor` must not create directories, initialize or migrate state, fetch destructively, repair files, update schemas, modify authentication, or prompt. Reuse existing discovery and validation helpers instead of adding another provider abstraction.

### Static tab completion

Implement completion only after command names and public options are frozen:

```text
brace completion bash
brace completion zsh
brace completion fish
brace completion powershell
```

Package four static resources through the existing support-resource mechanism:

```text
src/brace/resources/completions/brace.bash
src/brace/resources/completions/_brace
src/brace/resources/completions/brace.fish
src/brace/resources/completions/brace.ps1
```

The command accepts exactly those shells, reads the installed resource relative to the package, and writes directly to stdout without Rich. It performs no repository discovery, configuration or state read, provider call, network operation, or mutation. Unsupported values return argparse code 2. Brace never edits shell profiles.

Static scripts complete public commands, public options, fixed choices such as provider and visibility, and filesystem paths for repository and worktree arguments. They perform no dynamic task, bug, branch, GitHub, Azure, or state queries.

Keep `argparse`. Do not migrate to Click or Typer, add `argcomplete`, or build a completion generator solely for completion.

### Concurrency-option compatibility

Replace `--maximum-concurrent-builders` and `--maximum-concurrent-fixers` with `--maximum-concurrent-agents`. Accept the old `init` options as hidden deprecated aliases, reject combinations of old and new options, use the larger supplied legacy value as the initial global ceiling when only legacy options are present, emit one warning on stderr, and remove the aliases only in a documented major release.

### Issue reconciliation

| Issue | Additional CLI/UX amendment |
| --- | --- |
| #25 | Stable three-stage surface, resume-by-rerun, and no public commands for internal transitions. |
| #28 | Concise/default and detailed/JSON status, quiet aggregate progress, rate-limited plain-output heartbeat, blockers, summaries, and safe next actions. |
| #29 | Supply aggregate phase, utilization, and completion counts to the transient status and full details to verbose/status output. |
| #38 | Exact fields required by status JSON while keeping persistence and public schemas separate. |
| #40 | Interactive and `--no-input` audit-exhaustion behavior and exit code 3. |
| #41 | Exact provider identities and waiting reasons in status and doctor without weakening merge gates. |
| #51 | Non-TTY behavior, heartbeat rate limiting, narrow-terminal and concurrent-output safety, status cleanup, shell checks, packaged completion resources, and Ubuntu/Windows CLI tests. |

Create one new CLI-contract issue, without inventing an issue number, for `--no-input`, `--no-color`, `--no-progress`, `--verbose`, exit codes, `doctor`, static completion, diagnostic redaction, and legacy-option deprecation. Link it to #25 and make it depend on the command surface and state vocabulary being frozen. Do not create one issue per shell or flag unless implementation demonstrates independent ownership.

### Placement in the implementation sequence

1. In Phase 1, freeze the command surface, vocabulary, exit codes, and human/JSON contracts; add parser, narrow-terminal, concurrent-output, and non-TTY heartbeat contract tests.
2. In Phase 2, persist terminal reasons, budgets, deadlines, and next actions needed by status and noninteractive exits.
3. In Phase 4, derive one aggregate Rich `Status` line from coordinator state and expose task-level transitions only through verbose output; add no event bus, rendering thread, or nested executor.
4. In Phase 6, implement interactive and `--no-input` audit-capacity handling using durable audit state.
5. In Phase 7, add `doctor`, redaction, legacy-option compatibility, and structured-status validation.
6. After public options freeze, add the four static completion files and packaging/shell tests.

### CLI and UX acceptance criteria

- Successful `init` prints one result and next action; its checklist appears only under `--verbose`, and partial failure never claims rollback or safe rerun without recovery evidence.
- `plan` uses one transient status, prints one compact result, and preserves the separate plan/build review boundary.
- Amendment output shows added work, superseded untouched work, changed constraints, and unchanged integrated work before approval.
- Default `build` shows one aggregate status and no successful task-by-task events; it never reports `Built` while required work remains unintegrated.
- Default `audit` shows only major aggregate phases; verbose/status output identifies whole-project versus scoped bug/repair review and binds each whole-project audit to its frozen commit and tree.
- A final-round finding batch is shown as preserved and untriaged; no repair begins without capacity for another closure audit.
- `Completed` appears only after clean audit, same-tree validation, exact provider merge verification, containment, and required cleanup.
- Default status is concise; verbose status contains assignment, budget, identity, worktree, PR, and deadline details.
- Doctor prints only problems by default and all checks under `--verbose`; completion emits only its raw script.
- A TTY uses one Rich `Status` line on stderr and leaves one compact normal result after it clears.
- Non-TTY output and `--no-progress` contain no transient status, cursor control, or animation.
- A narrow TTY keeps progress on one row, preserves the phase and project name, and leaves no redraw fragments.
- Simultaneous worker completions cannot interleave, duplicate, or corrupt human output; only the coordinator renders transitions.
- After five minutes without other plain output, non-TTY and `--no-progress` modes emit at most one aggregate heartbeat per five-minute silence interval; provider polls and task-level heartbeats remain suppressed.
- Task-level transitions are absent from successful default output and present under `--verbose`.
- `--no-color` removes color without changing workflow behavior.
- Prompt, failure, terminal-outcome, and keyboard-interrupt paths clear transient output and leave the terminal in a usable state.
- Every terminal outcome exposes an exact reason and safe next action.
- `--no-input` never prompts or grants approval and persists state before returning 3 or 4.
- Exit codes match the contract, including interruption 130.
- Status and doctor JSON each emit one schema-valid document on stdout and diagnostics only on stderr.
- Status JSON exposes no raw agent output, prompts, logs, or credentials.
- `doctor` performs no project, provider, authentication, or filesystem mutation.
- `doctor` returns 0 for PASS/WARN-only results and 1 for any FAIL result in both human and JSON modes.
- Each completion shell emits nonempty UTF-8 without Rich/ANSI formatting and performs no repository, state, provider, or network operation.
- Unsupported completion shells return argparse code 2.
- Completion scripts contain every public command and cannot silently drift from parser options.
- Bash syntax is checked on Ubuntu and PowerShell syntax on Windows; Zsh and Fish use native checks when available and deterministic content checks otherwise.
- Wheel and source distributions contain all four completion files.
- Legacy concurrency aliases migrate deterministically, warn once, and cannot be combined with the replacement option.

### Deliberate deferrals

Do not claim GitHub Enterprise Server or Azure DevOps Server support until provider tests establish it. Signed artifacts, provenance/SBOM publication, and Brace-specific proxy or custom-CA settings remain separate product requirements. Prefer native Git, `gh`, `az`, and Codex configuration when those requirements arise.

The CLI objective is operational clarity: one parser, one existing rendering library, one transient aggregate status, one plain fallback, two structured snapshot commands, and four static completion resources.
