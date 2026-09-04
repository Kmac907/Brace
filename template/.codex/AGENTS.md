# Worktree Ralph coordinator instructions

The PowerShell coordinator owns workflow state, task and bug ledgers, Git worktrees, branches, provider pull requests, retries, reconciliation, and cleanup. It applies deterministic validation and state transitions; it does not make semantic project decisions.

Agents receive one immutable assignment in their prompt, and the coordinator persists matching immutable assignment and result records for recovery. They must not select their own task, alter shared ledgers, integrate changes, or broaden scope. Builder work is limited to focused functional implementation. The auditor performs one fresh whole-project audit. Bug fixers address the frozen ledger one bug at a time. Verifiers check the exact assigned acceptance criteria without starting another whole-project audit.

When a builder, fixer, auditor, or verifier reports a semantic blocker, the coordinator invokes the project-manager agent. The PM analyzes repository evidence, recommends options, and asks the user interactively only when necessary. After approval, the PM may amend only `requirements.md` and `plan.md` in an isolated amendment worktree, propose appended tasks, and supersede only untouched pending tasks. The coordinator validates and integrates that amendment through a provider pull request, updates its ledgers, and resumes the correct stage. Agents never communicate directly or edit shared ledgers.

Repository evidence overrides summaries. Never claim an unexecuted check passed. Never expose credentials. Never delete an unverified path or unowned resource.
