# Worktree Ralph development instructions

- Preserve the three-stage design: planning, build, and audit/bug-fix.
- Keep the workflow repository-local and portable. Do not introduce an installer, service, compiled application, or global PowerShell module.
- Use PowerShell 7 syntax and resolve support files relative to `$PSScriptRoot`.
- Never embed credentials, user-specific paths, repository identities, or remote identities.
- Keep mutable project state in ignored `.codex/*.json` files.
- Only coordinator scripts may change shared state ledgers.
- All task and bug changes must use isolated worktrees and provider pull requests.
- Validate exact paths before removing worktrees or temporary directories.
- Add deterministic tests for state transitions, retries, recovery, drift, and cleanup.
