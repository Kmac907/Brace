from __future__ import annotations

import re
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.theme import Theme


THEME = Theme({"brace": "bold cyan", "success": "bold green", "warning": "yellow", "error": "bold red"})
console = Console(theme=THEME)
error_console = Console(stderr=True, theme=THEME)
MAXIMUM_SUMMARY_CHARACTERS = 240
TERMINAL_ESCAPE = re.compile(r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _activity(operation: str, identity: str | None, attempt: int | None, elapsed: float) -> str:
    return f"{operation} | {identity or 'project'} | attempt {attempt if attempt is not None else '-'} | elapsed {_duration(elapsed)}"


def agent_heartbeat(operation: str, identity: str | None, attempt: int | None, elapsed: float) -> None:
    info("Heartbeat: " + _activity(operation, identity, attempt, elapsed), "brace")


def agent_completed(operation: str, identity: str | None, attempt: int | None, elapsed: float, summary: Any) -> None:
    if isinstance(summary, str):
        rendered = " ".join(summary.split())
    elif isinstance(summary, dict):
        rendered = ", ".join(
            f"{key}={value}" for key, value in summary.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        )
    else:
        rendered = "Validated result received"
    rendered = TERMINAL_ESCAPE.sub("", rendered)
    rendered = " ".join("".join(character for character in rendered if character.isprintable() or character.isspace()).split())
    if len(rendered) > MAXIMUM_SUMMARY_CHARACTERS:
        rendered = rendered[: MAXIMUM_SUMMARY_CHARACTERS - 1].rstrip() + "…"
    info("Completed: " + _activity(operation, identity, attempt, elapsed) + f" | {rendered}", "success")


def ask(message: str, default: str | None = None) -> str:
    return Prompt.ask(message, default=default, console=console)


def confirm(message: str, default: bool = False) -> bool:
    return Confirm.ask(message, default=default, console=console)


def status(message: str):
    if not console.is_terminal:
        console.print(message, style="brace")
        return nullcontext()
    return console.status(message, spinner="dots", spinner_style="brace")


def info(message: str, style: str | None = None) -> None:
    console.print(message, style=style, markup=False)


def success(message: str) -> None:
    info(message, "success")


def warning(message: str) -> None:
    error_console.print(f"Warning: {message}", style="warning", markup=False)


def error(message: str) -> None:
    error_console.print()
    error_console.print(Panel.fit(message, title="Brace error", border_style="error"), markup=False)


def traceback() -> None:
    error_console.print()
    error_console.print_exception(show_locals=False)


def render_status(
    state: dict[str, Any],
    tasks: dict[str, Any] | None = None,
    bugs: dict[str, Any] | None = None,
    active_started: dict[str, datetime] | None = None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    active_started = active_started or {}
    table = Table(title="Brace workflow", show_header=False, border_style="cyan")
    table.add_column("Field", style="bold cyan")
    table.add_column("Progress", no_wrap=True)
    table.add_column("Value")
    table.add_row("Stage", "", str(state["stage"]))
    table.add_row("Status", "", str(state["stageStatus"]))
    table.add_row("Repository", "", str(state["repository"]))
    table.add_row("Target", "", str(state["targetBranch"]))
    table.add_row("Integration", "", str(state["integrationBranch"]))
    integration_sha = str(state.get("integrationSha") or "")
    table.add_row("Integration SHA", "", integration_sha[:12])
    timestamp = str(state["updatedAt"])
    if timestamp[-1:] in {"Z", "z"}:
        timestamp = timestamp[:-1] + "+00:00"
    updated = datetime.fromisoformat(timestamp)
    table.add_row("Updated", "", f"{state['updatedAt']} ({_duration((now - updated).total_seconds())} ago)")
    if tasks is not None:
        complete, total = sum(item["status"] in {"integrated", "superseded"} for item in tasks["tasks"]), len(tasks["tasks"])
        table.add_row("Tasks", ProgressBar(total=max(total, 1), completed=complete, width=20), f"{complete}/{total} complete")
    if bugs is not None:
        complete, total = sum(item["status"] == "verified" for item in bugs["bugs"]), len(bugs["bugs"])
        table.add_row("Bugs", ProgressBar(total=max(total, 1), completed=complete, width=20), f"{complete}/{total} complete")
    for label, identity_key, ledger in (("Active task", "taskId", tasks), ("Active bug", "bugId", bugs)):
        if ledger is None:
            continue
        for item in (entry for entry in ledger["tasks" if identity_key == "taskId" else "bugs"] if entry["status"] == "active"):
            identity = item[identity_key]
            started = active_started.get(identity)
            elapsed = _duration((now - started).total_seconds()) if started else "unknown"
            table.add_row(label, "", f"{identity} | attempt {item['attemptCount']} | elapsed {elapsed}")
    for identity_key, ledger in (("taskId", tasks), ("bugId", bugs)):
        if ledger is None:
            continue
        for item in ledger["tasks" if identity_key == "taskId" else "bugs"]:
            identity = item[identity_key]
            if item.get("lastError"):
                table.add_row(f"{identity} error", "", str(item["lastError"]))
            pull_request = item.get("pullRequest")
            if pull_request:
                table.add_row(
                    f"{identity} PR", "",
                    f"{pull_request['id']} | {pull_request['state']} | {pull_request['url']}",
                )
    amendment = state.get("activeAmendment")
    if amendment and amendment.get("pullRequest"):
        pull_request = amendment["pullRequest"]
        table.add_row(
            f"{amendment['amendmentId']} PR", "",
            f"{pull_request['id']} | {pull_request['state']} | {pull_request['url']}",
        )
    console.print(table)
    if state.get("blocker"):
        blocker = state["blocker"]
        console.print(Panel(f"{blocker['message']}\n\nDecision: {blocker['requiredDecision']}", title="Blocked", border_style="warning"), markup=False)
