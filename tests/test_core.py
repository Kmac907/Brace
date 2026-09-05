from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "template" / ".codex" / "scripts"
SOURCE_ROOT = Path(os.environ.get("RALPH_SOURCE_ROOT", REPOSITORY))
sys.path.insert(0, str(SCRIPTS))

import common
import project_manager
from support import RepositoryTestCase


class CoreTests(RepositoryTestCase):
    def test_graph_and_coverage(self) -> None:
        first, second = self.task(), self.task("TASK-0002", ["TASK-0001"])
        common.assert_graph([first, second], "task")
        common.assert_task_coverage([first], "REQ-ONE")
        with self.assertRaisesRegex(common.RalphError, "cycle"):
            common.assert_graph([self.task("TASK-0001", ["TASK-0002"]), self.task("TASK-0002", ["TASK-0001"])], "task")
        with self.assertRaisesRegex(common.RalphError, "REQ-TWO"):
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
        with self.assertRaisesRegex(common.RalphError, "evidence"):
            project_manager.structured_blocker(dict(semantic, evidence=""), "build", "TASK-0001")

    def test_pm_analysis_rejects_unknown_task(self) -> None:
        analysis = {
            "affectedTaskIds": ["TASK-9999"], "affectedBugIds": [], "amendmentRequired": True,
            "options": [{"optionId": "A", "recommended": True, "requiresInput": False, "inputPrompt": None, "action": "amend", "authorizedDocumentationPaths": [], "bugDispositions": []}],
        }
        with self.assertRaisesRegex(common.RalphError, "unknown task"):
            project_manager.assert_pm_analysis(analysis, {"tasks": [self.task()]}, None, "build")

    def test_native_timeout_is_bounded(self) -> None:
        started = time.monotonic()
        with self.assertRaisesRegex(common.RalphError, "deadline"):
            common.run_native(sys.executable, ["-c", "import time; time.sleep(30)"], timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 8)

    def test_state_creation_and_schema_validation(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        self.assertEqual(state["repository"], "owner/repo")
        common.assert_state_identity(state, root, config)
        invalid = dict(state, stage="impossible")
        with self.assertRaises(common.RalphError):
            common.write_json_atomic(paths.state.with_name("invalid.json"), invalid, paths.schemas / "state.schema.json")

    def test_worktree_commit_scope(self) -> None:
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
        common.remove_worktree(root, config, "TASK-0001", "worktree/TASK-0001")

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
        self.git(clone, "switch", "-c", "ralph/integration", "--track", "origin/ralph/integration")
        (clone / "unknown.txt").write_text("unknown", encoding="utf-8")
        self.git(clone, "add", "unknown.txt")
        self.git(clone, "commit", "-m", "unknown")
        self.git(clone, "push", "origin", "ralph/integration")
        with self.assertRaisesRegex(common.RalphError, "unowned commits"):
            common.ensure_integration_branch(root, config, state)


if __name__ == "__main__":
    unittest.main()
