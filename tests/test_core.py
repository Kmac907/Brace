from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from brace import common, project_manager
from support import RepositoryTestCase


class CoreTests(RepositoryTestCase):
    @staticmethod
    def schema_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(CoreTests.schema_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(CoreTests.schema_keys(item) for item in value))
        return set()

    def test_graph_and_coverage(self) -> None:
        first, second = self.task(), self.task("TASK-0002", ["TASK-0001"])
        common.assert_graph([first, second], "task")
        common.assert_task_coverage([first], "REQ-ONE")
        with self.assertRaisesRegex(common.BraceError, "cycle"):
            common.assert_graph([self.task("TASK-0001", ["TASK-0002"]), self.task("TASK-0002", ["TASK-0001"])], "task")
        with self.assertRaisesRegex(common.BraceError, "REQ-TWO"):
            common.assert_task_coverage([first], "REQ-ONE REQ-TWO")

    def test_conflict_scheduling(self) -> None:
        first, second = self.task(paths=["src/a/**"]), self.task("TASK-0002", paths=["src/b/**"])
        self.assertEqual([item["taskId"] for item in common.select_ready_items([first, second], "task", 2)], ["TASK-0001", "TASK-0002"])
        second["allowedPaths"] = ["src/a/file.py"]
        self.assertEqual(len(common.select_ready_items([first, second], "task", 2)), 1)

    def test_blocker_validation(self) -> None:
        operational = project_manager.structured_blocker("failed", "build", "TASK-0001")
        self.assertFalse(project_manager.is_semantic_blocker(operational))
        semantic = dict(operational, kind="scope_gap", requiresUserDecision=True)
        self.assertTrue(project_manager.is_semantic_blocker(semantic))
        with self.assertRaisesRegex(common.BraceError, "evidence"):
            project_manager.structured_blocker(dict(semantic, evidence=""), "build", "TASK-0001")

    def test_role_prompt_recommends_optional_ponytail(self) -> None:
        root, _, _ = self.make_repository()
        (root / ".codex" / "prompts" / "builder.md").write_text("builder role", encoding="utf-8")
        with patch.object(common, "invoke_codex", return_value={}) as invoke:
            common.invoke_role(root, root, "builder", "task", "builder-result.schema.json", "workspace-write")
        prompt = invoke.call_args.args[0]
        self.assertIn("load and use it at full level", prompt)
        self.assertIn("optional and its absence is not a blocker", prompt)
        self.assertIn("required JSON output schema overrides", prompt)

    def test_pm_analysis_rejects_unknown_task(self) -> None:
        analysis = {
            "affectedTaskIds": ["TASK-9999"], "affectedBugIds": [], "amendmentRequired": True,
            "options": [{"optionId": "A", "recommended": True, "requiresInput": False, "inputPrompt": None, "action": "amend", "authorizedDocumentationPaths": [], "bugDispositions": []}],
        }
        with self.assertRaisesRegex(common.BraceError, "unknown task"):
            project_manager.assert_pm_analysis(analysis, {"tasks": [self.task()]}, None, "build")

    def test_native_timeout_is_bounded(self) -> None:
        self.assertEqual(common.run_native(sys.executable, ["-c", "raise SystemExit(7)"], allowed_exit_codes=None).returncode, 7)
        started = time.monotonic()
        with self.assertRaisesRegex(common.BraceError, "deadline"):
            common.run_native(sys.executable, ["-c", "import time; time.sleep(30)"], timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 8)

    def test_output_schema_projection_supports_bundled_results(self) -> None:
        root, _, config = self.make_repository()
        schemas = common.initialize_state_files(root, config).schemas
        projected = {path.name: common._project_output_schema(path) for path in sorted(schemas.glob("*-result.schema.json"))}
        self.assertEqual(set(projected), {
            "audit-result.schema.json", "builder-result.schema.json", "fixer-result.schema.json",
            "planning-result.schema.json", "pm-amendment-result.schema.json",
            "pm-blocker-result.schema.json", "verifier-result.schema.json",
        })
        self.assertNotIn("uniqueItems", self.schema_keys(projected["planning-result.schema.json"]))
        for name in ("audit-result.schema.json", "builder-result.schema.json", "fixer-result.schema.json", "verifier-result.schema.json"):
            self.assertTrue(self.schema_keys(projected[name]).isdisjoint({"allOf", "if", "then", "else"}))
        blocker = projected["builder-result.schema.json"]["properties"]["blocker"]
        self.assertIn("anyOf", blocker)
        self.assertTrue(self.schema_keys(blocker).isdisjoint({"$schema", "$defs"}))
        self.assertNotIn("oneOf", self.schema_keys(blocker))
        self.assertNotIn("blocker.schema.json", json.dumps(projected["builder-result.schema.json"]))
        self.assertEqual(projected["pm-amendment-result.schema.json"]["properties"]["newTasks"]["items"]["$ref"], "#/$defs/task")

    def test_output_schema_projection_rejects_unsafe_references_before_launch(self) -> None:
        schema_directory = self.base / "schemas"
        schema_directory.mkdir()
        (self.base / "outside.json").write_text("{}", encoding="utf-8")
        for reference, error in (("https://example.invalid/schema.json", "Remote"), ("../outside.json", "leaves"), ("missing.json", "does not exist")):
            schema = schema_directory / "result.schema.json"
            schema.write_text(json.dumps({
                "type": "object", "required": ["value"],
                "properties": {"value": {"$ref": reference}}, "additionalProperties": False,
            }), encoding="utf-8")
            with self.subTest(reference=reference), patch.object(common.subprocess, "Popen") as process:
                with self.assertRaisesRegex(common.BraceError, error):
                    common.invoke_codex("prompt", self.base, schema, "read-only", self.base / "logs")
                process.assert_not_called()

    def test_invoke_codex_uses_temporary_projection_and_full_local_validation(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        schema_path = paths.schemas / "planning-result.schema.json"
        result = {
            "status": "complete", "questions": [], "normalizedRequirementsMarkdown": "requirements",
            "planMarkdown": "plan", "tasks": [],
            "summary": {"requirementsCount": 1, "taskCount": 0, "parallelizableTaskCount": 0,
                        "assumptions": [], "deferredScope": [], "deferredRequirementIds": []},
        }
        outcomes = [result, result | {"summary": result["summary"] | {"deferredRequirementIds": ["REQ-ONE", "REQ-ONE"]}}]
        captured: list[tuple[Path, dict]] = []

        class Process:
            returncode = 0

            def __init__(self, command: list[str], **_: object):
                self.command = command

            def communicate(self, _: str, timeout: int) -> tuple[str, str]:
                self.assert_timeout = timeout
                projected_path = Path(self.command[self.command.index("--output-schema") + 1])
                captured.append((projected_path, common.read_json(projected_path)))
                result_path = Path(self.command[self.command.index("--output-last-message") + 1])
                result_path.write_text(json.dumps(outcomes.pop(0)), encoding="utf-8")
                return "", ""

        executable_directory = self.base / "bin"
        executable_directory.mkdir()
        executable = executable_directory / ("codex.cmd" if os.name == "nt" else "codex")
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o755)
        environment = {"PATH": str(executable_directory) + os.pathsep + os.environ.get("PATH", "")}
        with patch.dict(os.environ, environment), patch.object(common.subprocess, "Popen", Process):
            self.assertEqual(common.invoke_codex("prompt", root, schema_path, "read-only", paths.logs), result)
            self.assertFalse(captured[0][0].exists())
            self.assertNotEqual(captured[0][0], schema_path.resolve())
            self.assertNotIn("uniqueItems", self.schema_keys(captured[0][1]))
            with self.assertRaisesRegex(common.BraceError, "unique"):
                common.invoke_codex("prompt", root, schema_path, "read-only", paths.logs)
            self.assertFalse(captured[1][0].exists())

    def test_state_creation_and_schema_validation(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        self.assertEqual(state["repository"], "owner/repo")
        common.assert_state_identity(state, root, config)
        invalid = dict(state, stage="impossible")
        with self.assertRaises(common.BraceError):
            common.write_json_atomic(paths.state.with_name("invalid.json"), invalid, paths.schemas / "state.schema.json")
        sha = "a" * 40
        blocker = dict(project_manager.structured_blocker("scope changed", "build", "TASK-0001"), kind="scope_gap", requiresUserDecision=True, scopeChangePossible=True)
        state.update(amendmentSequence=1, activeAmendment={
            "amendmentId": "AMEND-0001", "sourceStage": "build", "sourceKind": "task", "sourceIdentity": "TASK-0001",
            "status": "submitted", "blocker": blocker, "analysisResultPath": "analysis.json", "selectedOptionId": "OPTION-0001",
            "userResponse": "approve", "decisionIdentity": "sha256:" + "b" * 64, "authorizedDocumentationPaths": ["requirements.md", "plan.md"],
            "branch": "worktree/AMEND-0001", "worktree": "worktree", "baseSha": sha, "resultSha": sha,
            "pullRequest": {"id": "1", "url": "https://example.invalid/1", "state": "open", "repository": "owner/repo", "head": "worktree/AMEND-0001", "headSha": sha, "base": "brace/integration", "baseSha": sha, "mergeSha": None},
            "affectedTaskIds": ["TASK-0001"], "affectedBugIds": [], "resumeStage": "build", "attemptCount": 1,
        })
        common.write_json_atomic(paths.state.with_name("referenced.json"), state, paths.schemas / "state.schema.json")

    def test_verifier_rejection_allows_findings_without_blocker(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        common.write_json_atomic(paths.logs / "rejected.json", {
            "approved": False, "summary": "changes required", "findings": ["fix it"], "checks": [], "blocker": None,
        }, paths.schemas / "verifier-result.schema.json")

    def test_worktree_commit_scope_and_amendment_identity(self) -> None:
        root, _, config = self.make_repository()
        base = self.git(root, "rev-parse", "HEAD")
        task = self.task(paths=["src/**"])
        path = common.new_worktree(root, config, "TASK-0001", "worktree/TASK-0001", base)
        (path / "src").mkdir()
        (path / "src" / "value.txt").write_text("ok", encoding="utf-8")
        self.git(path, "add", "src/value.txt")
        self.git(path, "commit", "-m", "task")
        result = common.assert_assignment_commit(path, base, task)
        self.assertRegex(result["Head"], r"^[0-9a-f]{40,64}$")
        task.update(branch="worktree/TASK-0001", baseSha=base, resultSha=result["Head"])
        common.reset_rejected_assignment(root, config, task, "task")
        common.reset_rejected_assignment(root, config, task, "task")
        self.assertEqual(self.git(path, "rev-parse", "HEAD"), base)
        common.remove_worktree(root, config, "TASK-0001", "worktree/TASK-0001")
        amendment = common.new_worktree(root, config, "AMEND-0001", "worktree/AMEND-0001", base)
        self.assertEqual(self.git(amendment, "branch", "--show-current"), "worktree/AMEND-0001")
        common.remove_worktree(root, config, "AMEND-0001", "worktree/AMEND-0001")

    def test_unknown_integration_commit_is_rejected(self) -> None:
        root, remote, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        first = common.ensure_integration_branch(root, config, state)
        state["integrationSha"] = first
        clone = self.base / "other"
        subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
        self.git(clone, "config", "user.name", "Other")
        self.git(clone, "config", "user.email", "other@example.invalid")
        self.git(clone, "switch", "-c", "brace/integration", "--track", "origin/brace/integration")
        (clone / "unknown.txt").write_text("unknown", encoding="utf-8")
        self.git(clone, "add", "unknown.txt")
        self.git(clone, "commit", "-m", "unknown")
        self.git(clone, "push", "origin", "brace/integration")
        with self.assertRaisesRegex(common.BraceError, "unowned commits"):
            common.ensure_integration_branch(root, config, state)
