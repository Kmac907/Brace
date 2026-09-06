from __future__ import annotations

import io
import unittest
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from brace import __version__, bootstrap, cli, ui
from brace.common import BraceError


class CliTests(unittest.TestCase):
    def test_commands_and_init_flags_parse(self) -> None:
        args = cli.parser().parse_args([
            "init",
            "--project-name", "example",
            "--parent-directory", ".",
            "--provider", "github",
            "--visibility", "private",
            "--github-owner", "owner",
            "--maximum-concurrent-builders", "4",
            "--maximum-concurrent-fixers", "5",
            "--worktree-root", "worktrees",
            "--git-user-name", "Test",
            "--git-user-email", "test@example.invalid",
        ])
        self.assertEqual(args.command, "init")
        self.assertEqual(args.maximum_concurrent_builders, 4)
        self.assertEqual(args.maximum_concurrent_fixers, 5)

        plan = cli.parser().parse_args(["plan", "repository", "--start-new-workflow"])
        self.assertEqual((plan.command, plan.repository, plan.start_new_workflow), ("plan", "repository", True))
        self.assertEqual(cli.parser().parse_args(["build"]).repository, ".")
        self.assertEqual(cli.parser().parse_args(["audit"]).repository, ".")

    def test_plan_dispatch_and_expected_error_exit(self) -> None:
        with patch.object(cli.planning, "run") as run:
            self.assertEqual(cli.main(["plan", "repository"]), 0)
            run.assert_called_once_with("repository", False)
        with patch.object(cli.planning, "run", side_effect=BraceError("failed")), patch.object(cli, "error") as report:
            self.assertEqual(cli.main(["plan"]), 1)
            report.assert_called_once_with("failed")

    def test_interrupts_and_unexpected_errors_are_rendered(self) -> None:
        with patch.object(cli, "_dispatch", side_effect=KeyboardInterrupt), patch.object(cli, "error") as report:
            self.assertEqual(cli.main(["plan"]), 130)
            report.assert_called_once_with("Interrupted.")
        with patch.object(cli, "_dispatch", side_effect=RuntimeError("boom")), patch.object(cli, "traceback") as report:
            self.assertEqual(cli.main(["plan"]), 1)
            report.assert_called_once_with()

    def test_error_and_warning_output_is_compact_and_clean(self) -> None:
        output = io.StringIO()
        terminal = Console(file=output, force_terminal=False, color_system=None, width=100, theme=ui.THEME)
        with patch.object(ui, "error_console", terminal):
            ui.error("Interrupted.")
            ui.warning("Cleanup was deferred.")
        rendered = output.getvalue()
        self.assertTrue(rendered.startswith("\n"))
        self.assertIn("Brace error", rendered)
        self.assertIn("Warning: Cleanup was deferred.", rendered)
        self.assertNotIn("UserWarning", rendered)
        self.assertLess(max(map(len, rendered.splitlines())), 50)

    def test_unexpected_traceback_uses_rich_renderer(self) -> None:
        output = io.StringIO()
        terminal = Console(file=output, force_terminal=False, color_system=None, width=100, theme=ui.THEME)
        with patch.object(ui, "error_console", terminal):
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                ui.traceback()
        rendered = output.getvalue()
        self.assertIn("Traceback", rendered)
        self.assertIn("RuntimeError: boom", rendered)

    def test_plain_status_has_progress_without_terminal_codes(self) -> None:
        output = io.StringIO()
        state = {
            "stage": "build", "stageStatus": "running", "repository": "owner/repo",
            "targetBranch": "main", "integrationBranch": "brace/integration", "integrationSha": "abc", "blocker": None,
        }
        tasks = {"tasks": [{"status": "integrated"}, {"status": "pending"}]}
        bugs = {"bugs": [{"status": "verified"}]}
        with patch.object(ui, "console", Console(file=output, force_terminal=False, color_system=None, width=100)):
            ui.render_status(state, tasks, bugs)
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertIn("1/2 integrated", rendered)
        self.assertIn("1/1 verified", rendered)

    def test_runtime_version_uses_package_metadata(self) -> None:
        self.assertEqual(__version__, version("brace"))

    def test_release_workflow_versions_tests_tags_and_publishes(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:", "uv version --bump", "uv run --locked", "uv build",
            "git tag -a", "git push --atomic", "gh release create", "--verify-tag",
        ):
            self.assertIn(required, workflow)

    def test_bundled_template_is_complete_and_has_no_scripts(self) -> None:
        template = bootstrap.bundled_template()
        for relative in (
            "requirements.md", "REQUIREMENTS-PROMPT.md", "AGENTS.md", ".gitignore", ".gitattributes",
            ".codex/AGENTS.md", ".codex/config.toml", ".codex/workflow.json",
            ".codex/prompts/planner.md", ".codex/schemas/state.schema.json", ".codex/schemas/pull-request.schema.json",
        ):
            self.assertTrue((template / relative).is_file(), relative)
        self.assertFalse((template / ".codex" / "scripts").exists())


if __name__ == "__main__":
    unittest.main()
