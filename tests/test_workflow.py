from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

from brace import audit as audit_loop
from brace import build as build_loop
from brace import common
from brace import planning as planning_loop
from brace.bootstrap import bundled_template
from support import RepositoryTestCase


class WorkflowTests(RepositoryTestCase):
    def prepare(self) -> tuple[Path, Path, dict]:
        root, remote, config = self.make_repository()
        source_template = bundled_template()
        shutil.copy2(source_template / ".gitignore", root / ".gitignore")
        shutil.copytree(source_template / ".codex" / "prompts", root / ".codex" / "prompts", dirs_exist_ok=True)
        shutil.copy2(source_template / ".codex" / "AGENTS.md", root / ".codex" / "AGENTS.md")
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "workflow files")
        self.git(root, "push", "origin", "main")
        return root, remote, config

    def planner_result(self) -> dict:
        return {
            "status": "complete", "questions": [],
            "normalizedRequirementsMarkdown": "# Requirements\n\nREQ-ONE\n",
            "planMarkdown": "# Plan\n\nImplement REQ-ONE.\n",
            "tasks": [{
                "taskId": "TASK-0001", "title": "Implement feature", "description": "Create the feature in one session.",
                "requirementIds": ["REQ-ONE"], "planSections": ["Plan"], "dependencies": [],
                "allowedPaths": ["src/**"], "exclusiveResources": [], "acceptanceCriteria": ["Feature exists"],
                "checks": ["test -f src/product.txt"],
            }],
            "summary": {"requirementsCount": 1, "taskCount": 1, "parallelizableTaskCount": 1, "assumptions": [], "deferredScope": [], "deferredRequirementIds": []},
        }

    def plan(self, root: Path) -> None:
        with patch.object(planning_loop, "assert_prerequisites"), patch.object(planning_loop, "invoke_role", return_value=self.planner_result()):
            planning_loop.run(root)

    def test_planning_build_and_audit_complete(self) -> None:
        root, _, config = self.prepare()
        self.plan(root)
        paths = common.Paths(root)
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        self.assertEqual(tasks["status"], "ready")
        self.assertEqual(state["stage"], "build")

        def fake_assignment(repository, worktree, item, kind, paths):
            target = Path(worktree) / "src"
            target.mkdir(exist_ok=True)
            (target / "product.txt").write_text("implemented\n", encoding="utf-8")
            self.git(Path(worktree), "add", "src/product.txt")
            self.git(Path(worktree), "commit", "-m", item["taskId"])
            head = self.git(Path(worktree), "rev-parse", "HEAD")
            result = {"status": "completed", "summary": "implemented", "commitSha": head, "filesChanged": ["src/product.txt"], "checks": [], "blocker": None}
            record = {"schemaVersion": "1.0", "identity": item["taskId"], "attempt": item["attemptCount"], "succeeded": True, "result": result, "error": None, "completedAt": common.utc_now()}
            common.write_immutable_json(common.attempt_path(paths, "result", item["taskId"], item["attemptCount"]), record)
            return record

        verifier = {"approved": True, "summary": "approved", "findings": [], "checks": [], "blocker": None}

        def fake_publish(repository, worktree, configuration, item, kind):
            base = self.git(root, "rev-parse", f"origin/{configuration['integrationBranch']}")
            self.git(root, "push", "origin", f"{item['resultSha']}:refs/heads/{configuration['integrationBranch']}")
            return {
                "id": "1", "url": "https://example.invalid/1", "state": "merged", "repository": "owner/repo",
                "head": item["branch"], "headSha": item["resultSha"], "base": configuration["integrationBranch"],
                "baseSha": base, "mergeSha": item["resultSha"],
            }

        with (
            patch.object(build_loop, "assert_prerequisites"),
            patch.object(build_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(build_loop, "invoke_role", return_value=verifier),
            patch.object(build_loop, "publish_assignment", side_effect=fake_publish),
        ):
            self.assertEqual(build_loop.run(root), "audit")

        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        self.assertEqual(tasks["status"], "complete")
        self.assertEqual(tasks["tasks"][0]["status"], "integrated")
        self.assertEqual(state["stage"], "audit")

        audit_result = {"status": "completed", "summary": "no bugs", "bugs": [], "checks": [], "missingEvidence": [], "blocker": None}

        def fake_audit_role(repository, worktree, role, context, schema, sandbox):
            return audit_result if role == "auditor" else verifier

        def fake_new_pr(repository, configuration, head, base, expected_head, expected_base, title, body):
            return {
                "id": "2", "url": "https://example.invalid/2", "state": "open", "repository": "owner/repo",
                "head": head, "headSha": expected_head, "base": base, "baseSha": expected_base, "mergeSha": None,
            }

        def fake_complete(repository, configuration, pull_request):
            self.git(root, "push", "origin", f"{pull_request['headSha']}:refs/heads/{configuration['targetBranch']}")
            return {**pull_request, "state": "merged", "mergeSha": pull_request["headSha"]}

        with (
            patch.object(audit_loop, "assert_prerequisites"),
            patch.object(audit_loop, "invoke_role", side_effect=fake_audit_role),
            patch.object(audit_loop, "new_pull_request", side_effect=fake_new_pr),
            patch.object(audit_loop, "complete_pull_request", side_effect=fake_complete),
        ):
            self.assertEqual(audit_loop.run(root), "complete")

        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        self.assertEqual(state["stage"], "complete")
        self.assertEqual(bugs["status"], "complete")
        self.assertTrue(paths.build_summary.is_file())
        self.assertTrue(paths.audit_summary.is_file())
        self.assertFalse(Path(config["worktreeRoot"]).exists())

    def test_rejected_task_and_bug_are_retried_from_their_base(self) -> None:
        root, _, config = self.prepare()
        self.plan(root)
        paths = common.Paths(root)
        approved = {"approved": True, "summary": "approved", "findings": [], "checks": [], "blocker": None}
        rejected = {"approved": False, "summary": "changes required", "findings": ["expected corrected output"], "checks": [], "blocker": None}
        rejected_shas = {}

        def fake_assignment(repository, worktree, item, kind, state_paths):
            path = Path(worktree)
            self.assertEqual(self.git(path, "rev-parse", "HEAD"), item["baseSha"])
            if item["attemptCount"] == 2:
                self.assertIn("expected corrected output", item["lastError"])
            target = path / "src" / "product.txt"
            target.parent.mkdir(exist_ok=True)
            target.write_text(f"{kind} attempt {item['attemptCount']}\n", encoding="utf-8")
            self.git(path, "add", "src/product.txt")
            self.git(path, "commit", "-m", f"{kind} attempt {item['attemptCount']}")
            head = self.git(path, "rev-parse", "HEAD")
            identity = item["taskId" if kind == "task" else "bugId"]
            if item["attemptCount"] == 1:
                rejected_shas[kind] = head
            result = {
                "status": "completed" if kind == "task" else "fixed", "summary": "implemented", "commitSha": head,
                "checks": [], "blocker": None,
            }
            result["filesChanged" if kind == "task" else "changedFiles"] = ["src/product.txt"]
            record = {
                "schemaVersion": "1.0", "identity": identity, "attempt": item["attemptCount"], "succeeded": True,
                "result": result, "error": None, "completedAt": common.utc_now(),
            }
            common.write_immutable_json(common.attempt_path(state_paths, "result", identity, item["attemptCount"]), record)
            return record

        def fake_publish(repository, worktree, configuration, item, kind):
            base = self.git(root, "rev-parse", f"origin/{configuration['integrationBranch']}")
            self.git(root, "push", "origin", f"{item['resultSha']}:refs/heads/{configuration['integrationBranch']}")
            identity = item.get("taskId") or item["bugId"]
            return {
                "id": identity, "url": f"https://example.invalid/{identity}", "state": "merged", "repository": "owner/repo",
                "head": item["branch"], "headSha": item["resultSha"], "base": configuration["integrationBranch"],
                "baseSha": base, "mergeSha": item["resultSha"],
            }

        task_verifications = 0

        def fake_build_role(repository, worktree, role, context, schema, sandbox):
            nonlocal task_verifications
            if context.startswith("Verify only this task"):
                task_verifications += 1
                return rejected if task_verifications == 1 else approved
            return approved

        with (
            patch.object(build_loop, "assert_prerequisites"),
            patch.object(build_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(build_loop, "invoke_role", side_effect=fake_build_role),
            patch.object(build_loop, "publish_assignment", side_effect=fake_publish) as task_publish,
        ):
            self.assertEqual(build_loop.run(root), "audit")

        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        task = tasks["tasks"][0]
        self.assertEqual(task["attemptCount"], 2)
        self.assertEqual(task_publish.call_count, 1)
        self.assertEqual(self.git(root, "merge-base", rejected_shas["task"], task["resultSha"]), task["baseSha"])

        audit_result = {
            "status": "completed", "summary": "one bug", "checks": [], "missingEvidence": [], "blocker": None,
            "bugs": [{
                "bugId": "BUG-0001", "title": "Correct output", "severity": "medium", "category": "correctness",
                "requirementIds": ["REQ-ONE"], "description": "Output is wrong", "evidence": "Focused check failed",
                "actualBehavior": "wrong", "requiredBehavior": "correct", "impact": "incorrect result",
                "requiredCorrection": "correct the output", "acceptanceTest": "output is correct", "dependencies": [],
                "allowedPaths": ["src/**"], "exclusiveResources": [],
            }],
        }
        bug_verifications = 0

        def fake_audit_role(repository, worktree, role, context, schema, sandbox):
            nonlocal bug_verifications
            if role == "auditor":
                return audit_result
            if context.startswith("Verify only this bug correction"):
                bug_verifications += 1
                return rejected if bug_verifications == 1 else approved
            return approved

        def fake_new_pr(repository, configuration, head, base, expected_head, expected_base, title, body):
            return {
                "id": "project", "url": "https://example.invalid/project", "state": "open", "repository": "owner/repo",
                "head": head, "headSha": expected_head, "base": base, "baseSha": expected_base, "mergeSha": None,
            }

        def fake_complete(repository, configuration, pull_request):
            self.git(root, "push", "origin", f"{pull_request['headSha']}:refs/heads/{configuration['targetBranch']}")
            return {**pull_request, "state": "merged", "mergeSha": pull_request["headSha"]}

        with (
            patch.object(audit_loop, "assert_prerequisites"),
            patch.object(audit_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(audit_loop, "invoke_role", side_effect=fake_audit_role),
            patch.object(audit_loop, "publish_assignment", side_effect=fake_publish) as bug_publish,
            patch.object(audit_loop, "new_pull_request", side_effect=fake_new_pr),
            patch.object(audit_loop, "complete_pull_request", side_effect=fake_complete),
        ):
            self.assertEqual(audit_loop.run(root), "complete")

        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        bug = bugs["bugs"][0]
        self.assertEqual(bug["attemptCount"], 2)
        self.assertEqual(bug_publish.call_count, 1)
        self.assertEqual(self.git(root, "merge-base", rejected_shas["bug"], bug["resultSha"]), bug["baseSha"])
