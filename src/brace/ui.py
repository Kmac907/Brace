from __future__ import annotations

from contextlib import nullcontext
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


def ask(message: str, default: str | None = None) -> str:
    return Prompt.ask(message, default=default, console=console)


def confirm(message: str, default: bool = False) -> bool:
    return Confirm.ask(message, default=default, console=console)


def status(message: str):
    if not console.is_terminal:
        console.print(message, style="brace")
        return nullcontext()
    return console.status(message, spinner="dots", spinner_style="brace")


def error(message: str) -> None:
    error_console.print(Panel(message, title="Brace error", border_style="error"), markup=False)


def render_status(state: dict[str, Any], tasks: dict[str, Any] | None = None, bugs: dict[str, Any] | None = None) -> None:
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
    if tasks is not None:
        complete, total = sum(item["status"] == "integrated" for item in tasks["tasks"]), len(tasks["tasks"])
        table.add_row("Tasks", ProgressBar(total=max(total, 1), completed=complete, width=20), f"{complete}/{total} integrated")
    if bugs is not None:
        complete, total = sum(item["status"] == "verified" for item in bugs["bugs"]), len(bugs["bugs"])
        table.add_row("Bugs", ProgressBar(total=max(total, 1), completed=complete, width=20), f"{complete}/{total} verified")
    console.print(table)
    if state.get("blocker"):
        blocker = state["blocker"]
        console.print(Panel(f"{blocker['message']}\n\nDecision: {blocker['requiredDecision']}", title="Blocked", border_style="warning"), markup=False)
