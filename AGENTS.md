# Brace development instructions

- Preserve the three-stage design: planning, build, and audit/bug-fix.
- Keep the workflow repository-local and portable. Do not introduce an installer, service, compiled application, or global module.
- Use Python 3.11+ and resolve support files relative to `__file__`.
- Prefer the Python standard library; add dependencies only for trust-boundary functionality the standard library does not provide.
- Never embed credentials, user-specific paths, repository identities, or remote identities.
- Keep mutable project state in ignored `.codex/*.json` files.
- Only coordinator scripts may change shared state ledgers.
- Keep semantic decisions in the project-manager agent. The Python coordinator validates and applies PM output but must not invent contract or scope changes.
- PM amendments must be user-approved, isolated, durable, provider-integrated, and safely resumable. Never rewrite integrated tasks; append follow-up tasks and supersede only untouched pending work.
- All task and bug changes must use isolated worktrees and provider pull requests.
- Validate exact paths before removing worktrees or temporary directories.
- Add deterministic tests for state transitions, retries, recovery, drift, and cleanup.
