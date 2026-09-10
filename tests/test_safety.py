from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brace import bootstrap, common, project_manager


class SafetyTests(unittest.TestCase):
    def pull_request(self, provider: str = "github", state: str | None = None) -> dict:
        return {
            "id": "7", "url": "https://example.invalid/7", "state": state or ("open" if provider == "github" else "active"),
            "repository": "owner/repo" if provider == "github" else "https://dev.azure.com/owner|project|repo",
            "head": "worktree/TASK-0001", "headSha": "b" * 40,
            "base": "brace/integration", "baseSha": "a" * 40, "mergeSha": None,
        }

    def provider_config(self, provider: str = "github") -> dict:
        return {
            "provider": provider, "remote": "origin", "integrationBranch": "brace/integration", "deleteMergedBranches": True,
            "github": {"repository": "owner/repo"},
            "azureDevOps": {"organization": "https://dev.azure.com/owner", "project": "project", "repository": "repo"},
        }

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

    def test_github_merge_waits_and_matches_the_reviewed_head(self) -> None:
        pull_request = self.pull_request()
        merged = {**pull_request, "state": "merged", "baseSha": "c" * 40, "mergeSha": "d" * 40}
        commands: list[tuple[str, list[str]]] = []

        def native(command, arguments=(), *args, **kwargs):
            arguments = list(arguments)
            commands.append((command, arguments))
            if command == "git" and "rev-parse" in arguments:
                return common.NativeResult(0, pull_request["baseSha"] if arguments[-1].endswith("^1") else "e" * 40)
            return common.NativeResult(0, "")

        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", side_effect=[pull_request, pull_request, merged]),
            patch.object(common, "run_native", side_effect=native),
        ):
            result = common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], pull_request["baseSha"])

        self.assertEqual(result["mergeSha"], merged["mergeSha"])
        self.assertIn(("gh", ["pr", "checks", "7", "--repo", "owner/repo", "--required", "--watch", "--fail-fast"]), commands)
        merge = next(arguments for command, arguments in commands if command == "gh" and arguments[:2] == ["pr", "merge"])
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], pull_request["headSha"])
        self.assertNotIn("--admin", merge)

    def test_head_change_during_check_wait_blocks_merge(self) -> None:
        pull_request = self.pull_request()
        changed = {**pull_request, "headSha": "c" * 40}
        commands: list[list[str]] = []

        def native(command, arguments=(), *args, **kwargs):
            commands.append(list(arguments))
            return common.NativeResult(0, "")

        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", side_effect=[pull_request, changed]),
            patch.object(common, "run_native", side_effect=native),
            self.assertRaisesRegex(common.BraceError, "identity changed.*headSha"),
        ):
            common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], pull_request["baseSha"])
        self.assertFalse(any(arguments[:2] == ["pr", "merge"] for arguments in commands))

    def test_pending_or_failed_required_github_checks_block_merge(self) -> None:
        pull_request = self.pull_request()
        for returncode, output in ((8, "checks pending"), (1, "build fail"), (1, "no required checks reported: provider failure")):
            with self.subTest(returncode=returncode):
                commands: list[list[str]] = []

                def native(command, arguments=(), *args, **kwargs):
                    commands.append(list(arguments))
                    return common.NativeResult(returncode, output)

                with (
                    patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
                    patch.object(common, "get_pull_request", return_value=pull_request),
                    patch.object(common, "run_native", side_effect=native),
                    self.assertRaisesRegex(common.BraceError, "checks did not pass"),
                ):
                    common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], pull_request["baseSha"])
                self.assertFalse(any(arguments[:2] == ["pr", "merge"] for arguments in commands))

    def test_empty_required_github_check_set_is_allowed(self) -> None:
        pull_request = self.pull_request()
        response = "no required checks reported on the 'worktree/TASK-0001' branch"
        with patch.object(common, "run_native", return_value=common.NativeResult(1, response)):
            common._wait_for_required_checks(".", self.provider_config(), pull_request)

    def test_azure_source_sha_mismatch_blocks_completion(self) -> None:
        pull_request = self.pull_request("azure_devops")
        changed = {**pull_request, "headSha": "c" * 40}
        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", return_value=changed),
            patch.object(common, "run_native") as native,
            self.assertRaisesRegex(common.BraceError, "identity changed.*headSha"),
        ):
            common.complete_pull_request(".", self.provider_config("azure_devops"), pull_request, pull_request["headSha"], pull_request["baseSha"])
        native.assert_not_called()

    def test_incomplete_blocking_azure_policy_blocks_completion(self) -> None:
        pull_request = self.pull_request("azure_devops")
        policy = [{"isBlocking": True, "status": "running", "displayName": "CI"}]
        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", return_value=pull_request),
            patch.object(common, "run_native", return_value=common.NativeResult(0, json.dumps(policy))) as native,
            self.assertRaisesRegex(common.BraceError, "policies did not pass: CI"),
        ):
            common.complete_pull_request(".", self.provider_config("azure_devops"), pull_request, pull_request["headSha"], pull_request["baseSha"])
        self.assertFalse(any(call.args[1][:3] == ["repos", "pr", "update"] for call in native.call_args_list))

    def test_post_merge_requires_reviewed_parent_and_remote_containment(self) -> None:
        pull_request = self.pull_request(state="merged") | {"mergeSha": "d" * 40}

        def native(command, arguments=(), *args, **kwargs):
            arguments = list(arguments)
            if arguments[-1:] == [f"{pull_request['mergeSha']}^1"]:
                return common.NativeResult(0, pull_request["baseSha"])
            if "merge-base" in arguments:
                return common.NativeResult(1, "")
            return common.NativeResult(0, "e" * 40 if "rev-parse" in arguments else "")

        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", return_value=pull_request),
            patch.object(common, "run_native", side_effect=native),
            self.assertRaisesRegex(common.BraceError, "Remote base does not contain"),
        ):
            common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], pull_request["baseSha"])

    def test_merged_recovery_revalidates_checks_and_reviewed_base(self) -> None:
        reviewed_base = "a" * 40
        pull_request = self.pull_request(state="merged") | {"baseSha": "c" * 40, "mergeSha": "d" * 40}
        commands: list[list[str]] = []

        def native(command, arguments=(), *args, **kwargs):
            arguments = list(arguments)
            commands.append(arguments)
            if arguments[-1:] == [f"{pull_request['mergeSha']}^1"]:
                return common.NativeResult(0, reviewed_base)
            return common.NativeResult(0, "e" * 40 if "rev-parse" in arguments else "")

        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", return_value=pull_request),
            patch.object(common, "run_native", side_effect=native),
        ):
            recovered = common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], reviewed_base)
        self.assertEqual(recovered["baseSha"], reviewed_base)
        self.assertTrue(any(arguments[:2] == ["pr", "checks"] for arguments in commands))
        self.assertFalse(any(arguments[:2] == ["pr", "merge"] for arguments in commands))

    def test_merged_recovery_rejects_failed_or_pending_checks(self) -> None:
        pull_request = self.pull_request(state="merged") | {"mergeSha": "d" * 40}
        for returncode, output in ((8, "checks pending"), (1, "build fail")):
            with self.subTest(returncode=returncode):
                with (
                    patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
                    patch.object(common, "get_pull_request", return_value=pull_request),
                    patch.object(common, "run_native", return_value=common.NativeResult(returncode, output)),
                    self.assertRaisesRegex(common.BraceError, "checks did not pass"),
                ):
                    common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], pull_request["baseSha"])

    def test_merged_recovery_rejects_missing_provider_record(self) -> None:
        pull_request = self.pull_request(state="merged") | {"mergeSha": "d" * 40}
        with (
            patch.object(common, "get_repository_identity", return_value=pull_request["repository"]),
            patch.object(common, "get_pull_request", return_value=None),
            patch.object(common, "run_native") as native,
            self.assertRaisesRegex(common.BraceError, "verifiable merged pull request"),
        ):
            common.complete_pull_request(".", self.provider_config(), pull_request, pull_request["headSha"], pull_request["baseSha"])
        native.assert_not_called()

    def test_failed_publication_preserves_assignment_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worktree = Path(directory) / "worktree"
            worktree.mkdir()
            item = {"taskId": "TASK-0001", "title": "Task", "branch": "worktree/TASK-0001", "baseSha": "a" * 40, "resultSha": "b" * 40}
            pull_request = self.pull_request()

            def native(command, arguments=(), *args, **kwargs):
                if command == "git" and list(arguments)[-2:-1] == ["rev-parse"]:
                    return common.NativeResult(0, item["baseSha"])
                return common.NativeResult(0, "")

            with (
                patch.object(common, "run_native", side_effect=native),
                patch.object(common, "new_pull_request", return_value=pull_request),
                patch.object(common, "complete_pull_request", side_effect=common.BraceError("merge blocked")),
                self.assertRaisesRegex(common.BraceError, "merge blocked"),
            ):
                common.publish_assignment(".", worktree, self.provider_config(), item, "task")
            self.assertTrue(worktree.is_dir())

    def test_stale_review_is_rejected_before_source_branch_push(self) -> None:
        item = {"taskId": "TASK-0001", "title": "Task", "branch": "worktree/TASK-0001", "baseSha": "a" * 40, "resultSha": "b" * 40}
        commands: list[list[str]] = []

        def native(command, arguments=(), *args, **kwargs):
            commands.append(list(arguments))
            return common.NativeResult(0, "c" * 40 if "rev-parse" in arguments else "")

        with patch.object(common, "run_native", side_effect=native), self.assertRaisesRegex(common.BraceError, "Integration base changed"):
            common.publish_assignment(".", ".", self.provider_config(), item, "task")
        self.assertFalse(any("push" in arguments for arguments in commands))

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
