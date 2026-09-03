# Worktree Ralph coordinator instructions

The coordinator owns workflow state, Git worktrees, branches, provider pull requests, retries, and cleanup.

Agents receive one immutable assignment in their prompt. They must not select their own task, alter shared ledgers, integrate changes, or broaden scope. Builder work is limited to focused functional implementation. The auditor performs one fresh whole-project audit. Bug fixers address the frozen ledger one bug at a time. Verifiers check the exact assigned acceptance criteria without starting another whole-project audit.

Repository evidence overrides summaries. Never claim an unexecuted check passed. Never expose credentials. Never delete an unverified path or unowned resource.
