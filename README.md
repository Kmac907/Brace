# Worktree Ralph

Worktree Ralph is a portable, repository-local workflow for taking a project from requirements to a merged implementation with isolated Codex agents.

It has three scripts:

1. `planning_loop.py` turns `requirements.md` into `plan.md` and `.codex/tasks.json`.
2. `build_loop.py` runs dependency-ready tasks in parallel Git worktrees and integrates each through a provider pull request.
3. `audit_loop.py` performs one whole-project audit, fixes the resulting bug ledger in parallel worktrees, validates the result, and merges the project pull request.

The Python coordinator is deterministic. It owns state, scheduling, Git, pull requests, retries, reconciliation, and cleanup. Codex agents perform semantic planning, implementation, review, auditing, fixes, and project-management decisions.

## Requirements

- Python 3.11 or newer
- Git
- Codex CLI
- The `jsonschema` package
- GitHub CLI (`gh`) for GitHub projects, or Azure CLI (`az`) with the Azure DevOps extension
- Authenticated Git and provider CLI access
- A configured Git `user.name` and `user.email`

Install the Python dependency:

```text
python -m pip install -r requirements.txt
```

A generated project carries the same dependency declaration at `.codex/requirements.txt`.

## Quick start

### Bootstrap a project

Download and review the bootstrapper, then run it:

```text
curl -fsSLO https://raw.githubusercontent.com/Kmac907/worktree-ralph/main/new_worktree_ralph_project.py
python new_worktree_ralph_project.py
```

The bootstrapper asks whether the project already exists.

For an existing repository:

```text
python new_worktree_ralph_project.py --existing-repository-path /path/to/repository
```

The existing repository must be clean, checked out on its remote default branch, exactly synchronized with `origin`, and must not already contain `.codex`. Existing project documentation is preserved.

For a new repository, supply values interactively or with arguments:

```text
python new_worktree_ralph_project.py --project-name example --parent-directory /projects --provider github
```

The bootstrapper copies the workflow, configures `.codex/workflow.json`, commits it, creates or updates the remote repository, verifies the remote SHA, and initializes local workflow state.

The downloaded script runs with your account's permissions and can create repositories and push commits. Pin its URL to a reviewed tag or commit when reproducibility matters.

### Write requirements

Use `REQUIREMENTS-PROMPT.md` to turn the project idea into the structured `requirements.md` template. Review the result before planning.

### Plan

```text
python .codex/scripts/planning_loop.py
```

Planning reads the repository and `requirements.md`. When essential information is missing, it asks in the same terminal, appends the answers, and retries. It then normalizes requirements, writes `plan.md`, validates the dependency graph and requirement coverage, commits and pushes the contract, and writes the task ledger and planning summary.

Planning stops before implementation. Review:

- `plan.md`
- `.codex/tasks.json`
- `.codex/planning-summary.json`

### Build

```text
python .codex/scripts/build_loop.py
```

The build loop resumes from persisted state, reconciles existing branches, worktrees, commits, and pull requests, then runs dependency-ready non-conflicting tasks up to `maximumConcurrentBuilders`. Each task receives its own worktree and `worktree/TASK-NNNN` branch. A verifier checks the focused result before the coordinator creates and merges its pull request into `ralph/integration`.

The build stage ends after all active tasks are integrated and lightweight integration verification passes. It writes `.codex/build-summary.json`.

### Audit and finish

```text
python .codex/scripts/audit_loop.py
```

The audit loop performs one fresh audit of the exact integration commit and freezes its findings in `.codex/bugs.json`. Dependency-ready non-conflicting fixes run up to `maximumConcurrentFixers`, each in its own `worktree/BUG-NNNN` branch and pull request. After every bug is verified, final validation runs against the exact integration SHA. The coordinator then merges the project pull request into the target branch and removes owned worktrees and branches.

It writes `.codex/audit-summary.json` and leaves the flat `.codex` ledgers as the completed record.

## Ownership

| Component | Responsibility |
| --- | --- |
| Python coordinator | Sole writer of live state and ledgers; schedules work; validates outputs; manages Git, pull requests, recovery, and cleanup |
| Planning agent | Proposes normalized requirements, the implementation plan, and the initial task graph |
| Project-manager agent | Analyzes semantic blockers and proposes user-approved contract amendments and follow-up tasks |
| Builder agents | Implement one assigned task in one isolated worktree and commit |
| Verifier | Checks a focused task, bug fix, amendment, build integration, or final result |
| Auditor | Produces one complete bounded whole-project bug set without editing |
| Bug-fixer agents | Fix or disprove one assigned bug in one isolated worktree |
| User | Decides changes to requirements, scope, policy, or project direction |

