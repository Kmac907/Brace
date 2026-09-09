from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from brace import common, project_manager
from support import RepositoryTestCase


class CoreTests(RepositoryTestCase):
    @staticmethod
    def reviewer_result(approved: bool = True) -> dict:
        finding = {
            "severity": "medium", "location": "src/product.py:1", "expectedBehavior": "correct output",
            "actualBehavior": "incorrect output", "evidence": "focused check failed",
            "impact": "users receive an incorrect result", "classification": "Observed",
        }
        return {
            "approved": approved,
            "summary": "No reproducible defect found in the reviewed scope." if approved else "changes required",
            "findings": [] if approved else [finding], "checks": [], "blocker": None,
            "ponytailVerdict": f"Ponytail verdict: {'supported' if approved else 'rejected'} — inspected diff evidence.",
        }

    def windows_process_is_running(self, pid: int) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return False
            self.fail(f"OpenProcess failed for PID {pid} with Windows error {error}.")
        try:
            status = kernel32.WaitForSingleObject(handle, 0)
        finally:
            kernel32.CloseHandle(handle)
        if status == 0:
            return False
        if status == 258:
            return True
        self.fail(f"WaitForSingleObject failed for PID {pid} with status {status}.")

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

    def test_coordinator_canonicalizes_graph_identities_and_references(self) -> None:
        first = self.task("TASK-0018")
        second = self.task("TASK-0028", ["TASK-0018"])
        mapping = common.canonicalize_graph_identities([first, second], "task")
        self.assertEqual(mapping, {"TASK-0018": "TASK-0001", "TASK-0028": "TASK-0002"})
        self.assertEqual((first["taskId"], second["taskId"], second["dependencies"]), ("TASK-0001", "TASK-0002", ["TASK-0001"]))
        plan = "Implement TASK-001, then TASK-0028; leave X-TASK-0018-extra unchanged."
        self.assertEqual(
            common.normalize_task_references(plan, mapping, (first["taskId"], second["taskId"])),
            "Implement TASK-0001, then TASK-0002; leave X-TASK-0018-extra unchanged.",
        )

        bug = {"bugId": "BUG-0018", "dependencies": [], "allowedPaths": ["src/**"]}
        common.canonicalize_graph_identities([bug], "bug")
        self.assertEqual(bug["bugId"], "BUG-0001")
        follow_up = self.task("TASK-0018", ["TASK-0002"])
        common.canonicalize_graph_identities([follow_up], "task", 3, ["TASK-0001", "TASK-0002"])
        self.assertEqual((follow_up["taskId"], follow_up["dependencies"]), ("TASK-0003", ["TASK-0002"]))

    def test_coordinator_rejects_ambiguous_or_unknown_identities(self) -> None:
        with self.assertRaisesRegex(common.BraceError, "Duplicate provisional task identity"):
            common.canonicalize_graph_identities([self.task("TASK-0018"), self.task("TASK-0018")], "task")
        with self.assertRaisesRegex(common.BraceError, "Provisional task identity conflicts with existing identity: TASK-0001"):
            common.canonicalize_graph_identities(
                [self.task("TASK-0001"), self.task("TASK-0018", ["TASK-0001"])],
                "task",
                3,
                ["TASK-0001", "TASK-0002"],
            )
        with self.assertRaisesRegex(common.BraceError, "Unresolved task dependency collides with canonical identity: TASK-0001"):
            common.canonicalize_graph_identities([self.task("TASK-0018"), self.task("TASK-0028", ["TASK-0001"])], "task")
        bugs = [
            {"bugId": "BUG-0018", "dependencies": [], "allowedPaths": ["src/**"]},
            {"bugId": "BUG-0028", "dependencies": ["BUG-0001"], "allowedPaths": ["src/**"]},
        ]
        with self.assertRaisesRegex(common.BraceError, "Unresolved bug dependency collides with canonical identity: BUG-0001"):
            common.canonicalize_graph_identities(bugs, "bug")
        task = self.task("TASK-0018", ["TASK-9999"])
        common.canonicalize_graph_identities([task], "task")
        with self.assertRaisesRegex(common.BraceError, "unknown task TASK-9999"):
            common.assert_graph([task], "task")
        with self.assertRaisesRegex(common.BraceError, "Plan references unknown tasks: TASK-9999"):
            common.normalize_task_references("Implement TASK-9999.", {}, ["TASK-0001"])

    def assert_pm_follow_up_recovery(self, provisional_id: str) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        head = self.git(root, "rev-parse", "HEAD")
        identity = "AMEND-0001"
        initial = [self.task(), self.task("TASK-0002")]
        plan_hash = common.git_blob_identity(root, head, "plan.md")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(revision=1, planHash=plan_hash, definitionHash=common.definition_hash(initial, "task"), status="active", tasks=initial)
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")

        analysis = {
            "summary": "append follow-up", "recommendation": "amend", "question": "Proceed?", "amendmentRequired": True,
            "effects": {name: "updated" for name in ("requirements", "plan", "tasks", "bugs", "completedWork", "schedule")},
            "options": [{
                "optionId": "OPTION-0001", "label": "Amend", "description": "Append work", "recommended": True,
                "action": "amend", "requiresInput": False, "inputPrompt": None,
                "authorizedDocumentationPaths": [], "bugDispositions": [],
            }],
            "affectedTaskIds": ["TASK-0001"], "affectedBugIds": [],
        }
        analysis_path = paths.results / f"{identity}-analysis.json"
        common.write_immutable_json(analysis_path, analysis)
        decision = project_manager.decision_identity(identity, "OPTION-0001", "OPTION-0001", analysis["question"])
        pull_request = {
            "id": "1", "url": "https://example.invalid/1", "state": "merged", "repository": "owner/repo",
            "head": f"worktree/{identity}", "headSha": head, "base": config["integrationBranch"], "baseSha": head, "mergeSha": head,
        }
        blocker = dict(project_manager.structured_blocker("scope changed", "build", "TASK-0001"), kind="scope_gap", requiresUserDecision=True, scopeChangePossible=True)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        state.update(
            stage="build", stageStatus="amending", targetBaseSha=head, integrationSha=head, planHash=plan_hash,
            requirementsHash=common.git_blob_identity(root, head, "requirements.md"), taskDefinitionHash=tasks["definitionHash"],
            amendmentSequence=1, activeAmendment={
                "amendmentId": identity, "sourceStage": "build", "sourceKind": "task", "sourceIdentity": "TASK-0001",
                "status": "integrated", "blocker": blocker, "analysisResultPath": str(analysis_path),
                "selectedOptionId": "OPTION-0001", "userResponse": "OPTION-0001", "decisionIdentity": decision,
                "authorizedDocumentationPaths": ["requirements.md", "plan.md"], "branch": f"worktree/{identity}",
                "worktree": None, "baseSha": head, "resultSha": head, "pullRequest": pull_request,
                "affectedTaskIds": ["TASK-0001"], "affectedBugIds": [], "resumeStage": "build", "attemptCount": 1,
            },
        )
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
        definition = {
            key: value for key, value in self.task(provisional_id, ["TASK-0002"]).items()
            if key in {"taskId", "title", "description", "requirementIds", "planSections", "dependencies", "allowedPaths", "exclusiveResources", "acceptanceCriteria", "checks"}
        }
        result = {
            "status": "completed", "summary": "amended", "decisionIdentity": decision, "selectedOptionId": "OPTION-0001",
            "commitSha": head, "changedFiles": ["plan.md"], "newTasks": [definition], "supersededTaskIds": [],
            "resumeStage": "build", "blocker": None,
        }
        result_path = common.attempt_path(paths, "result", identity, 1)
        common.write_immutable_json(result_path, {
            "schemaVersion": "1.0", "identity": identity, "attempt": 1, "succeeded": True,
            "result": result, "error": None, "completedAt": common.utc_now(),
        })

        with patch.object(project_manager, "save_state", side_effect=RuntimeError("interrupted after task ledger")):
            with self.assertRaisesRegex(RuntimeError, "interrupted after task ledger"):
                project_manager.invoke_pm_resolution(root, config, state, paths, tasks, common.read_json(paths.bugs), "build", "task", "TASK-0001", blocker)

        interrupted_tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        interrupted_revision = interrupted_tasks["revision"]
        persisted_state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        self.assertEqual(persisted_state["activeAmendment"]["status"], "integrated")
        self.assertEqual([task["taskId"] for task in interrupted_tasks["tasks"]], ["TASK-0001", "TASK-0002", "TASK-0003"])

        resolution = project_manager.invoke_pm_resolution(
            root, config, persisted_state, paths, interrupted_tasks, common.read_json(paths.bugs),
            "build", "task", "TASK-0001", blocker,
        )
        recovered_tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        recovered_state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        self.assertEqual(resolution["action"], "amended")
        self.assertEqual(recovered_tasks["revision"], interrupted_revision)
        self.assertEqual([task["taskId"] for task in recovered_tasks["tasks"]], ["TASK-0001", "TASK-0002", "TASK-0003"])
        self.assertIsNone(recovered_state["activeAmendment"])
        common.assert_ledger_identity(recovered_state, recovered_tasks, "task")
        self.assertEqual(common.read_json(result_path)["result"]["newTasks"][0]["taskId"], provisional_id)

    def test_pm_follow_up_recovery_is_idempotent_for_provisional_result(self) -> None:
        self.assert_pm_follow_up_recovery("TASK-0018")

    def test_pm_follow_up_recovery_is_idempotent_for_canonical_result(self) -> None:
        self.assert_pm_follow_up_recovery("TASK-0003")

    def pm_result_ready_fixture(self, plan: str) -> tuple:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base = self.git(root, "rev-parse", "HEAD")
        identity = "AMEND-0001"
        worktree = common.new_worktree(root, config, identity, f"worktree/{identity}", base)
        (worktree / "plan.md").write_text(plan, encoding="utf-8")
        self.git(worktree, "add", "plan.md")
        self.git(worktree, "commit", "-m", "amend plan")
        result_sha = self.git(worktree, "rev-parse", "HEAD")

        initial = [self.task(), self.task("TASK-0002")]
        plan_hash = common.git_blob_identity(root, base, "plan.md")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        tasks.update(revision=1, planHash=plan_hash, definitionHash=common.definition_hash(initial, "task"), status="active", tasks=initial)
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")

        analysis_path = paths.results / f"{identity}-analysis.json"
        analysis = {
            "summary": "append follow-up", "recommendation": "amend", "question": "Proceed?", "amendmentRequired": True,
            "effects": {name: "updated" for name in ("requirements", "plan", "tasks", "bugs", "completedWork", "schedule")},
            "options": [{
                "optionId": "OPTION-0001", "label": "Amend", "description": "Append work", "recommended": True,
                "action": "amend", "requiresInput": False, "inputPrompt": None,
                "authorizedDocumentationPaths": [], "bugDispositions": [],
            }],
            "affectedTaskIds": ["TASK-0001"], "affectedBugIds": [],
        }
        common.write_immutable_json(analysis_path, analysis)
        decision = project_manager.decision_identity(identity, "OPTION-0001", "OPTION-0001", analysis["question"])
        blocker = dict(project_manager.structured_blocker("scope changed", "build", "TASK-0001"), kind="scope_gap", requiresUserDecision=True, scopeChangePossible=True)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        state.update(
            stage="build", stageStatus="amending", targetBaseSha=base, integrationSha=base, planHash=plan_hash,
            requirementsHash=common.git_blob_identity(root, base, "requirements.md"), taskDefinitionHash=tasks["definitionHash"],
            amendmentSequence=1, activeAmendment={
                "amendmentId": identity, "sourceStage": "build", "sourceKind": "task", "sourceIdentity": "TASK-0001",
                "status": "result_ready", "blocker": blocker, "analysisResultPath": str(analysis_path),
                "selectedOptionId": "OPTION-0001", "userResponse": "OPTION-0001", "decisionIdentity": decision,
                "authorizedDocumentationPaths": ["requirements.md", "plan.md"], "branch": f"worktree/{identity}",
                "worktree": str(worktree), "baseSha": base, "resultSha": None, "pullRequest": None,
                "affectedTaskIds": ["TASK-0001"], "affectedBugIds": [], "resumeStage": "build", "attemptCount": 1,
            },
        )
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
        definition = {
            key: value for key, value in self.task("TASK-0018", ["TASK-0002"]).items()
            if key in {"taskId", "title", "description", "requirementIds", "planSections", "dependencies", "allowedPaths", "exclusiveResources", "acceptanceCriteria", "checks"}
        }
        result_path = common.attempt_path(paths, "result", identity, 1)
        common.write_immutable_json(result_path, {
            "schemaVersion": "1.0", "identity": identity, "attempt": 1, "succeeded": True,
            "result": {
                "status": "completed", "summary": "amended", "decisionIdentity": decision,
                "selectedOptionId": "OPTION-0001", "commitSha": result_sha, "changedFiles": ["plan.md"],
                "newTasks": [definition], "supersededTaskIds": [], "resumeStage": "build", "blocker": None,
            }, "error": None, "completedAt": common.utc_now(),
        })
        return root, config, paths, state, tasks, blocker, worktree, base, result_sha, result_path

    def test_pm_plan_normalization_is_durable_and_replay_safe(self) -> None:
        raw_plan = "# Plan\n\nImplement TASK-0018, then TASK-003. Preserve X-TASK-0018-extra.\n"
        root, config, paths, state, tasks, blocker, worktree, base, result_sha, result_path = self.pm_result_ready_fixture(raw_plan)

        with patch.object(project_manager, "save_state", side_effect=RuntimeError("interrupted after normalization")):
            with self.assertRaisesRegex(RuntimeError, "interrupted after normalization"):
                project_manager.invoke_pm_resolution(root, config, state, paths, tasks, None, "build", "task", "TASK-0001", blocker)

        normalized_sha = self.git(worktree, "rev-parse", "HEAD")
        self.assertNotEqual(normalized_sha, result_sha)
        self.assertEqual(self.git(worktree, "rev-list", "--count", f"{base}..{normalized_sha}"), "2")
        self.assertEqual(self.git(worktree, "show", f"{result_sha}:plan.md"), raw_plan.strip())
        self.assertEqual(
            self.git(worktree, "show", f"{normalized_sha}:plan.md"),
            "# Plan\n\nImplement TASK-0003, then TASK-0003. Preserve X-TASK-0018-extra.",
        )
        self.assertIsNone(common.read_json(paths.state, paths.schemas / "state.schema.json")["activeAmendment"]["resultSha"])

        for _ in range(2):
            recovered_state = common.read_json(paths.state, paths.schemas / "state.schema.json")
            recovered_tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
            with patch.object(project_manager, "invoke_role", side_effect=RuntimeError("verifier reached")):
                with self.assertRaisesRegex(RuntimeError, "verifier reached"):
                    project_manager.invoke_pm_resolution(
                        root, config, recovered_state, paths, recovered_tasks, None, "build", "task", "TASK-0001", blocker,
                    )
            self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), normalized_sha)
            self.assertEqual(common.read_json(paths.state, paths.schemas / "state.schema.json")["activeAmendment"]["resultSha"], normalized_sha)

        self.assertEqual(common.read_json(result_path)["result"]["newTasks"][0]["taskId"], "TASK-0018")

    def test_pm_plan_normalization_recovers_exact_unstaged_plan(self) -> None:
        raw_plan = "# Plan\n\nImplement TASK-0018.\n"
        root, config, paths, state, tasks, blocker, worktree, base, result_sha, _ = self.pm_result_ready_fixture(raw_plan)
        normalized = "# Plan\n\nImplement TASK-0003."
        (worktree / "plan.md").write_text(normalized, encoding="utf-8")

        with patch.object(project_manager, "invoke_role", side_effect=RuntimeError("verifier reached")):
            with self.assertRaisesRegex(RuntimeError, "verifier reached"):
                project_manager.invoke_pm_resolution(root, config, state, paths, tasks, None, "build", "task", "TASK-0001", blocker)

        head = self.git(worktree, "rev-parse", "HEAD")
        self.assertEqual(self.git(worktree, "rev-list", "--count", f"{base}..{head}"), "2")
        self.assertEqual(self.git(worktree, "show", f"{head}:plan.md"), normalized.strip())
        self.assertEqual(self.git(worktree, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertNotEqual(head, result_sha)

    def test_pm_plan_normalization_recovers_exact_staged_plan(self) -> None:
        raw_plan = "# Plan\n\nImplement TASK-0018.\n"
        root, config, paths, state, tasks, blocker, worktree, base, result_sha, _ = self.pm_result_ready_fixture(raw_plan)
        normalized = "# Plan\n\nImplement TASK-0003."
        (worktree / "plan.md").write_text(normalized, encoding="utf-8")
        self.git(worktree, "add", "plan.md")

        with patch.object(project_manager, "invoke_role", side_effect=RuntimeError("verifier reached")):
            with self.assertRaisesRegex(RuntimeError, "verifier reached"):
                project_manager.invoke_pm_resolution(root, config, state, paths, tasks, None, "build", "task", "TASK-0001", blocker)

        head = self.git(worktree, "rev-parse", "HEAD")
        self.assertEqual(self.git(worktree, "rev-list", "--count", f"{base}..{head}"), "2")
        self.assertEqual(self.git(worktree, "show", f"{head}:plan.md"), normalized.strip())
        self.assertEqual(self.git(worktree, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertNotEqual(head, result_sha)

    def test_pm_plan_normalization_rejects_mismatched_and_unrelated_dirt(self) -> None:
        raw_plan = "# Plan\n\nImplement TASK-0018.\n"
        root, config, paths, state, tasks, blocker, worktree, _, result_sha, _ = self.pm_result_ready_fixture(raw_plan)
        (worktree / "plan.md").write_text("# Plan\n\nImplement TASK-0004.\n", encoding="utf-8")
        with self.assertRaisesRegex(common.BraceError, "mismatched plan normalization"):
            project_manager.invoke_pm_resolution(root, config, state, paths, tasks, None, "build", "task", "TASK-0001", blocker)
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), result_sha)

        (worktree / "plan.md").write_text(raw_plan, encoding="utf-8")
        (worktree / "unrelated.txt").write_text("unrelated", encoding="utf-8")
        with self.assertRaisesRegex(common.BraceError, "unexpected uncommitted changes"):
            project_manager.invoke_pm_resolution(root, config, state, paths, tasks, None, "build", "task", "TASK-0001", blocker)
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), result_sha)

    def test_pm_plan_normalization_rejects_unknown_references_before_commit(self) -> None:
        root, config, paths, state, tasks, blocker, worktree, base, result_sha, result_path = self.pm_result_ready_fixture(
            "# Plan\n\nImplement TASK-9999.\n"
        )
        with self.assertRaisesRegex(common.BraceError, "Plan references unknown tasks: TASK-9999"):
            project_manager.invoke_pm_resolution(root, config, state, paths, tasks, None, "build", "task", "TASK-0001", blocker)
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), result_sha)
        self.assertEqual(self.git(worktree, "rev-list", "--count", f"{base}..{result_sha}"), "1")
        self.assertIsNone(common.read_json(paths.state, paths.schemas / "state.schema.json")["activeAmendment"]["resultSha"])
        self.assertEqual(common.read_json(result_path)["result"]["newTasks"][0]["taskId"], "TASK-0018")

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

    def test_role_prompt_requires_empirical_ponytail_compliance(self) -> None:
        root, _, _ = self.make_repository()
        (root / ".codex" / "prompts" / "builder.md").write_text("builder role", encoding="utf-8")
        with patch.object(common, "invoke_codex", return_value={}) as invoke:
            common.invoke_role(root, root, "builder", "task", "builder-result.schema.json", "workspace-write")
        prompt = invoke.call_args.args[0]
        self.assertIn("load and use it at full level", prompt)
        self.assertIn("optional and its absence is not a blocker", prompt)
        self.assertIn("required JSON output schema overrides", prompt)
        self.assertIn("Identify the root cause and the component that owns the affected invariant", prompt)
        self.assertIn("Prefer enforcing deterministic behavior in deterministic code", prompt)
        self.assertIn("Recheck the proposed solution against every applicable Ponytail rule", prompt)
        self.assertIn("Ponytail impact: chose [solution] at [owning layer] instead of [rejected alternative] because [reason].", prompt)
        self.assertIn("Reviewers and verifiers must verify Ponytail compliance independently", prompt)
        self.assertNotIn("Coordinator identity guidance", prompt)

    def test_pm_analysis_rejects_unknown_task(self) -> None:
        analysis = {
            "affectedTaskIds": ["TASK-9999"], "affectedBugIds": [], "amendmentRequired": True,
            "options": [{"optionId": "A", "recommended": True, "requiresInput": False, "inputPrompt": None, "action": "amend", "authorizedDocumentationPaths": [], "bugDispositions": []}],
        }
        with self.assertRaisesRegex(common.BraceError, "unknown task"):
            project_manager.assert_pm_analysis(analysis, {"tasks": [self.task()]}, None, "build")

    def test_native_timeout_is_bounded(self) -> None:
        self.assertEqual(common.run_native(sys.executable, ["-c", "raise SystemExit(7)"], allowed_exit_codes=None).returncode, 7)
        pid_path = self.base / "timeout-pids.json"
        script = (
            "import json, os, pathlib, subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)']); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]), encoding='utf-8'); "
            "time.sleep(30)"
        )
        started = time.monotonic()
        with self.assertRaises(common.BraceError) as failure:
            common.run_native(sys.executable, ["-c", script, pid_path], timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 8)
        if os.name == "nt":
            parent_pid, child_pid = json.loads(pid_path.read_text(encoding="utf-8"))
            parent_deadline = time.monotonic() + 1
            while self.windows_process_is_running(parent_pid) and time.monotonic() < parent_deadline:
                time.sleep(0.05)
            self.assertFalse(self.windows_process_is_running(parent_pid))
            if "deadline" in str(failure.exception):
                self.assertFalse(self.windows_process_is_running(child_pid))
            else:
                self.assertRegex(str(failure.exception), "Process-tree termination could not be verified")
                deadline = time.monotonic() + 5
                while self.windows_process_is_running(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(self.windows_process_is_running(child_pid))
        else:
            self.assertIn("deadline", str(failure.exception))

    @unittest.skipUnless(os.name == "nt", "Windows command script behavior")
    def test_native_runs_command_script_from_path_with_spaces(self) -> None:
        directory = self.base / "Program Files"
        directory.mkdir()
        script = directory / "capture argument.cmd"
        script.write_text("@echo off\n@echo(%~1\n", encoding="utf-8")

        self.assertEqual(common.run_native(str(script), ["Endpoint Engineering"]).output, "Endpoint Engineering")

    @unittest.skipUnless(os.name == "nt", "Windows process cleanup behavior")
    def test_windows_tree_cleanup_uses_retained_parent_identity(self) -> None:
        process = Mock(pid=1234)
        process.wait.return_value = 0
        process.poll.side_effect = [None, 0]

        def exit_during_safe_parent_kill() -> None:
            self.assertIsNone(process.poll())
            self.assertEqual(process.poll(), 0)

        process.kill.side_effect = exit_during_safe_parent_kill
        with patch.object(common.subprocess, "run") as taskkill:
            with self.assertRaisesRegex(common.BraceError, "could not be verified.*retained process-tree identity"):
                common._terminate_tree(process, 1)
        taskkill.assert_not_called()
        process.kill.assert_called_once_with()
        self.assertLessEqual(process.wait.call_args.kwargs["timeout"], 1)

        process.reset_mock(side_effect=True)
        process.wait.side_effect = subprocess.TimeoutExpired(["tracked"], 1)
        with patch.object(common.subprocess, "run") as taskkill:
            with self.assertRaisesRegex(common.BraceError, "Tracked process did not stop"):
                common._terminate_tree(process, 1)
        taskkill.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows process-tree behavior")
    def test_native_timeout_checks_tree_after_parent_exits(self) -> None:
        pid_path = self.base / "exited-parent-pids.json"
        child_script = "import time; time.sleep(4)"
        script = (
            "import json, os, pathlib, subprocess, sys; "
            f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]), encoding='utf-8')"
        )
        started = time.monotonic()
        with self.assertRaises(common.BraceError) as failure:
            common.run_native(sys.executable, ["-c", script, pid_path], timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 8)
        parent_pid, child_pid = json.loads(pid_path.read_text(encoding="utf-8"))
        self.assertFalse(self.windows_process_is_running(parent_pid))
        if "deadline" in str(failure.exception):
            self.assertFalse(self.windows_process_is_running(child_pid))
        else:
            self.assertRegex(str(failure.exception), "Process-tree termination could not be verified")
            self.assertTrue(self.windows_process_is_running(child_pid))
            deadline = time.monotonic() + 5
            while self.windows_process_is_running(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(self.windows_process_is_running(child_pid))

    def test_posix_tree_cleanup_uses_spawn_group_after_parent_exits(self) -> None:
        process = Mock(pid=1234)
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch.object(common.os, "name", "posix"), patch.object(
            common.os, "killpg", create=True
        ) as killpg, patch.object(common.signal, "SIGKILL", 9, create=True):
            common._terminate_tree(process, 1)
        killpg.assert_called_once_with(1234, 9)
        process.poll.assert_not_called()
        process.wait.assert_called_once_with(timeout=1)

        process.reset_mock()
        process.wait.return_value = 0
        with patch.object(common.os, "name", "posix"), patch.object(
            common.os, "killpg", side_effect=ProcessLookupError, create=True
        ), patch.object(common.signal, "SIGKILL", 9, create=True):
            common._terminate_tree(process, 1)
        process.kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=1)

        process.reset_mock()
        process.wait.return_value = 0
        with patch.object(common.os, "name", "posix"), patch.object(
            common.os, "killpg", side_effect=PermissionError("denied"), create=True
        ), patch.object(common.signal, "SIGKILL", 9, create=True):
            with self.assertRaisesRegex(common.BraceError, "could not be verified.*group 1234.*denied"):
                common._terminate_tree(process, 1)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1)

    @unittest.skipIf(os.name == "nt", "POSIX process-group behavior")
    def test_native_timeout_kills_posix_group_after_parent_exits(self) -> None:
        sentinel = self.base / "surviving-descendant.txt"
        child_script = (
            "import pathlib, sys, time; time.sleep(2); "
            "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
        )
        script = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {child_script!r}, sys.argv[1]])"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(common.BraceError, "deadline"):
            common.run_native(sys.executable, ["-c", script, sentinel], timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 8)
        time.sleep(3)
        self.assertFalse(sentinel.exists())

    def test_output_schema_projection_supports_bundled_results(self) -> None:
        root, _, config = self.make_repository()
        schemas = common.initialize_state_files(root, config).schemas
        projected = {path.name: common._project_output_schema(path) for path in sorted(schemas.glob("*-result.schema.json"))}
        self.assertEqual(set(projected), {
            "audit-result.schema.json", "builder-result.schema.json", "fixer-result.schema.json",
            "planning-result.schema.json", "pm-amendment-result.schema.json",
            "pm-blocker-result.schema.json", "reviewer-result.schema.json", "verifier-result.schema.json",
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
            "planMarkdown": "plan", "tasks": [{
                "taskId": "TASK-0018", "title": "task", "description": "description", "requirementIds": ["REQ-ONE"],
                "planSections": ["plan"], "dependencies": [], "allowedPaths": ["src/**"], "exclusiveResources": [],
                "acceptanceCriteria": ["works"], "checks": ["check"],
            }],
            "summary": {"requirementsCount": 1, "taskCount": 1, "parallelizableTaskCount": 1,
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
            returned = common.invoke_codex("prompt", root, schema_path, "read-only", paths.logs)
            self.assertEqual(returned, result)
            common.canonicalize_graph_identities(returned["tasks"], "task")
            raw_result = common.read_json(next(paths.logs.glob("agent-*.result.json")))
            self.assertEqual(raw_result["tasks"][0]["taskId"], "TASK-0018")
            self.assertEqual(returned["tasks"][0]["taskId"], "TASK-0001")
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

    def test_review_records_are_distinct_resumable_and_candidate_bound(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base = "a" * 40
        candidate = "b" * 40
        approved = self.reviewer_result()

        first = common.write_review_result(paths, "TASK-0001", 2, 1, base, candidate, approved)
        second = common.write_review_result(paths, "TASK-0001", 2, 2, base, candidate, approved)

        self.assertEqual(common.review_path(paths, "TASK-0001", 2, 1).name, "TASK-0001-attempt-002-review-01.json")
        self.assertNotEqual(common.review_path(paths, "TASK-0001", 2, 1), common.review_path(paths, "TASK-0001", 2, 2))
        self.assertNotEqual(common.review_path(paths, "TASK-0001", 2, 1), common.attempt_path(paths, "result", "TASK-0001", 2))
        self.assertEqual(common.read_review_result(paths, "TASK-0001", 2, 1, base, candidate), first)
        self.assertEqual(common.read_review_result(paths, "TASK-0001", 2, 2, base, candidate), second)
        self.assertIsNone(common.read_review_result(paths, "TASK-0001", 2, 1, base, "c" * 40))
        self.assertEqual(len(list(paths.results.glob("TASK-0001-attempt-002-review-*.json"))), 2)

        for completed_at, error in (("not-a-date-time", "date-time"), ("2026-09-09T00:00:00+01:60", "date-time"), (123, "string")):
            malformed = dict(first, completedAt=completed_at)
            common.write_text_atomic(common.review_path(paths, "TASK-0001", 2, 1), common.pretty_json(malformed))
            with self.subTest(completed_at=completed_at), self.assertRaisesRegex(common.BraceError, error):
                common.read_review_result(paths, "TASK-0001", 2, 1, base, candidate)

    def test_dual_reviews_start_independently_with_identical_exact_candidate_context(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base = self.git(root, "rev-parse", "HEAD")
        (root / "src").mkdir()
        (root / "src" / "product.py").write_text("value = 1\n", encoding="utf-8")
        self.git(root, "add", "src/product.py")
        self.git(root, "commit", "-m", "candidate")
        candidate = self.git(root, "rev-parse", "HEAD")
        task = self.task() | {"attemptCount": 1, "baseSha": base, "resultSha": candidate}
        barrier = threading.Barrier(2)
        contexts: list[str] = []

        def fake_review(repository, state_paths, identity, attempt, reviewer, base_sha, candidate_sha, context, required_checks):
            self.assertEqual(required_checks, task["checks"])
            contexts.append(context)
            barrier.wait(timeout=2)
            return {
                "identity": identity, "attempt": attempt, "reviewer": reviewer,
                "baseSha": base_sha, "candidateSha": candidate_sha,
                "result": self.reviewer_result(),
            }

        with (
            patch.object(common, "new_review_worktree") as create,
            patch.object(common, "remove_review_worktree") as remove,
            patch.object(common, "run_review", side_effect=fake_review),
        ):
            records = common.run_reviews(root, paths, task, "task")

        self.assertEqual([record["reviewer"] for record in records], [1, 2])
        self.assertEqual(contexts[0], contexts[1])
        context = json.loads(contexts[0])
        self.assertEqual((context["baseSha"], context["candidateSha"]), (base, candidate))
        self.assertEqual(context["assignment"]["taskId"], "TASK-0001")
        self.assertIn("REQ-ONE", context["requirementsMarkdown"])
        self.assertIn("src/product.py", context["baseToCandidateDiff"])
        self.assertNotIn("lastError", context["assignment"])
        self.assertEqual(create.call_count, 2)
        self.assertEqual(remove.call_count, 2)

        malformed = self.reviewer_result() | {"findings": ["not structured"]}
        with self.assertRaisesRegex(common.BraceError, "reviewer-result.schema.json"):
            common.validate_json(malformed, paths.schemas / "reviewer-result.schema.json")
        incomplete = self.reviewer_result() | {"checks": [{"command": "docs check", "result": "skipped", "evidence": "tool unavailable"}]}
        with self.assertRaisesRegex(common.BraceError, "should not be valid"):
            common.validate_json(incomplete, paths.schemas / "reviewer-result.schema.json")

    def test_approved_reviews_require_each_task_and_bug_check(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base, candidate = "a" * 40, "b" * 40
        for identity in ("TASK-0001", "BUG-0001"):
            for reviewer in (1, 2):
                common.write_review_result(paths, identity, 1, reviewer, base, candidate, self.reviewer_result())
        task = self.task() | {"attemptCount": 1, "baseSha": base, "resultSha": candidate}
        bug = {"bugId": "BUG-0001", "attemptCount": 1, "baseSha": base, "resultSha": candidate, "acceptanceTest": "python -m unittest bug"}
        with self.assertRaisesRegex(common.BraceError, "missing passed evidence.*python -m unittest"):
            common.require_approved_reviews(paths, task, "task")
        with self.assertRaisesRegex(common.BraceError, "missing passed evidence.*python -m unittest bug"):
            common.require_approved_reviews(paths, bug, "bug")

        actual = self.git(root, "rev-parse", "HEAD")
        with patch.object(common, "invoke_role", return_value=self.reviewer_result()):
            with self.assertRaisesRegex(common.BraceError, "missing passed evidence.*required check"):
                common.run_review(root, paths, "TASK-0002", 1, 1, actual, actual, "review", ["required check"])
        self.assertFalse(common.review_path(paths, "TASK-0002", 1, 1).exists())

    def test_state_initialization_restores_reviewer_support_for_existing_project(self) -> None:
        root, _, config = self.make_repository()
        paths = common.Paths(root)
        (paths.prompts / "reviewer.md").unlink(missing_ok=True)
        (paths.schemas / "reviewer-result.schema.json").unlink()
        review_schema = common.read_json(paths.schemas / "review-record.schema.json")
        review_schema["properties"]["result"]["$ref"] = "verifier-result.schema.json"
        common.write_text_atomic(paths.schemas / "review-record.schema.json", common.pretty_json(review_schema))

        common.initialize_state_files(root, config)

        self.assertTrue((paths.prompts / "reviewer.md").is_file())
        self.assertTrue((paths.schemas / "reviewer-result.schema.json").is_file())
        self.assertEqual(common.read_json(paths.schemas / "review-record.schema.json")["properties"]["result"]["$ref"], "reviewer-result.schema.json")

    def test_review_worktree_recovery_mutation_and_cleanup(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base = self.git(root, "rev-parse", "HEAD")
        candidate_file = root / "candidate.txt"
        candidate_file.write_text("candidate\n", encoding="utf-8")
        (root / ".gitignore").write_text(".ignored/\n", encoding="utf-8")
        self.git(root, "add", "candidate.txt", ".gitignore")
        self.git(root, "commit", "-m", "candidate")
        candidate = self.git(root, "rev-parse", "HEAD")
        approved = self.reviewer_result()

        worktree = common.new_review_worktree(root, config, "BUG-0001", 1, 1, candidate)
        self.assertTrue(worktree.is_absolute())
        self.assertEqual(self.git(worktree, "branch", "--show-current"), "")
        self.assertEqual(common.new_review_worktree(root, config, "BUG-0001", 1, 1, candidate), worktree)
        with self.assertRaisesRegex(common.BraceError, "matching durable result"):
            common.remove_review_worktree(root, config, paths, "BUG-0001", 1, 1, base, candidate)

        (worktree / "untracked.txt").write_text("mutation\n", encoding="utf-8")
        with self.assertRaisesRegex(common.BraceError, "contains changes"):
            common.assert_review_worktree(worktree, candidate)
        (worktree / "untracked.txt").unlink()
        (worktree / ".ignored").mkdir()
        (worktree / ".ignored" / "mutation.txt").write_text("ignored\n", encoding="utf-8")
        with self.assertRaisesRegex(common.BraceError, "contains changes"):
            common.assert_review_worktree(worktree, candidate)
        (worktree / ".ignored" / "mutation.txt").unlink()
        (worktree / ".ignored").rmdir()
        (worktree / ".ignored").mkdir()
        with self.assertRaisesRegex(common.BraceError, "contains changes"):
            common.assert_review_worktree(worktree, candidate)
        (worktree / ".ignored").rmdir()
        common.write_review_result(paths, "BUG-0001", 1, 1, base, candidate, approved)
        common.remove_review_worktree(root, config, paths, "BUG-0001", 1, 1, base, candidate)
        self.assertFalse(worktree.exists())

        def mutate_review(_: Path, review_worktree: Path, role: str, context: str, schema: str, sandbox: str) -> dict:
            self.assertEqual((role, schema, sandbox), ("reviewer", "reviewer-result.schema.json", "read-only"))
            (Path(review_worktree) / "mutation.txt").write_text("changed\n", encoding="utf-8")
            return approved

        with patch.object(common, "invoke_role", side_effect=mutate_review) as invoke:
            self.assertEqual(common.run_review(root, paths, "BUG-0001", 1, 1, base, candidate, "review"), common.read_review_result(paths, "BUG-0001", 1, 1, base, candidate))
            invoke.assert_not_called()
            with self.assertRaisesRegex(common.BraceError, "contains changes"):
                common.run_review(root, paths, "BUG-0001", 1, 2, base, candidate, "review")

        second_worktree = common.review_worktree_path(root, config, "BUG-0001", 1, 2)
        self.assertFalse(common.review_path(paths, "BUG-0001", 1, 2).exists())
        (second_worktree / "mutation.txt").unlink()
        with patch.object(common, "invoke_role", return_value=approved) as invoke:
            common.run_review(root, paths, "BUG-0001", 1, 2, base, candidate, "review")
            invoke.assert_called_once()
        common.remove_review_worktree(root, config, paths, "BUG-0001", 1, 2, base, candidate)
        self.assertFalse(second_worktree.exists())

    def test_review_worktree_recovers_and_cleans_stale_registration(self) -> None:
        root, _, config = self.make_repository()
        paths = common.initialize_state_files(root, config)
        base = candidate = self.git(root, "rev-parse", "HEAD")
        approved = self.reviewer_result()

        interrupted = common.new_review_worktree(root, config, "TASK-0001", 1, 1, candidate)
        shutil.rmtree(interrupted)
        self.assertTrue(common._worktree_registered(root, interrupted))
        self.assertEqual(common.new_review_worktree(root, config, "TASK-0001", 1, 1, candidate), interrupted)
        common.write_review_result(paths, "TASK-0001", 1, 1, base, candidate, approved)
        common.remove_review_worktree(root, config, paths, "TASK-0001", 1, 1, base, candidate)

        completed = common.new_review_worktree(root, config, "TASK-0001", 1, 2, candidate)
        common.write_review_result(paths, "TASK-0001", 1, 2, base, candidate, approved)
        shutil.rmtree(completed)
        common.remove_review_worktree(root, config, paths, "TASK-0001", 1, 2, base, candidate)
        self.assertFalse(common._worktree_registered(root, completed))

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
