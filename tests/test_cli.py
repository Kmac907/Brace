from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

from brace import __version__, bootstrap, cli, common, ui
from brace.common import BraceError
from support import RepositoryTestCase


class CliTests(RepositoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        if self._testMethodName not in {
            "test_status_retries_cross_file_writer_interleaving",
            "test_status_rejects_unrecorded_provider_merge",
        }:
            patcher = patch.object(common, "STATUS_SNAPSHOT_ATTEMPTS", 1)
            patcher.start()
            self.addCleanup(patcher.stop)

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
        omitted = cli.parser().parse_args(["init"])
        self.assertIsNone(omitted.maximum_concurrent_builders)
        self.assertIsNone(omitted.maximum_concurrent_fixers)

        plan = cli.parser().parse_args(["plan", "repository", "--start-new-workflow"])
        self.assertEqual((plan.command, plan.repository, plan.start_new_workflow), ("plan", "repository", True))
        self.assertEqual(cli.parser().parse_args(["build"]).repository, ".")
        self.assertEqual(cli.parser().parse_args(["audit"]).repository, ".")
        self.assertEqual(cli.parser().parse_args(["status"]).repository, ".")

    def test_init_concurrency_defaults_and_rejects_invalid_interactive_values(self) -> None:
        with (
            patch.object(bootstrap.sys.stdin, "isatty", return_value=False),
            patch.object(bootstrap, "ask") as ask,
        ):
            self.assertEqual(bootstrap.concurrency_ceiling(None, "builders"), 3)
            self.assertEqual(bootstrap.concurrency_ceiling(4, "fixers"), 4)
        ask.assert_not_called()

        with (
            patch.object(bootstrap.sys.stdin, "isatty", return_value=True),
            patch.object(bootstrap, "ask", return_value="7") as ask,
        ):
            self.assertEqual(bootstrap.concurrency_ceiling(4, "fixers"), 4)
            self.assertEqual(bootstrap.concurrency_ceiling(None, "builders"), 7)
        self.assertEqual(ask.call_count, 1)
        self.assertIn("may choose fewer", ask.call_args.args[0])

        args = cli.parser().parse_args([
            "init", "--project-name", "example", "--parent-directory", str(self.base), "--provider", "github",
        ])
        with (
            patch.object(bootstrap.shutil, "which", return_value="available"),
            patch.object(bootstrap.sys.stdin, "isatty", return_value=True),
            patch.object(bootstrap, "ask", return_value="0"),
            self.assertRaisesRegex(bootstrap.BootstrapError, "between 1 and 32"),
        ):
            bootstrap.bootstrap(args)
        self.assertFalse((self.base / "example").exists())

    def test_interactive_init_persists_concurrency_ceilings(self) -> None:
        target = self.base / "existing"
        target.mkdir()
        args = cli.parser().parse_args(["init", "--existing-repository-path", str(target)])
        commit = "a" * 40

        def native(command: str, arguments: list[str], *_args: object, **_kwargs: object) -> common.NativeResult:
            if command == "git" and arguments[:2] in (["config", "user.name"], ["config", "user.email"]):
                return common.NativeResult(0, "Test")
            if command == "git" and arguments[:3] == ["diff", "--cached", "--name-only"]:
                return common.NativeResult(0, ".codex/workflow.json\n")
            if command == "git" and arguments[:2] in (["rev-parse", "HEAD"], ["rev-parse", "origin/main"]):
                return common.NativeResult(0, commit)
            return common.NativeResult(0, "")

        with (
            patch.object(bootstrap.shutil, "which", return_value="available"),
            patch.object(bootstrap.sys.stdin, "isatty", return_value=True),
            patch.object(bootstrap, "ask", side_effect=["7", "8"]) as ask,
            patch.object(bootstrap, "existing_details", return_value={
                "root": str(target), "provider": "github", "target": "main",
                "identity": "owner/repository", "repository": "repository", "github_owner": "owner",
            }),
            patch.object(bootstrap, "run_native", side_effect=native),
            patch.object(bootstrap, "initialize_state_files", return_value=common.Paths(target)),
            patch.object(bootstrap, "get_configuration", return_value={}),
            patch.object(bootstrap, "read_json", return_value={"repository": "owner/repository"}),
            patch.object(bootstrap, "info"),
            patch.object(bootstrap, "success"),
        ):
            bootstrap.bootstrap(args)

        workflow = json.loads((target / ".codex" / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(workflow["maximumConcurrentBuilders"], 7)
        self.assertEqual(workflow["maximumConcurrentFixers"], 8)
        self.assertEqual(ask.call_count, 2)

    def test_plan_dispatch_and_expected_error_exit(self) -> None:
        with patch.object(cli.planning, "run") as run:
            self.assertEqual(cli.main(["plan", "repository"]), 0)
            run.assert_called_once_with("repository", False)
        with patch.object(cli.planning, "run", side_effect=BraceError("failed")), patch.object(cli, "error") as report:
            self.assertEqual(cli.main(["plan"]), 1)
            report.assert_called_once_with("failed")
        with patch.object(cli, "show_repository_status") as show:
            self.assertEqual(cli.main(["status", "repository"]), 0)
            show.assert_called_once_with("repository")

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
            "targetBranch": "main", "integrationBranch": "brace/integration", "integrationSha": "abc",
            "blocker": {"message": "operator input required", "requiredDecision": "choose a scope"},
            "updatedAt": "2026-09-09t00:00:00z",
        }
        tasks = {"tasks": [
            {"taskId": "TASK-0001", "status": "integrated", "attemptCount": 1, "lastError": None, "pullRequest": None},
            {"taskId": "TASK-0002", "status": "active", "attemptCount": 2, "lastError": "review interrupted", "pullRequest": None},
        ]}
        bugs = {"bugs": [
            {
                "bugId": "BUG-0001", "status": "verified", "attemptCount": 1, "lastError": None,
                "pullRequest": {"id": "17", "state": "merged", "url": "https://example.invalid/17"},
            },
            {"bugId": "BUG-0002", "status": "active", "attemptCount": 3, "lastError": None, "pullRequest": None},
        ]}
        with patch.object(ui, "console", Console(file=output, force_terminal=False, color_system=None, width=100, theme=ui.THEME)):
            ui.render_status(
                state, tasks, bugs,
                {
                    "TASK-0002": datetime(2026, 9, 9, tzinfo=timezone.utc),
                    "BUG-0002": datetime(2026, 9, 9, 0, 2, 3, tzinfo=timezone.utc),
                },
                datetime(2026, 9, 9, 1, 2, 3, tzinfo=timezone.utc),
            )
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertIn("1/2 complete", rendered)
        self.assertIn("1/2 complete", rendered)
        self.assertIn("TASK-0002 | attempt 2 | elapsed 01:02:03", rendered)
        self.assertIn("BUG-0002 | attempt 3 | elapsed 01:00:00", rendered)
        self.assertIn("review interrupted", rendered)
        self.assertIn("17 | merged", rendered)
        self.assertIn("operator input required", rendered)
        self.assertIn("choose a scope", rendered)

    def test_interactive_status_retains_rich_spinner(self) -> None:
        terminal = Mock(is_terminal=True)
        expected = object()
        terminal.status.return_value = expected
        with patch.object(ui, "console", terminal):
            self.assertIs(ui.status("working"), expected)
        terminal.status.assert_called_once_with("working", spinner="dots", spinner_style="brace")

    def test_plain_agent_updates_are_bounded_and_have_operation_identity_attempt_elapsed(self) -> None:
        output = io.StringIO()
        with patch.object(ui, "console", Console(file=output, force_terminal=False, color_system=None, width=500, theme=ui.THEME)):
            ui.agent_heartbeat("builder", "TASK-0001", 2, 62)
            ui.agent_completed(
                "builder", "TASK-0001", 2, 63,
                "\x1b[31mdone\x1b[0m \x1b]8;;https://example.invalid\x07link\x1b]8;;\x07\n" + "x" * 400,
            )
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertIn("Heartbeat: builder | TASK-0001 | attempt 2 | elapsed 00:01:02", rendered)
        self.assertIn("Completed: builder | TASK-0001 | attempt 2 | elapsed 00:01:03", rendered)
        self.assertIn("…", rendered)
        self.assertNotIn("x" * 241, rendered)
        self.assertNotIn("]8;;", rendered)

    def test_status_reads_validated_state_while_lock_is_owned_without_mutation(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        task = self.task() | {"status": "active", "attemptCount": 2, "lastError": "second reviewer interrupted"}
        task_hash = common.definition_hash([task], "task")
        plan_hash = "gitblob:" + "a" * 40
        state.update(
            stage="build", stageStatus="running", planHash=plan_hash, taskDefinitionHash=task_hash,
        )
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(status="active", planHash=plan_hash, definitionHash=task_hash, tasks=[task])
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        common.write_immutable_json(common.attempt_path(paths, "assignment", task["taskId"], 2), {
            "schemaVersion": "1.0", "identity": task["taskId"], "attempt": 2,
            "baseSha": "a" * 40, "startingHead": "a" * 40,
            "createdAt": "2026-09-09T00:00:00Z", "item": task,
        })
        output = io.StringIO()
        terminal = Console(file=output, force_terminal=False, color_system=None, width=120)
        with common.WorkflowLock(paths.lock):
            before = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in paths.codex.rglob("*") if path.is_file()
                and path != paths.lock
            }
            with (
                patch.object(ui, "console", terminal),
                patch.object(common, "get_repository_identity", side_effect=AssertionError("provider lookup")),
                patch.object(common, "assert_prerequisites", side_effect=AssertionError("provider check")),
            ):
                common.show_repository_status(root)
            after = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in paths.codex.rglob("*") if path.is_file()
                and path != paths.lock
            }
        self.assertEqual(after, before)
        self.assertIn("TASK-0001 | attempt 2 | elapsed", output.getvalue())

    def test_status_reports_missing_and_malformed_state_concisely(self) -> None:
        root, _, config = self.make_repository()
        with self.assertRaisesRegex(BraceError, "not initialized"):
            common.show_repository_status(root)
        paths = common.initialize_state_files(root, config)
        paths.state.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(BraceError, "Unable to read Brace status: Invalid JSON"):
            common.show_repository_status(root)

    def test_status_rejects_malformed_assignment_concisely(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        task = self.task() | {"status": "active", "attemptCount": 1}
        task_hash = common.definition_hash([task], "task")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(status="active", definitionHash=task_hash, tasks=[task])
        state["taskDefinitionHash"] = task_hash
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
        assignment = common.attempt_path(paths, "assignment", task["taskId"], 1)
        assignment.parent.mkdir(parents=True, exist_ok=True)
        assignment.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(BraceError, "Unable to read Brace status: Active assignment record is invalid"):
            common.show_repository_status(root)

    def test_status_rejects_definition_drift(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        task = self.task()
        task_hash = common.definition_hash([task], "task")
        plan_hash = "gitblob:" + "a" * 40
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(status="ready", planHash="gitblob:" + "b" * 40, definitionHash=task_hash, tasks=[task])
        state.update(planHash=plan_hash, taskDefinitionHash=task_hash)
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
        with (
            patch.object(common.time, "sleep"),
            self.assertRaisesRegex(BraceError, "tasks.json plan hash differs"),
        ):
            common.show_repository_status(root)

        tasks["planHash"] = plan_hash
        tasks["tasks"][0]["title"] = "altered after planning"
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        with (
            patch.object(common.time, "sleep"),
            self.assertRaisesRegex(BraceError, "task ledger definitions changed"),
        ):
            common.show_repository_status(root)

    def test_status_rejects_unfrozen_ledger_entries(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(status="ready", tasks=[self.task()])
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        with patch.object(common.time, "sleep"):
            with self.assertRaisesRegex(BraceError, "task ledger"):
                common.show_repository_status(root)

        tasks.update(status="not_planned", tasks=[])
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        bug = {
            "bugId": "BUG-0001", "title": "BUG-0001", "severity": "medium", "category": "correctness",
            "status": "verified", "disposition": "not_reproducible", "requirementIds": ["REQ-ONE"],
            "description": "one defect", "evidence": "reproduction", "actualBehavior": "wrong",
            "requiredBehavior": "right", "impact": "incorrect output", "requiredCorrection": "correct it",
            "acceptanceTest": "output is right", "dependencies": [], "allowedPaths": ["src/**"],
            "exclusiveResources": [], "attemptCount": 0, "branch": None, "worktree": None, "baseSha": None,
            "resultSha": None, "pullRequest": None, "lastError": None, "amendmentId": None,
            "dispositionEvidence": "not reproduced",
        }
        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        bugs.update(status="complete", bugs=[bug])
        common.write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")
        with patch.object(common.time, "sleep"):
            with self.assertRaisesRegex(BraceError, "bug ledger"):
                common.show_repository_status(root)

    def test_status_retries_cross_file_writer_interleaving(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        task = self.task()
        task_hash = common.definition_hash([task], "task")
        plan_hash = "gitblob:" + "a" * 40
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(status="ready", planHash=plan_hash, definitionHash=task_hash, tasks=[task])
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        completed_state = state | {
            "stage": "build", "stageStatus": "not_started", "planHash": plan_hash,
            "taskDefinitionHash": task_hash,
        }
        output = io.StringIO()
        terminal = Console(file=output, force_terminal=False, color_system=None, width=120)

        def finish_cross_file_write(_: float) -> None:
            common.write_json_atomic(paths.state, completed_state, paths.schemas / "state.schema.json")

        with (
            common.WorkflowLock(paths.lock),
            patch.object(common.time, "sleep", side_effect=finish_cross_file_write) as sleep,
            patch.object(ui, "console", terminal),
        ):
            common.show_repository_status(root)
        sleep.assert_called_once()
        self.assertIn("0/1 complete", output.getvalue())

    def test_status_rejects_unrecorded_provider_merge(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base_sha, merge_sha, result_sha = "a" * 40, "b" * 40, "c" * 40
        plan_hash = "gitblob:" + "d" * 40
        task = self.task() | {
            "status": "integrated", "branch": "worktree/TASK-0001", "baseSha": base_sha,
            "resultSha": result_sha, "pullRequest": {
                "id": "1", "url": "https://example.invalid/1", "state": "merged", "repository": "owner/repo",
                "head": "worktree/TASK-0001", "headSha": result_sha, "base": "brace/integration",
                "baseSha": base_sha, "mergeSha": merge_sha,
            },
        }
        task_hash = common.definition_hash([task], "task")
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        state.update(
            stage="build", stageStatus="running", planHash=plan_hash,
            taskDefinitionHash=task_hash, integrationSha=base_sha,
        )
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(status="active", planHash=plan_hash, definitionHash=task_hash, tasks=[task])
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")

        with patch.object(common.time, "sleep") as sleep:
            with self.assertRaisesRegex(BraceError, "provider merge is not recorded"):
                common.show_repository_status(root)
        self.assertEqual(sleep.call_count, common.STATUS_SNAPSHOT_ATTEMPTS - 1)

        def finish_cross_file_write(_: float) -> None:
            state["integrationSha"] = merge_sha
            common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")

        with (
            patch.object(common.time, "sleep", side_effect=finish_cross_file_write) as sleep,
            patch.object(ui, "console", Console(file=io.StringIO(), force_terminal=False)),
        ):
            common.show_repository_status(root)
        sleep.assert_called_once()

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