Agents do not communicate directly. The coordinator provides immutable assignments and carries structured results between roles. Agents never edit live `.codex/state.json`, `.codex/tasks.json`, or `.codex/bugs.json`, and never perform provider operations.

## Files in a generated project

```text
.codex/
├── AGENTS.md
├── workflow.json
├── requirements.txt
├── prompts/
├── schemas/
├── scripts/
├── state.json
├── tasks.json
├── bugs.json
├── planning-summary.json
├── build-summary.json
├── audit-summary.json
├── assignments/
├── results/
└── logs/
```

Mutable state, summaries, attempt records, logs, and the lock are ignored by Git. Prompts, schemas, scripts, configuration, and dependency declarations are tracked.

External worktrees use:

```text
<worktree-root>/<repository-id>/
├── TASK-0001/
├── BUG-0001/
└── AMEND-0001/
```

Owned branches use `ralph/integration`, `worktree/TASK-NNNN`, `worktree/BUG-NNNN`, and `worktree/AMEND-NNNN`.

## Drift detection and recovery

Before waves, verification, publication, audit, and final merge, the coordinator verifies:

- repository path, provider identity, remote URL, target branch, and workflow configuration;
- exact requirements, plan, task-definition, and bug-definition hashes;
- target-branch baseline;
- integration history against verified task, bug, or amendment merge SHAs;
- exact worktree branch, base, HEAD, cleanliness, and result identity;
- exact pull-request repository, ID, head SHA, base SHA, and merge SHA.

State and ledgers use atomic replacement. Each attempt has immutable assignment and result files. Restart the same script after a recoverable interruption; it reconciles durable work before retrying.

A completed workflow can be replaced only with:

```text
python .codex/scripts/planning_loop.py --start-new-workflow
```

The previous workflow must be complete and owned worktrees and branches must already be cleanly resolved.

## Semantic blockers

Operational failures such as authentication, timeouts, dirty worktrees, malformed results, or provider outages use bounded retries or stop with a concrete diagnostic.

A semantic blocker can invoke the project-manager agent. The coordinator:

1. stops scheduling new work and preserves completed agent results;
2. asks the user an interactive question with a recommendation and effects;
3. creates an isolated amendment worktree after approval;
4. limits edits to approved Markdown contract files;
5. verifies one focused commit and its exact decision identity;
6. integrates it through a provider pull request;
7. appends validated follow-up tasks or records an approved bug disposition;
8. resumes build or audit automatically.

If an audit amendment expands implementation scope, the workflow returns to build and requires a fresh whole-project audit afterward. Amendment rounds are bounded by `maximumAmendmentRounds`.

## Configuration

Edit `.codex/workflow.json` before planning begins.

- `provider`: `github` or `azure_devops`
- `remote`: Git remote name
- `targetBranch`: final merge target
- `integrationBranch`: coordinator integration branch
- `maximumConcurrentBuilders`: parallel task agents
- `maximumConcurrentFixers`: parallel bug-fix agents
- `maximumTaskAttempts`: attempts per task
- `maximumBugAttempts`: attempts per bug
- `maximumPlanningQuestionRounds`: interactive planning rounds
- `maximumAmendmentRounds`: semantic amendment limit
- `agentTimeoutMinutes`: Codex deadline
- `agentCleanupGraceSeconds`: process-tree cleanup grace
- `worktreeRoot`: optional external worktree root
- `deleteMergedBranches`: delete verified merged branches
- provider repository fields under `github` or `azureDevOps`

Configuration is frozen into workflow state. Changing it after workflow creation is treated as drift.

## Troubleshooting

- **Missing Python package**: run `python -m pip install -r .codex/requirements.txt`.
- **Authentication failure**: repair `gh`, `az`, Git, or Codex authentication, then rerun the same stage.
- **Dirty repository or worktree**: preserve or commit the reported files; the coordinator will not discard them.
- **Unknown integration commit**: reconcile the exact unowned commit or pull request before resuming.
- **Stale pull request**: close or reconcile the PR whose head/base identity differs from the persisted assignment.
- **Semantic question**: answer in the active terminal. No JSON inbox is required.
- **Attempts exhausted**: inspect the persisted result and blocker; the workflow does not fabricate completion.

## Development and tests

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests use temporary local Git repositories and mocked provider/agent boundaries. They do not create remote pull requests.

## License

See [LICENSE](LICENSE).
