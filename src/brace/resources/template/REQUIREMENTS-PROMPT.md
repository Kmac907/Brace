# Requirements authoring prompt

Run your coding agent from this project repository's root, copy the prompt below, and replace the placeholder between the project-idea delimiters. A project idea may be a short paragraph, rough notes, an existing specification, or a detailed proposal.

```text
Turn the project idea below into a complete requirements.md using the requirements.md template already present at the repository root.

INSTRUCTIONS

1. Read the existing requirements.md template completely before making changes.
2. Treat my project idea and my answers as the only authority for project requirements.
3. Do not invent features, integrations, platforms, policies, limits, security requirements, or technical decisions.
4. Before editing requirements.md, identify any missing information that would materially affect project scope, user-visible behavior, architecture or technology choices, security or authorization, data handling, external integrations, deployment, compatibility, or acceptance criteria.
5. Ask one concise batch of questions only when an ambiguity, contradiction, or missing decision prevents safe and useful requirements. Ask interactively in this conversation and wait for my answers.
6. Record non-blocking unknowns under "Open questions" and continue instead of asking about them.
7. Do not create a question file or require me to edit JSON.
8. Once any necessary questions are answered, rewrite requirements.md completely.
9. Do not create plan.md, tasks.json, state.json, or implementation code. This task is only for requirements.md.

REQUIREMENT-WRITING RULES

1. Preserve the template's section structure where applicable.
2. Replace every TODO.
3. Give every requirement a stable identifier using the template's category:
   - REQ-GOAL-NNN
   - REQ-NONGOAL-NNN
   - REQ-USE-NNN
   - REQ-FUNC-NNN
   - REQ-IO-NNN
   - REQ-UI-NNN
   - REQ-TECH-NNN
   - REQ-PLAT-NNN
   - REQ-INT-NNN
   - REQ-DATA-NNN
   - REQ-AUTH-NNN
   - REQ-SEC-NNN
   - REQ-PERF-NNN
   - REQ-REC-NNN
   - REQ-COMPAT-NNN
   - REQ-TEST-NNN
   - REQ-DOC-NNN
   - REQ-DELIVERY-NNN
   - REQ-CONSTRAINT-NNN
   - REQ-ACCEPT-NNN
4. Number identifiers sequentially within each category, starting at 001.
5. Write one independently understandable requirement per bullet.
6. Use clear, specific, testable language.
7. Separate required behavior from implementation suggestions.
8. Preserve every explicit constraint, exclusion, dependency, limitation, and non-goal in my project idea.
9. Do not silently convert preferences into mandatory requirements.
10. Do not silently resolve contradictions. Ask me about material conflicts.
11. Make acceptance criteria objectively verifiable.
12. Ensure acceptance criteria cover the project's stated goals and essential functional behavior.
13. Put unresolved decisions under "Open questions."
14. Put answers obtained during this conversation under "Confirmed clarifications."
15. If a template section is explicitly inapplicable, omit it and record the reason under "Confirmed clarifications."
16. Do not leave placeholders, TODOs, ambiguous "etc." statements, or unsupported assumptions.
17. Do not expand the project beyond my project idea and confirmed answers.

FINAL VALIDATION

1. Confirm every statement from my project idea is represented or deliberately excluded.
2. Confirm every requirement has the correct identifier.
3. Confirm identifiers are unique and sequential.
4. Confirm requirements do not contradict one another.
5. Confirm open questions are genuinely unresolved.
6. Confirm there are no TODO placeholders.
7. Confirm requirements are detailed enough for the Brace planning loop to create plan.md and a task graph.
8. Write the final document to the repository-root requirements.md.
9. Return a concise summary containing the number of requirements by category, important constraints and non-goals, confirmed clarifications, unresolved open questions, and whether requirements.md is ready for the planning loop.

PROJECT IDEA

--- BEGIN PROJECT IDEA ---

Paste anything you currently know about the project here.

--- END PROJECT IDEA ---
```
