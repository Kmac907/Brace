from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(REPOSITORY / "template" / ".codex" / "scripts"))

import common
import project_manager

import new_brace_project as bootstrap


class SafetyTests(unittest.TestCase):
    def test_stale_pull_request_head_is_rejected(self) -> None:
        value = [{
            "number": 7, "url": "https://example.invalid/7", "state": "OPEN",
            "headRefName": "worktree/TASK-0001", "headRefOid": "b" * 40,
            "baseRefName": "brace/integration", "baseRefOid": "c" * 40, "mergeCommit": None,
        }]
        config = {"provider": "github", "github": {"repository": "owner/repo"}}
        with (
            patch.object(common, "get_repository_identity", return_value="owner/repo"),
            patch.object(common, "run_native", return_value=common.NativeResult(0, json.dumps(value))),
            self.assertRaisesRegex(common.BraceError, "different head SHA"),
        ):
            common.get_pull_request(".", config, "worktree/TASK-0001", "brace/integration", "a" * 40)

    def test_multiple_exact_pull_requests_are_rejected(self) -> None:
        record = {
            "number": 7, "url": "https://example.invalid/7", "state": "MERGED",
            "headRefName": "worktree/TASK-0001", "headRefOid": "a" * 40,
            "baseRefName": "brace/integration", "baseRefOid": "c" * 40, "mergeCommit": {"oid": "d" * 40},
        }
        config = {"provider": "github", "github": {"repository": "owner/repo"}}
        with (
            patch.object(common, "get_repository_identity", return_value="owner/repo"),
            patch.object(common, "run_native", return_value=common.NativeResult(0, json.dumps([record, {**record, "number": 8}]))),
            self.assertRaisesRegex(common.BraceError, "Multiple pull requests"),
        ):
            common.get_pull_request(".", config, "worktree/TASK-0001", "brace/integration", "a" * 40)

    def test_immutable_result_cannot_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            common.write_immutable_json(path, {"value": 1})
            common.write_immutable_json(path, {"value": 1})
            with self.assertRaisesRegex(common.BraceError, "already exists with different content"):
                common.write_immutable_json(path, {"value": 2})

    def test_documentation_authority_rejects_coordinator_files(self) -> None:
        option = {"authorizedDocumentationPaths": [".codex/state.json"]}
        with self.assertRaisesRegex(common.BraceError, "safe Markdown path"):
            project_manager.authorized_documentation_paths(option)

    def test_bootstrap_sections_preserve_existing_content_and_reject_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".gitignore"
            path.write_text("existing\n", encoding="utf-8")
            bootstrap.add_section(path, "BRACE", "generated")
            self.assertEqual(path.read_text(encoding="utf-8"), "existing\n\n# BEGIN BRACE\ngenerated\n# END BRACE\n")
            with self.assertRaisesRegex(bootstrap.BootstrapError, "duplicate"):
                bootstrap.add_section(path, "BRACE", "again")

    def test_decision_identity_is_stable_and_input_sensitive(self) -> None:
        first = project_manager.decision_identity("AMEND-0001", "A", "yes", "question")
        self.assertEqual(first, project_manager.decision_identity("AMEND-0001", "A", "yes", "question"))
        self.assertNotEqual(first, project_manager.decision_identity("AMEND-0001", "A", "no", "question"))

    def test_operational_blocker_does_not_enter_pm_workflow(self) -> None:
        with self.assertRaisesRegex(common.BraceError, "Operational blocker"):
            project_manager.invoke_pm_resolution(
                ".",
                {"maximumAmendmentRounds": 3},
                {"activeAmendment": None, "amendmentSequence": 0},
                None,
                {"tasks": []},
                None,
                "build",
                "task",
                "TASK-0001",
                "provider unavailable",
            )


if __name__ == "__main__":
    unittest.main()
