# Worktree Ralph

Worktree Ralph is a portable set of repository-local PowerShell scripts for running a Codex-assisted project from requirements through implementation, audit, bug fixing, and merge.

It is not an installed application. A standalone bootstrapper copies the workflow into a new repository. The copied project then owns three entry scripts:

- `.codex/scripts/planning-loop.ps1`
- `.codex/scripts/build-loop.ps1`
- `.codex/scripts/audit-loop.ps1`

Each script is resumable. Tasks and bugs run in isolated external Git worktrees and integrate through real GitHub or Azure DevOps pull requests. Every assignment attempt and result is written immutably before the coordinator advances it.

## Prerequisites

- PowerShell 7.4 or newer
- Git 2.40 or newer
- Codex CLI
- GitHub CLI (`gh`) or Azure CLI (`az`) with the Azure DevOps extension
- Existing authentication for Codex and the selected Git provider

No credential is written to the repository.

## Create a project

Run `New-WorktreeRalphProject.ps1` from a trusted local copy. The script asks for the project location and provider settings, creates the local and remote repositories, copies only `template/`, validates the scaffold, pushes `main`, and removes its temporary source clone.

After bootstrap:

1. Complete `requirements.md`.
2. Run `.codex/scripts/planning-loop.ps1`.
3. Run `.codex/scripts/build-loop.ps1` after planning reports completion.
4. Run `.codex/scripts/audit-loop.ps1` after the build reports completion.

The scripts print clear stage status and write local summaries under `.codex/`. Rerun the current stage script after an interruption; it reconciles exact worktree, commit, repository, branch, and pull-request identities before continuing.

## Workflow model

The integration branch is `ralph/integration`. A task uses `worktree/TASK-NNNN`; a bug uses `worktree/BUG-NNNN`. Each task or bug receives its own external worktree. The coordinator is the only writer of `.codex/state.json`, `.codex/tasks.json`, and `.codex/bugs.json`.

The build stage performs focused functional checks. The audit stage performs one fresh whole-project audit, freezes the resulting bug ledger, fixes and verifies those bugs, performs final validation, and merges the project pull request to `main`.

## Drift and recovery

The coordinator freezes hashes for `requirements.md`, `plan.md`, workflow configuration, task definitions, bug definitions, and the target-branch baseline. It checks them at stage startup, between waves, after agent work, and immediately before publishing or merging. Unknown integration commits, target advancement, stale pull requests, dirty worktrees, and mismatched provider or remote identities stop with a specific blocker.

Agent and CLI processes have deadlines and process-tree teardown. Successful attempt results remain in ignored `.codex/assignments/` and `.codex/results/`, allowing restart without repeating completed agent work. Final cleanup verifies that owned worktrees and branches are gone.

To begin another project update after a completed workflow, update `requirements.md`, then run `planning-loop.ps1 -StartNewWorkflow`. This is accepted only after the prior final merge and cleanup are verified. Prior attempt records move under ignored logs, and new flat ledgers are created.

## Development

Run `tests/Run-Tests.ps1` from this repository. Tests use temporary Git repositories and deterministic fake agent/provider fixtures; they do not create remote repositories.

## License

Copyright (c) 2026 Kmac907. All rights reserved. See `LICENSE`.
