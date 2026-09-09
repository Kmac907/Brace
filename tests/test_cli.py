from __future__ import annotations

import io
import unittest
from hashlib import sha256
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
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        self.assertFalse((workflows / "package.yml").exists())
        workflow = (workflows / "release.yml").read_text(encoding="utf-8")
        self.assertEqual(sha256(workflow.encode()).hexdigest(), "db6166ddd925d8808af166b81f2e92f04c2c8bdbef527f4e60b554bce470689b")
        lines = [line.strip() for line in workflow.splitlines()]
        commands = [line.removeprefix("run: ") for line in lines]
        for required in (
            "workflow_dispatch:",
            "          - patch\n          - minor\n          - major",
            'uv version --bump "${{ inputs.bump }}"',
            "uv run --locked python -m unittest discover -s tests -v",
            'git commit -m "Release v${VERSION}"',
            'git tag -a "v${VERSION}" -m "Brace v${VERSION}"',
            'git push --atomic origin HEAD:main "refs/tags/v${VERSION}"',
        ):
            self.assertIn(required, workflow)
        self.assertIn(
            "      - name: Build wheel and source distribution\n"
            "        run: uv build\n"
            "      - name: Verify distributions",
            workflow,
        )
        self.assertEqual(sum(command == "uv build" or command.startswith("uv build ") for command in commands), 1)
        for verification in (
            'test "$(uv run --isolated --no-project --with dist/*.whl brace --version)" = "brace ${VERSION}"',
            'test "$(uv run --isolated --no-project --with dist/*.tar.gz brace --version)" = "brace ${VERSION}"',
        ):
            self.assertIn(verification, lines)
        self.assertIn(
            'run: gh release create "v${VERSION}" dist/* --verify-tag --generate-notes --title "Brace v${VERSION}"',
            lines,
        )

    def test_ci_workflow_runs_locked_checks_without_publishing(self) -> None:
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        workflow = (workflows / "ci.yml").read_text(encoding="utf-8")
        release = (workflows / "release.yml").read_text(encoding="utf-8")
        for required in (
            "  pull_request:\n  push:\n    branches: [main]",
            "permissions:\n  contents: read",
            "os: [ubuntu-latest, windows-latest]",
            "runs-on: ${{ matrix.os }}",
            "run: uv python install 3.11",
            "run: uv run --locked python -m compileall -q src tests",
            "run: uv run --locked python -m unittest discover -s tests -v",
            "run: uv run --locked brace --help",
            "enable-cache: false",
        ):
            self.assertIn(required, workflow)
        self.assertEqual(
            [line.strip() for line in workflow.splitlines() if "setup-uv@" in line or line.strip().startswith("version:")],
            [line.strip() for line in release.splitlines() if "setup-uv@" in line or line.strip().startswith("version:")],
        )
        for forbidden in ("uv build", "uv version", "git tag", "git push", "gh release", "publish", "upload", "retention-days"):
            self.assertNotIn(forbidden, workflow)

    def test_bundled_template_is_complete_and_has_no_scripts(self) -> None:
        template = bootstrap.bundled_template()
        for relative in (
            "requirements.md", "REQUIREMENTS-PROMPT.md", "AGENTS.md", ".gitignore", ".gitattributes",
            ".codex/AGENTS.md", ".codex/config.toml", ".codex/workflow.json",
            ".codex/prompts/planner.md", ".codex/schemas/state.schema.json", ".codex/schemas/pull-request.schema.json",
        ):
            self.assertTrue((template / relative).is_file(), relative)
        self.assertFalse((template / ".codex" / "scripts").exists())
        self.assertIn("two independent adversarial reviewers", (template / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn(
            "two independent adversarial reviewers",
            (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8"),
        )
