# Brace rewrite

The user has requested a complete, clean-slate rewrite as a Python CLI packaged with uv, with a local supervisor and a native Textual TUI as the primary interactive application. Build the CLI foundation before the TUI. Consider Go or Rust only when a demonstrated capability or performance requirement warrants porting a specific component.

- Read `REWRITE-PLAN.md` as the self-contained specification for the new product.
- Do not reuse or port legacy code, tests, prompts, schemas, configuration, state, plans, or development guidelines.
- Do not recover or continue legacy tasks, issues, branches, worktrees, or agent conversations.
- Remaining legacy implementation files are pending removal at milestone M0; they are not design references. Nested legacy instruction files do not govern this rewrite.
- Current authorization is specification updates only. Do not implement the rewrite until the user explicitly requests implementation.
- Explicit subsequent user instructions take precedence over the plan.
