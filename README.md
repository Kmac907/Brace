# Worktree Ralph

Worktree Ralph is a portable, repository-local workflow for taking a software project from written requirements to an audited merge with Codex coding agents.

It uses three PowerShell scripts:

1. **Planning** turns `requirements.md` into an implementation plan and dependency-aware task queue.
2. **Build** assigns ready tasks to parallel agents in isolated Git worktrees and integrates each result through a provider pull request.
3. **Audit** performs one deep review, records the complete finding set, fixes independent bugs in parallel, validates the finished project, and merges it to `main`.

There is no application, service, installer, or global module. The bootstrapper copies the workflow into the project, and the project carries everything required to continue.

## Requirements

- PowerShell 7 (`pwsh`)
- Git with worktree support
- [Codex CLI](https://github.com/openai/codex), authenticated
- One supported Git provider:
  - GitHub CLI (`gh`), authenticated with `gh auth login`
  - Azure CLI (`az`), authenticated, with the `azure-devops` extension installed
- A configured Git author name and email

The provider account must be allowed to create repositories, branches, and pull requests in the selected GitHub owner or Azure DevOps project.

## Quick start

### 1. Create the project

The shortest bootstrap runs directly from this repository and prompts for the project name, local parent directory, and provider:

```powershell
irm https://raw.githubusercontent.com/Kmac907/worktree-ralph/main/New-WorktreeRalphProject.ps1 | iex
```

This executes code downloaded from `Kmac907/worktree-ralph` with your current PowerShell permissions. Review the script first if you do not trust the repository or its maintainers. For reproducible automation, replace `main` in the URL with a release tag or exact commit.

To supply the settings without prompts:

```powershell
$bootstrap = irm https://raw.githubusercontent.com/Kmac907/worktree-ralph/main/New-WorktreeRalphProject.ps1

& ([scriptblock]::Create([string]$bootstrap)) `
    -ProjectName MyProject `
    -ParentDirectory C:\Code\Projects `
    -Provider github `
    -Visibility private `
    -MaximumConcurrentBuilders 3 `
    -MaximumConcurrentFixers 3
```

The bootstrapper:

- creates an empty local project directory;
- copies the repository-local workflow template;
- initializes `main` and creates the initial commit;
- creates the GitHub or Azure DevOps repository;
- pushes and verifies `main`;
- initializes ignored workflow state; and
- removes its temporary clone after successful setup.

It refuses to overwrite a non-empty destination or reuse an existing remote repository.

### 2. Write the requirements

Open the generated `requirements.md` and replace every `TODO`. Requirements use stable `REQ-*` identifiers so planning can prove that every applicable requirement belongs to at least one task.

Describe the expected behavior, constraints, integrations, security boundaries, failure handling, testing, delivery, and acceptance criteria. Mark intentionally excluded work as a non-goal instead of leaving it ambiguous.

### 3. Run planning

From the generated project root:

```powershell
& .\.codex\scripts\planning-loop.ps1
```

Planning reads the complete requirements and existing repository. It asks an interactive question only when an answer is necessary to produce a safe, internally consistent plan. Answers are incorporated into `requirements.md`, and planning continues automatically.

When planning finishes, it:

- normalizes `requirements.md`;
- creates or updates `plan.md`;
- validates requirement coverage and the task dependency graph;
- writes the local `tasks.json` queue;
- commits and pushes the planning documents to `main`; and
- prints `PLANNING COMPLETE` with a project summary.

### 4. Run the build

```powershell
& .\.codex\scripts\build-loop.ps1
```

The build loop runs without a command per task. It repeatedly finds dependency-ready, non-conflicting tasks, creates one external worktree and branch per task, launches builders in parallel, verifies their focused results, and integrates them into `ralph/integration` through real provider pull requests.

It stops after every task is integrated and lightweight integration validation passes. The final message is `BUILD COMPLETE`.

### 5. Run the audit and bug-fix stage

```powershell
& .\.codex\scripts\audit-loop.ps1
```

The audit loop performs one fresh, whole-project audit and freezes its findings in `bugs.json`. It then assigns independent bugs to isolated worktrees, verifies each fix, and integrates each fix through a pull request.

After every finding is resolved, it runs final project validation, merges the `ralph/integration` project pull request into `main`, removes owned worktrees and branches, and prints `PROJECT COMPLETE`.

## Workflow at a glance

```text
requirements.md
      |
      v
planning-loop.ps1 -> plan.md + tasks.json
      |
      v
build-loop.ps1 -> TASK worktrees -> task PRs -> ralph/integration
      |
      v
audit-loop.ps1 -> bugs.json -> BUG worktrees -> bug PRs
      |
      v
final validation -> project PR -> main -> cleanup
```

The three stage scripts are the only normal workflow entry points. Task selection, retries, verification, pull requests, reconciliation, and cleanup are coordinator responsibilities.

## Parallel agents

Concurrency is chosen during bootstrap:

- `MaximumConcurrentBuilders` defaults to `3`.
- `MaximumConcurrentFixers` defaults to `3`.
- Both accept values from `1` through `32`.

The coordinator may run fewer agents when dependencies, overlapping allowed paths, or exclusive resources make assignments unsafe to execute together. Planning and the deep audit each use one agent.

The selected values are stored in `.codex/workflow.json`, and the bootstrapper immediately freezes that configuration in workflow state. Choose concurrency and the optional worktree root during bootstrap. Editing the configuration afterward is treated as drift.

## Git and worktree model

The coordinator owns all Git integration:

```text
main
  `-- ralph/integration
        |-- worktree/TASK-0001
        |-- worktree/TASK-0002
        `-- worktree/BUG-0001
```

- Each task or bug receives its own external worktree and branch.
- Agents edit and commit only inside their assigned worktree.
- Agents do not push, create pull requests, merge, or modify shared ledgers.
- The coordinator verifies the exact commit before publishing it.
- Task and bug pull requests target `ralph/integration`.
- The final project pull request targets `main`.
- Squash-merged assignment branches and owned worktrees are removed after their merge is verified.

Unless `worktreeRoot` is configured, external worktrees are placed below the operating system temporary directory under `worktree-ralph/<repository-id>/`.

## Files in a generated project

Tracked workflow files travel with the project:

| Path | Purpose |
| --- | --- |
| `requirements.md` | User-authored project contract and confirmed planning clarifications |
| `plan.md` | Planner-generated implementation plan consumed by builders and reviewers |
| `.codex/workflow.json` | Provider, concurrency, retry, timeout, branch, and worktree settings |
| `.codex/AGENTS.md` | Coordinator-wide agent rules |
| `.codex/scripts/` | Planning, build, audit, and shared coordinator scripts |
| `.codex/prompts/` | Planner, builder, verifier, auditor, and bug-fixer role instructions |
| `.codex/schemas/` | Strict JSON contracts for state and agent results |

Mutable workflow records are local and ignored by Git:

| Path | Purpose |
| --- | --- |
| `.codex/state.json` | Current stage, frozen identities and hashes, integration SHA, and blocker |
| `.codex/tasks.json` | Frozen task definitions plus assignment and integration status |
| `.codex/bugs.json` | Frozen audit findings plus fix and verification status |
| `.codex/assignments/` | Immutable assignment record for every attempt |
| `.codex/results/` | Immutable agent result for every attempt |
| `.codex/*-summary.json` | Planning, build, and final audit summaries |
| `.codex/logs/` | Bounded agent logs and archived completed-attempt records |
| `.codex/workflow.lock` | Prevents two local coordinators from running simultaneously |

Do not manually edit `state.json`, `tasks.json`, `bugs.json`, assignment records, or result records. They are coordinator-owned recovery data.

## Drift detection and recovery

Worktree Ralph freezes and validates:

- repository and provider identity;
- remote URL and branch names;
- target-branch planning baseline;
- `requirements.md`, `plan.md`, and workflow configuration hashes;
- task and bug definition hashes;
- worktree path, branch, base commit, and result commit;
- pull-request repository, ID, source SHA, base SHA, and merge SHA; and
- every accepted commit on `ralph/integration`.

Checks run at stage startup, between assignment waves, after agent work, and before verification, publishing, or merging. Unknown commits, target advancement, stale pull requests, dirty worktrees, and identity mismatches stop the workflow instead of being silently accepted.

If a script is interrupted, run the same stage script again. It reconciles durable attempt records, worktrees, branches, pull requests, and provider merge results before scheduling replacement work.

When a genuine blocker is found, the script records and prints:

- the blocked stage;
- the exact reason; and
- the decision or correction required.

Assignment-specific failures and attempt counts remain attached to their task or bug in the corresponding ledger.

Correct that external condition and rerun the same script. Do not reset ledgers or delete worktrees to force progress.

Agent execution is bounded by `agentTimeoutMinutes`. A timed-out Codex process tree is terminated and given `agentCleanupGraceSeconds` to stop before the assignment is retried or blocked.

## Starting another project update

After a workflow reaches `PROJECT COMPLETE`:

1. Update `requirements.md` for the next body of work.
2. Commit and push that requirements change to `main`, leaving the repository clean.
3. Run:

```powershell
& .\.codex\scripts\planning-loop.ps1 -StartNewWorkflow
```

A new workflow is allowed only after the prior final merge and cleanup are verified. Completed attempt records are archived under `.codex/logs/`, and each new flat state, task, and bug ledger is written using atomic file replacement.

## Configuration reference

The bootstrapper writes `.codex/workflow.json`. Important settings are:

| Setting | Default | Meaning |
| --- | --- | --- |
| `provider` | `github` template default | `github` or `azure_devops`; bootstrap sets the selected provider |
| `remote` | `origin` | Git remote owned by the workflow |
| `targetBranch` | `main` | Final project pull-request target |
| `integrationBranch` | `ralph/integration` | Coordinator integration branch |
| `maximumConcurrentBuilders` | `3` | Maximum builders in one task wave |
| `maximumConcurrentFixers` | `3` | Maximum bug fixers in one bug wave |
| `maximumTaskAttempts` | `3` | Attempt limit per task |
| `maximumBugAttempts` | `3` | Attempt limit per bug |
| `maximumPlanningQuestionRounds` | `5` | Maximum interactive clarification rounds |
| `agentTimeoutMinutes` | `90` | Deadline for one Codex role invocation |
| `agentCleanupGraceSeconds` | `10` | Process-tree teardown grace period |
| `worktreeRoot` | `null` | Optional external worktree parent; system temporary storage when unset |
| `deleteMergedBranches` | `true` | Delete verified assignment and integration branches after merge |

Provider-specific repository identity is written under `github` or `azureDevOps` during bootstrap.

## Bootstrap parameters

`New-WorktreeRalphProject.ps1` accepts:

| Parameter | Behavior |
| --- | --- |
| `ProjectName` | New local directory and remote repository name; prompted when omitted |
| `ParentDirectory` | Existing local parent directory; prompted when omitted |
| `Provider` | `github` or `azure_devops`; prompted when omitted |
| `Visibility` | GitHub repository visibility; defaults to `private` |
| `GitHubOwner` | GitHub user or organization; current authenticated user when omitted |
| `AzureOrganization` | Azure DevOps organization URL; prompted for Azure DevOps |
| `AzureProject` | Azure DevOps project; prompted for Azure DevOps |
| `MaximumConcurrentBuilders` | Builder limit from 1–32; defaults to 3 |
| `MaximumConcurrentFixers` | Bug-fixer limit from 1–32; defaults to 3 |
| `WorktreeRoot` | Optional external worktree parent |
| `GitUserName` | Repository-local Git author override |
| `GitUserEmail` | Repository-local Git author override |
| `SourceRepository` | Workflow source; defaults to this GitHub repository |

## Security model

- The scripts do not write provider or Codex credentials into the project.
- Provider operations use the currently authenticated `gh` or `az` session.
- Agents are instructed not to push, merge, create pull requests, or edit coordinator state.
- The coordinator validates agent paths and exact commits before publishing them.
- Read-only planner, auditor, and verifier roles cannot intentionally modify production worktrees through the workflow.
- Destructive cleanup is restricted to verified workflow-owned worktree paths and branches.

Running a remote script with `irm | iex` is convenient but trusts the referenced repository revision. Pin a reviewed release tag or commit when reproducibility is more important than automatically receiving the latest bootstrapper.

## Development and tests

Clone this workflow repository and run:

```powershell
& .\tests\Run-Tests.ps1
```

The suite covers common state behavior, Git worktree isolation, drift and reconciliation, process-tree timeout cleanup, bootstrap behavior, and the complete planning-to-merge workflow. Tests use disposable local Git repositories and deterministic provider and agent fixtures; they do not create real remote repositories.

When changing workflow behavior, preserve the planning → build → audit design and add deterministic coverage for the affected state transition, recovery path, or cleanup rule.

## License

Copyright (c) 2026 Kmac907. All rights reserved. See [LICENSE](LICENSE).
