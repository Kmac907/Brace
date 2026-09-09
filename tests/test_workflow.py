from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from brace import audit as audit_loop
from brace import build as build_loop
from brace import common
from brace import planning as planning_loop
from brace.bootstrap import bundled_template
from support import RepositoryTestCase


class WorkflowTests(RepositoryTestCase):
    @staticmethod
    def reviewer_result(approved: bool = True) -> dict:
        finding = {
            "severity": "medium", "location": "src/product.txt:1", "expectedBehavior": "correct output",
            "actualBehavior": "expected corrected output", "evidence": "focused check failed",
            "impact": "users receive incorrect output", "classification": "Observed",
        }
        return {
            "approved": approved,
            "summary": "No reproducible defect found in the reviewed scope." if approved else "changes required",
            "findings": [] if approved else [finding], "checks": [], "blocker": None,
            "ponytailVerdict": f"Ponytail verdict: {'supported' if approved else 'rejected'} — inspected diff evidence.",
        }

    def persist_reviews(self, paths: common.Paths, item: dict, kind: str, results: tuple[dict, dict] | None = None) -> list[dict]:
        identity = item["taskId" if kind == "task" else "bugId"]
        chosen = results or (self.reviewer_result(), self.reviewer_result())
        required = item["checks"] if kind == "task" else [item["acceptanceTest"]]
        chosen = tuple(result | {"checks": [{"command": command, "result": "passed", "evidence": "focused check passed"} for command in required]} for result in chosen)
        return [
            common.read_review_result(paths, identity, item["attemptCount"], reviewer, item["baseSha"], item["resultSha"])
            or common.write_review_result(
                paths, identity, item["attemptCount"], reviewer, item["baseSha"], item["resultSha"], result, required
            )
            for reviewer, result in enumerate(chosen, 1)
        ]

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

    def prepare_legacy_project(self) -> tuple[Path, Path, dict]:
        root, remote, config = self.prepare()
        self.git(
            root,
            "rm",
            ".codex/prompts/reviewer.md",
            ".codex/schemas/reviewer-result.schema.json",
            ".codex/schemas/review-record.schema.json",
        )
        self.git(root, "commit", "-m", "simulate v0.2.8 support")
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

    def test_legacy_support_restoration_does_not_block_planning(self) -> None:
        root, _, _ = self.prepare_legacy_project()

        self.plan(root)

        pending = self.git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
        self.assertEqual(
            {line[3:].replace("\\", "/") for line in pending},
            {
                ".codex/prompts/reviewer.md",
                ".codex/schemas/reviewer-result.schema.json",
                ".codex/schemas/review-record.schema.json",
            },
        )

    def test_legacy_support_restoration_does_not_block_completed_reset(self) -> None:
        root, _, config = self.prepare_legacy_project()
        paths = common.initialize_state_files(root, config)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        state.update(
            stage="complete",
            stageStatus="complete",
            finalMergeSha=self.git(root, "rev-parse", "HEAD"),
        )
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")

        common.reset_completed_workflow(root, config, state)

        self.assertFalse(paths.state.exists())

    def test_modified_legacy_support_is_rejected_during_planning(self) -> None:
        root, _, config = self.prepare_legacy_project()
        paths = common.initialize_state_files(root, config)
        (paths.prompts / "reviewer.md").write_text("untrusted prompt\n", encoding="utf-8")

        with (
            patch.object(planning_loop, "assert_prerequisites"),
            patch.object(planning_loop, "invoke_role", return_value=self.planner_result()),
            self.assertRaisesRegex(common.BraceError, "unrelated uncommitted work.*reviewer.md"),
        ):
            planning_loop.run(root)

    def test_modified_legacy_support_is_rejected_during_completed_reset(self) -> None:
        root, _, config = self.prepare_legacy_project()
        paths = common.initialize_state_files(root, config)
        (paths.prompts / "reviewer.md").write_text("untrusted prompt\n", encoding="utf-8")
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        state.update(
            stage="complete",
            stageStatus="complete",
            finalMergeSha=self.git(root, "rev-parse", "HEAD"),
        )
        common.write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")

        with self.assertRaisesRegex(common.BraceError, "must be clean"):
            common.reset_completed_workflow(root, config, state)

    def test_planning_canonicalizes_model_identities_without_retry(self) -> None:
        root, _, _ = self.prepare()
        result = self.planner_result()
        result["tasks"][0]["taskId"] = "TASK-0018"
        result["tasks"].append({
            **result["tasks"][0], "taskId": "TASK-0028", "title": "Finish feature",
            "dependencies": ["TASK-0018"],
        })
        result["planMarkdown"] = "# Plan\n\nImplement TASK-001, then TASK-0028. Preserve X-TASK-0018-extra.\n"
        result["summary"].update(taskCount=2, parallelizableTaskCount=1)
        with patch.object(planning_loop, "assert_prerequisites"), patch.object(planning_loop, "invoke_role", return_value=result) as invoke:
            planning_loop.run(root)
        paths = common.Paths(root)
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")["tasks"]
        self.assertEqual([task["taskId"] for task in tasks], ["TASK-0001", "TASK-0002"])
        self.assertEqual(tasks[1]["dependencies"], ["TASK-0001"])
        self.assertEqual((root / "plan.md").read_text(encoding="utf-8"), "# Plan\n\nImplement TASK-0001, then TASK-0002. Preserve X-TASK-0018-extra.")
        self.assertEqual(invoke.call_count, 1)

    def test_planning_validation_failures_do_not_retry_or_persist_tasks(self) -> None:
        root, _, _ = self.prepare()
        result = self.planner_result()
        result["tasks"][0].update(taskId="TASK-0018", dependencies=["TASK-0001"])
        with (
            patch.object(planning_loop, "assert_prerequisites"),
            patch.object(planning_loop, "invoke_role", return_value=result) as invoke,
            self.assertRaisesRegex(common.BraceError, "Unresolved task dependency collides with canonical identity: TASK-0001"),
        ):
            planning_loop.run(root)
        paths = common.Paths(root)
        self.assertEqual(common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")["tasks"], [])
        self.assertEqual(invoke.call_count, 1)

    def test_planning_duplicate_ids_fail_before_persistence(self) -> None:
        root, _, _ = self.prepare()
        result = self.planner_result()
        result["tasks"][0]["taskId"] = "TASK-0018"
        result["tasks"].append(dict(result["tasks"][0]))
        with (
            patch.object(planning_loop, "assert_prerequisites"),
            patch.object(planning_loop, "invoke_role", return_value=result),
            self.assertRaisesRegex(common.BraceError, "Duplicate provisional task identity"),
        ):
            planning_loop.run(root)
        paths = common.Paths(root)
        self.assertEqual(common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")["tasks"], [])

    def test_planning_unknown_plan_reference_fails_before_persistence(self) -> None:
        root, _, _ = self.prepare()
        result = self.planner_result()
        result["tasks"][0]["taskId"] = "TASK-0018"
        result["planMarkdown"] = "Implement TASK-9999."
        with (
            patch.object(planning_loop, "assert_prerequisites"),
            patch.object(planning_loop, "invoke_role", return_value=result),
            self.assertRaisesRegex(common.BraceError, "Plan references unknown tasks"),
        ):
            planning_loop.run(root)
        paths = common.Paths(root)
        self.assertEqual(common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")["tasks"], [])

    def test_planning_still_retries_for_clarification(self) -> None:
        root, _, _ = self.prepare()
        question = {
            "status": "questions", "questions": [{"questionId": "QUESTION-0001", "question": "Which behavior?", "reason": "Required"}],
            "normalizedRequirementsMarkdown": None, "planMarkdown": None, "tasks": [],
            "summary": {"requirementsCount": 0, "taskCount": 0, "parallelizableTaskCount": 0, "assumptions": [], "deferredScope": [], "deferredRequirementIds": []},
        }
        calls = 0

        def answer_then_complete(*_: object) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                return question
            self.assertIn("Use the existing behavior.", (root / "requirements.md").read_text(encoding="utf-8"))
            return self.planner_result()

        with (
            patch.object(planning_loop, "assert_prerequisites"),
            patch.object(planning_loop, "invoke_role", side_effect=answer_then_complete) as invoke,
            patch.object(planning_loop, "ask", return_value="Use the existing behavior."),
        ):
            planning_loop.run(root)
        self.assertEqual(invoke.call_count, 2)

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
            patch.object(build_loop, "run_reviews", side_effect=lambda repository, state_paths, item, kind: self.persist_reviews(state_paths, item, kind)),
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

    def test_build_status_covers_success_and_exceptions(self) -> None:
        root, _, _ = self.prepare()
        self.plan(root)
        active: list[str] = []
        events: list[tuple[str, str]] = []
        assignment_calls = 0
        verification_calls = 0

        @contextmanager
        def recording_status(message: str):
            active.append(message)
            events.append(("enter", message))
            try:
                yield
            finally:
                self.assertEqual(active.pop(), message)
                events.append(("exit", message))

        def fake_assignment(repository, worktree, item, kind, paths):
            nonlocal assignment_calls
            assignment_calls += 1
            self.assertEqual(active, ["Building TASK-0001"])
            if assignment_calls == 1:
                raise RuntimeError("builder failed")
            target = Path(worktree) / "src" / "product.txt"
            target.parent.mkdir(exist_ok=True)
            target.write_text(f"attempt {item['attemptCount']}\n", encoding="utf-8")
            self.git(Path(worktree), "add", "src/product.txt")
            self.git(Path(worktree), "commit", "-m", f"attempt {item['attemptCount']}")
            head = self.git(Path(worktree), "rev-parse", "HEAD")
            result = {"status": "completed", "summary": "implemented", "commitSha": head, "filesChanged": ["src/product.txt"], "checks": [], "blocker": None}
            record = {"schemaVersion": "1.0", "identity": item["taskId"], "attempt": item["attemptCount"], "succeeded": True, "result": result, "error": None, "completedAt": common.utc_now()}
            common.write_immutable_json(common.attempt_path(paths, "result", item["taskId"], item["attemptCount"]), record)
            return record

        verifier = {"approved": True, "summary": "approved", "findings": [], "checks": [], "blocker": None}

        def fake_reviews(repository, paths, item, kind):
            nonlocal verification_calls
            verification_calls += 1
            self.assertEqual(active, ["Reviewing TASK-0001 (attempt 2)"])
            if verification_calls == 1:
                first = self.reviewer_result() | {"checks": [{"command": item["checks"][0], "result": "passed", "evidence": "focused check passed"}]}
                common.write_review_result(
                    paths,
                    item["taskId"],
                    item["attemptCount"],
                    1,
                    item["baseSha"],
                    item["resultSha"],
                    first,
                    item["checks"],
                )
                raise RuntimeError("second reviewer interrupted")
            return self.persist_reviews(paths, item, kind)

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
            patch.object(build_loop, "status", side_effect=recording_status),
            patch.object(build_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(build_loop, "run_reviews", side_effect=fake_reviews),
            patch.object(build_loop, "invoke_role", return_value=verifier),
            patch.object(build_loop, "publish_assignment", side_effect=fake_publish),
        ):
            with self.assertRaisesRegex(RuntimeError, "builder failed"):
                build_loop.run(root)
            self.assertEqual(active, [])
            with self.assertRaisesRegex(RuntimeError, "second reviewer interrupted"):
                build_loop.run(root)
            self.assertEqual(active, [])
            legacy_tasks = common.read_json(common.Paths(root).tasks, common.Paths(root).schemas / "tasks.schema.json")
            legacy_tasks["tasks"][0]["status"] = "verified_ready"
            build_loop.save_ledger(legacy_tasks, common.Paths(root))
            self.assertEqual(build_loop.run(root), "audit")

        self.assertEqual(active, [])
        self.assertEqual((assignment_calls, verification_calls), (2, 2))
        self.assertEqual(len(list(common.Paths(root).results.glob("TASK-0001-attempt-002-review-*.json"))), 2)
        self.assertEqual(events, [
            ("enter", "Building TASK-0001"),
            ("exit", "Building TASK-0001"),
            ("enter", "Building TASK-0001"),
            ("exit", "Building TASK-0001"),
            ("enter", "Reviewing TASK-0001 (attempt 2)"),
            ("exit", "Reviewing TASK-0001 (attempt 2)"),
            ("enter", "Reviewing TASK-0001 (attempt 2)"),
            ("exit", "Reviewing TASK-0001 (attempt 2)"),
            ("enter", "Running integration verification"),
            ("exit", "Running integration verification"),
        ])

    def test_rejected_task_and_bug_are_retried_from_their_base(self) -> None:
        root, _, config = self.prepare()
        self.plan(root)
        paths = common.Paths(root)
        approved = self.reviewer_result()
        rejected = self.reviewer_result(False)
        verifier = {"approved": True, "summary": "approved", "findings": [], "checks": [], "blocker": None}
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

        def fake_task_reviews(repository, state_paths, item, kind):
            nonlocal task_verifications
            task_verifications += 1
            results = (rejected, approved) if task_verifications == 1 else (approved, approved)
            return self.persist_reviews(state_paths, item, kind, results)

        with (
            patch.object(build_loop, "assert_prerequisites"),
            patch.object(build_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(build_loop, "run_reviews", side_effect=fake_task_reviews),
            patch.object(build_loop, "invoke_role", return_value=verifier),
            patch.object(build_loop, "publish_assignment", side_effect=fake_publish) as task_publish,
        ):
            self.assertEqual(build_loop.run(root), "audit")

        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        task = tasks["tasks"][0]
        self.assertEqual(task["attemptCount"], 2)
        self.assertEqual(task_verifications, 2)
        self.assertEqual(len(list(paths.results.glob("TASK-0001-attempt-*-review-*.json"))), 4)
        self.assertEqual(task_publish.call_count, 1)
        self.assertEqual(self.git(root, "merge-base", rejected_shas["task"], task["resultSha"]), task["baseSha"])

        audit_result = {
            "status": "completed", "summary": "one bug", "checks": [], "missingEvidence": [], "blocker": None,
            "bugs": [{
                "bugId": "BUG-0018", "title": "Correct output", "severity": "medium", "category": "correctness",
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
            return verifier

        def fake_bug_reviews(repository, state_paths, item, kind):
            nonlocal bug_verifications
            bug_verifications += 1
            if bug_verifications == 1:
                raise RuntimeError("legacy bug review interrupted")
            results = (approved, rejected) if bug_verifications == 2 else (approved, approved)
            return self.persist_reviews(state_paths, item, kind, results)

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
            patch.object(audit_loop, "run_reviews", side_effect=fake_bug_reviews),
            patch.object(audit_loop, "invoke_role", side_effect=fake_audit_role),
            patch.object(audit_loop, "publish_assignment", side_effect=fake_publish) as bug_publish,
            patch.object(audit_loop, "new_pull_request", side_effect=fake_new_pr),
            patch.object(audit_loop, "complete_pull_request", side_effect=fake_complete),
        ):
            with self.assertRaisesRegex(RuntimeError, "legacy bug review interrupted"):
                audit_loop.run(root)
            legacy_bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
            legacy_bugs["bugs"][0].update(status="ready_to_publish", disposition="fixed")
            audit_loop.save_ledger(legacy_bugs, paths)
            self.assertEqual(audit_loop.run(root), "complete")

        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        bug = bugs["bugs"][0]
        self.assertEqual(bug["attemptCount"], 2)
        self.assertEqual(bug_verifications, 3)
        self.assertEqual(len(list(paths.results.glob("BUG-0001-attempt-*-review-*.json"))), 4)
        self.assertEqual(bug_publish.call_count, 1)
        self.assertEqual(self.git(root, "merge-base", rejected_shas["bug"], bug["resultSha"]), bug["baseSha"])
