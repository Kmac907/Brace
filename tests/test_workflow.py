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
            ".codex/schemas/closure-record.schema.json",
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
                ".codex/schemas/closure-record.schema.json",
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

        verifier = {
            "approved": True, "summary": "approved", "findings": [],
            "checks": [{"command": "test -f src/product.txt", "result": "passed", "evidence": "file exists"}],
            "blocker": None,
        }

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
        audit_contexts: list[str] = []
        final_review_calls = 0
        validation_calls = 0

        def fake_audit_role(repository, worktree, role, context, schema, sandbox):
            nonlocal validation_calls
            if role == "auditor":
                audit_contexts.append(context)
                return audit_result
            validation_calls += 1
            if validation_calls == 1:
                return verifier | {"approved": False, "summary": "failed", "findings": ["required checks failed"], "checks": []}
            return verifier | {"checks": []} if validation_calls == 2 else verifier

        def fake_final_reviews(repository, state_paths, item, kind):
            nonlocal final_review_calls
            final_review_calls += 1
            results = (self.reviewer_result(), self.reviewer_result(False)) if final_review_calls == 1 else None
            return self.persist_reviews(state_paths, item, kind, results)

        def fake_new_pr(repository, configuration, head, base, expected_head, expected_base, title, body):
            return {
                "id": "2", "url": "https://example.invalid/2", "state": "open", "repository": "owner/repo",
                "head": head, "headSha": expected_head, "base": base, "baseSha": expected_base, "mergeSha": None,
            }

        def fake_complete(repository, configuration, pull_request, expected_head, expected_base):
            self.assertEqual((expected_head, expected_base), (pull_request["headSha"], pull_request["baseSha"]))
            self.git(root, "push", "origin", f"{pull_request['headSha']}:refs/heads/{configuration['targetBranch']}")
            return {**pull_request, "state": "merged", "mergeSha": pull_request["headSha"]}

        with (
            patch.object(audit_loop, "assert_prerequisites"),
            patch.object(audit_loop, "invoke_role", side_effect=fake_audit_role),
            patch.object(audit_loop, "run_reviews", side_effect=fake_final_reviews),
            patch.object(audit_loop, "new_pull_request", side_effect=fake_new_pr) as project_pr,
            patch.object(audit_loop, "complete_pull_request", side_effect=fake_complete),
        ):
            with self.assertRaisesRegex(common.BraceError, "Final validation failed: required checks failed"):
                audit_loop.run(root)
            with self.assertRaisesRegex(common.BraceError, "missing passed evidence.*test -f src/product.txt"):
                audit_loop.run(root)
            self.assertEqual(audit_loop.run(root), "complete")

        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        self.assertEqual(state["stage"], "complete")
        self.assertEqual(bugs["status"], "complete")
        self.assertEqual(bugs["auditCycle"], 4)
        self.assertEqual(validation_calls, 4)
        self.assertEqual(final_review_calls, 2)
        self.assertEqual(project_pr.call_count, 1)
        self.assertIn("src/product.txt:1", audit_contexts[3])
        self.assertEqual(len(list(paths.results.glob("TASK-0000-attempt-*-review-*.json"))), 4)
        self.assertTrue((paths.results / "CLOSURE-attempt-001-audit.json").is_file())
        self.assertTrue((paths.results / "CLOSURE-attempt-002-audit.json").is_file())
        self.assertTrue((paths.results / "CLOSURE-attempt-003-audit.json").is_file())
        self.assertTrue((paths.results / "CLOSURE-attempt-004-audit.json").is_file())
        self.assertTrue((paths.results / "CLOSURE-attempt-004-validation.json").is_file())
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
        verifier = {
            "approved": True, "summary": "approved", "findings": [],
            "checks": [
                {"command": "test -f src/product.txt", "result": "passed", "evidence": "file exists"},
                {"command": "output is correct", "result": "passed", "evidence": "regression passed"},
            ],
            "blocker": None,
        }
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

        audit_calls = 0

        def fake_audit_role(repository, worktree, role, context, schema, sandbox):
            nonlocal audit_calls
            if role == "auditor":
                audit_calls += 1
                return audit_result if audit_calls == 1 else {
                    "status": "completed", "summary": "clean closure", "checks": [],
                    "missingEvidence": [], "blocker": None, "bugs": [],
                }
            return verifier

        def fake_bug_reviews(repository, state_paths, item, kind):
            nonlocal bug_verifications
            if kind == "task":
                return self.persist_reviews(state_paths, item, kind)
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

        def fake_complete(repository, configuration, pull_request, expected_head, expected_base):
            self.assertEqual((expected_head, expected_base), (pull_request["headSha"], pull_request["baseSha"]))
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
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        bug = bugs["bugs"][0]
        self.assertEqual(audit_calls, 2)
        self.assertEqual(bugs["auditCycle"], 2)
        self.assertEqual(bugs["auditSha"], state["integrationSha"])
        self.assertEqual(bug["attemptCount"], 2)
        self.assertEqual(bug_verifications, 3)
        self.assertEqual(len(list(paths.results.glob("BUG-0001-attempt-*-review-*.json"))), 4)
        self.assertEqual(bug_publish.call_count, 1)
        self.assertEqual(self.git(root, "merge-base", rejected_shas["bug"], bug["resultSha"]), bug["baseSha"])

    def test_parallel_task_and_bug_waves_requeue_stale_reviews(self) -> None:
        root, _, config = self.prepare()
        config.update(maximumConcurrentBuilders=3, maximumConcurrentFixers=3)
        paths = common.Paths(root)
        common.write_text_atomic(paths.config, common.pretty_json(config))
        self.git(root, "add", ".codex/workflow.json")
        self.git(root, "commit", "-m", "configure bounded three-agent waves")
        self.git(root, "push", "origin", "main")
        planned = self.planner_result()
        planned["tasks"][0].update(allowedPaths=["task-one.txt"], checks=["check task one"])
        planned["tasks"].append({
            **planned["tasks"][0], "taskId": "TASK-0028", "title": "Implement second feature",
            "allowedPaths": ["task-two.txt"], "checks": ["check task two"],
        })
        planned["tasks"].append({
            **planned["tasks"][0], "taskId": "TASK-0038", "title": "Implement third feature",
            "allowedPaths": ["task-three.txt"], "checks": ["check task three"],
        })
        planned["summary"].update(taskCount=3, parallelizableTaskCount=3)
        with patch.object(planning_loop, "assert_prerequisites"), patch.object(planning_loop, "invoke_role", return_value=planned):
            planning_loop.run(root)

        assignments: list[tuple[str, str, int, str, str]] = []
        publications: list[tuple[str, str, str, str]] = []

        def fake_assignment(repository, worktree, item, kind, state_paths):
            path = Path(worktree)
            identity = item["taskId" if kind == "task" else "bugId"]
            filename = item["allowedPaths"][0]
            starting_head = self.git(path, "rev-parse", "HEAD")
            if item.get("resultSha"):
                self.assertEqual(starting_head, item["resultSha"])
                self.git(path, "merge", "--no-edit", item["baseSha"])
            else:
                self.assertEqual(starting_head, item["baseSha"])
                (path / filename).write_text(f"{identity} attempt {item['attemptCount']}\n", encoding="utf-8")
                self.git(path, "add", filename)
                self.git(path, "commit", "-m", f"{identity} attempt {item['attemptCount']}")
            head = self.git(path, "rev-parse", "HEAD")
            assignments.append((kind, identity, item["attemptCount"], item["baseSha"], head))
            result = {
                "status": "completed" if kind == "task" else "fixed", "summary": "implemented",
                "commitSha": head, "checks": [], "blocker": None,
                "filesChanged" if kind == "task" else "changedFiles": [filename],
            }
            record = {
                "schemaVersion": "1.0", "identity": identity, "attempt": item["attemptCount"],
                "succeeded": True, "result": result, "error": None, "completedAt": common.utc_now(),
            }
            common.write_immutable_json(common.attempt_path(state_paths, "result", identity, item["attemptCount"]), record)
            return record

        def fake_reviews(repository, state_paths, item, kind):
            return self.persist_reviews(state_paths, item, kind)

        def fake_publish(repository, worktree, configuration, item, kind):
            identity = item["taskId" if kind == "task" else "bugId"]
            base = self.git(root, "rev-parse", f"origin/{configuration['integrationBranch']}")
            self.assertEqual(item["baseSha"], base)
            tree = self.git(root, "rev-parse", f"{item['resultSha']}^{{tree}}")
            merge_sha = self.git(root, "commit-tree", tree, "-p", base, "-m", f"merge {identity}")
            self.git(root, "push", "origin", f"{merge_sha}:refs/heads/{configuration['integrationBranch']}")
            publications.append((kind, identity, base, merge_sha))
            return {
                "id": identity, "url": f"https://example.invalid/{identity}", "state": "merged",
                "repository": "owner/repo", "head": item["branch"], "headSha": item["resultSha"],
                "base": configuration["integrationBranch"], "baseSha": base, "mergeSha": merge_sha,
            }

        checks = ["check task one", "check task two", "check task three", "check bug one", "check bug two", "check bug three"]
        verifier = {
            "approved": True, "summary": "approved", "findings": [], "blocker": None,
            "checks": [{"command": command, "result": "passed", "evidence": "passed"} for command in checks],
        }
        with (
            patch.object(build_loop, "assert_prerequisites"),
            patch.object(build_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(build_loop, "run_reviews", side_effect=fake_reviews),
            patch.object(build_loop, "publish_assignment", side_effect=fake_publish),
            patch.object(build_loop, "invoke_role", return_value=verifier),
        ):
            self.assertEqual(build_loop.run(root), "audit")

        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        self.assertEqual([task["attemptCount"] for task in tasks["tasks"]], [1, 2, 2])
        self.assertEqual(len(list(paths.results.glob("TASK-0001-attempt-*-review-*.json"))), 2)
        self.assertEqual(len(list(paths.results.glob("TASK-0002-attempt-*-review-*.json"))), 4)
        self.assertEqual(len(list(paths.results.glob("TASK-0003-attempt-*-review-*.json"))), 4)

        findings = [{
            "bugId": identity, "title": title, "severity": "high", "category": "correctness",
            "requirementIds": ["REQ-ONE"], "description": "output is wrong", "evidence": "reproduced",
            "actualBehavior": "wrong", "requiredBehavior": "correct", "impact": "workflow blocks",
            "requiredCorrection": "correct output", "acceptanceTest": check, "dependencies": [],
            "allowedPaths": [filename], "exclusiveResources": [],
        } for identity, title, filename, check in (
            ("BUG-0018", "Fix first bug", "bug-one.txt", "check bug one"),
            ("BUG-0028", "Fix second bug", "bug-two.txt", "check bug two"),
            ("BUG-0038", "Fix third bug", "bug-three.txt", "check bug three"),
        )]
        audit_calls = 0

        def fake_audit_role(repository, worktree, role, context, schema, sandbox):
            nonlocal audit_calls
            if role != "auditor":
                return verifier
            audit_calls += 1
            return {
                "status": "completed", "summary": "findings" if audit_calls == 1 else "clean",
                "bugs": findings if audit_calls == 1 else [], "checks": [], "missingEvidence": [], "blocker": None,
            }

        def fake_new_pr(repository, configuration, head, base, expected_head, expected_base, title, body):
            return {
                "id": "project", "url": "https://example.invalid/project", "state": "open",
                "repository": "owner/repo", "head": head, "headSha": expected_head,
                "base": base, "baseSha": expected_base, "mergeSha": None,
            }

        def fake_complete(repository, configuration, pull_request, expected_head, expected_base):
            self.assertEqual((expected_head, expected_base), (pull_request["headSha"], pull_request["baseSha"]))
            self.git(root, "push", "origin", f"{expected_head}:refs/heads/{configuration['targetBranch']}")
            return {**pull_request, "state": "merged", "mergeSha": expected_head}

        with (
            patch.object(audit_loop, "assert_prerequisites"),
            patch.object(audit_loop, "run_assignment", side_effect=fake_assignment),
            patch.object(audit_loop, "run_reviews", side_effect=fake_reviews),
            patch.object(audit_loop, "publish_assignment", side_effect=fake_publish),
            patch.object(audit_loop, "invoke_role", side_effect=fake_audit_role),
            patch.object(audit_loop, "new_pull_request", side_effect=fake_new_pr),
            patch.object(audit_loop, "complete_pull_request", side_effect=fake_complete),
        ):
            self.assertEqual(audit_loop.run(root), "complete")

        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        self.assertEqual([bug["attemptCount"] for bug in bugs["bugs"]], [1, 2, 2])
        self.assertEqual(len(list(paths.results.glob("BUG-0001-attempt-*-review-*.json"))), 2)
        self.assertEqual(len(list(paths.results.glob("BUG-0002-attempt-*-review-*.json"))), 4)
        self.assertEqual(len(list(paths.results.glob("BUG-0003-attempt-*-review-*.json"))), 4)
        for kind in ("task", "bug"):
            kind_publications = [record for record in publications if record[0] == kind]
            initial = [record for record in assignments if record[0] == kind and record[2] == 1]
            retries = [record for record in assignments if record[0] == kind and record[2] == 2]
            self.assertEqual(len({record[3] for record in initial}), 1)
            self.assertEqual([record[3] for record in retries], [record[3] for record in kind_publications[:2]])

    def test_build_restart_requeues_open_pr_before_merge_validation(self) -> None:
        root, _, config = self.prepare()
        planned = self.planner_result()
        planned["tasks"].append({
            **planned["tasks"][0], "taskId": "TASK-0002", "title": "Merged sibling",
            "allowedPaths": ["sibling.txt"],
        })
        planned["summary"].update(taskCount=2, parallelizableTaskCount=2)
        with patch.object(planning_loop, "assert_prerequisites"), patch.object(planning_loop, "invoke_role", return_value=planned):
            planning_loop.run(root)
        paths = common.Paths(root)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        base = common.ensure_integration_branch(root, config, state)
        task = tasks["tasks"][0]
        task.update(
            status="verified_ready", attemptCount=1, branch="worktree/TASK-0001", baseSha=base,
        )
        worktree = common.new_worktree(root, config, task["taskId"], task["branch"], base)
        (worktree / "src").mkdir()
        (worktree / "src" / "product.txt").write_text("candidate\n", encoding="utf-8")
        self.git(worktree, "add", "src/product.txt")
        self.git(worktree, "commit", "-m", "candidate")
        task.update(worktree=str(worktree), resultSha=self.git(worktree, "rev-parse", "HEAD"))
        self.persist_reviews(paths, task, "task")
        tasks["status"] = "active"
        tree = self.git(root, "rev-parse", f"{base}^{{tree}}")
        advanced = self.git(root, "commit-tree", tree, "-p", base, "-m", "merged sibling")
        self.git(root, "push", "origin", f"{advanced}:refs/heads/{config['integrationBranch']}")
        sibling = tasks["tasks"][1]
        sibling.update(
            status="integrated", attemptCount=1, branch="worktree/TASK-0002", baseSha=base, resultSha=advanced,
            pullRequest={
                "id": "2", "url": "https://example.invalid/2", "state": "merged", "repository": "owner/repo",
                "head": "worktree/TASK-0002", "headSha": advanced, "base": config["integrationBranch"],
                "baseSha": base, "mergeSha": advanced,
            },
        )
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        state.update(stage="build", stageStatus="running", integrationSha=advanced)
        common.save_state(state, paths)
        open_pr = {
            "id": "1", "url": "https://example.invalid/1", "state": "open", "repository": "owner/repo",
            "head": task["branch"], "headSha": task["resultSha"], "base": config["integrationBranch"],
            "baseSha": advanced, "mergeSha": None,
        }

        with (
            patch.object(build_loop, "assert_prerequisites"),
            patch.object(build_loop, "get_pull_request", return_value=open_pr),
            patch.object(common, "complete_pull_request", side_effect=AssertionError("stale PR was completed")),
            patch.object(build_loop, "select_ready_items", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            build_loop.run(root)
        persisted = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")["tasks"][0]
        self.assertEqual((persisted["status"], persisted["baseSha"], persisted["resultSha"]), ("pending", advanced, task["resultSha"]))
        self.assertEqual(persisted["pullRequest"]["id"], open_pr["id"])

    def test_build_rejects_unowned_remote_before_candidate_mutation(self) -> None:
        root, _, config = self.prepare()
        self.plan(root)
        paths = common.Paths(root)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        base = common.ensure_integration_branch(root, config, state)
        task = tasks["tasks"][0]
        task.update(status="verified_ready", attemptCount=1, branch="worktree/TASK-0001", baseSha=base)
        worktree = common.new_worktree(root, config, task["taskId"], task["branch"], base)
        (worktree / "src").mkdir()
        (worktree / "src" / "product.txt").write_text("candidate\n", encoding="utf-8")
        self.git(worktree, "add", "src/product.txt")
        self.git(worktree, "commit", "-m", "candidate")
        task.update(worktree=str(worktree), resultSha=self.git(worktree, "rev-parse", "HEAD"))
        self.persist_reviews(paths, task, "task")
        tasks["status"] = "active"
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        state.update(stage="build", stageStatus="running", integrationSha=base)
        common.save_state(state, paths)
        before = common.pretty_json(task)
        candidate = task["resultSha"]

        tree = self.git(root, "rev-parse", f"{base}^{{tree}}")
        unowned = self.git(root, "commit-tree", tree, "-p", base, "-m", "unowned remote commit")
        self.git(root, "push", "origin", f"{unowned}:refs/heads/{config['integrationBranch']}")
        with (
            patch.object(build_loop, "assert_prerequisites"),
            patch.object(build_loop, "get_pull_request", return_value=None),
            self.assertRaisesRegex(common.BraceError, "unowned commits"),
        ):
            build_loop.run(root)

        persisted = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")["tasks"][0]
        self.assertEqual(common.pretty_json(persisted), before)
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), candidate)

    def test_audit_restart_requeues_open_pr_before_merge_validation(self) -> None:
        root, _, config = self.prepare()
        self.plan(root)
        paths = common.Paths(root)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        base = common.ensure_integration_branch(root, config, state)
        tree = self.git(root, "rev-parse", f"{base}^{{tree}}")
        advanced = self.git(root, "commit-tree", tree, "-p", base, "-m", "merged sibling")
        self.git(root, "push", "origin", f"{advanced}:refs/heads/{config['integrationBranch']}")
        tasks["tasks"][0].update(
            status="integrated", attemptCount=1, branch="worktree/TASK-0001", baseSha=base, resultSha=advanced,
            pullRequest={
                "id": "task", "url": "https://example.invalid/task", "state": "merged", "repository": "owner/repo",
                "head": "worktree/TASK-0001", "headSha": advanced, "base": config["integrationBranch"],
                "baseSha": base, "mergeSha": advanced,
            },
        )
        tasks["status"] = "complete"
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        finding = {
            "bugId": "BUG-0001", "title": "Fix candidate", "severity": "high", "category": "correctness",
            "requirementIds": ["REQ-ONE"], "description": "wrong", "evidence": "reproduced",
            "actualBehavior": "wrong", "requiredBehavior": "correct", "impact": "workflow blocks",
            "requiredCorrection": "correct it", "acceptanceTest": "check bug", "dependencies": [],
            "allowedPaths": ["bug.txt"], "exclusiveResources": [],
        }
        bug = audit_loop.persisted_bug(finding)
        bug.update(status="ready_to_publish", disposition="fixed", attemptCount=1, branch="worktree/BUG-0001", baseSha=base)
        worktree = common.new_worktree(root, config, bug["bugId"], bug["branch"], base)
        (worktree / "bug.txt").write_text("candidate\n", encoding="utf-8")
        self.git(worktree, "add", "bug.txt")
        self.git(worktree, "commit", "-m", "candidate")
        bug.update(worktree=str(worktree), resultSha=self.git(worktree, "rev-parse", "HEAD"))
        self.persist_reviews(paths, bug, "bug")
        bugs.update(
            status="active", auditCycle=1, auditSha=advanced, bugs=[bug],
            definitionHash=common.definition_hash([bug], "bug"),
        )
        common.write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")
        state.update(
            stage="audit", stageStatus="running", integrationSha=advanced,
            bugDefinitionHash=bugs["definitionHash"],
        )
        common.save_state(state, paths)
        open_pr = {
            "id": "1", "url": "https://example.invalid/1", "state": "open", "repository": "owner/repo",
            "head": bug["branch"], "headSha": bug["resultSha"], "base": config["integrationBranch"],
            "baseSha": advanced, "mergeSha": None,
        }

        with (
            patch.object(audit_loop, "assert_prerequisites"),
            patch.object(audit_loop, "get_pull_request", return_value=open_pr),
            patch.object(audit_loop, "complete_pull_request", side_effect=AssertionError("stale PR was completed")),
            patch.object(audit_loop, "select_ready_items", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            audit_loop.run(root)
        persisted = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")["bugs"][0]
        self.assertEqual((persisted["status"], persisted["baseSha"], persisted["resultSha"]), ("open", advanced, bug["resultSha"]))
        self.assertEqual(persisted["pullRequest"]["id"], open_pr["id"])

    def test_audit_rejects_unowned_remote_before_candidate_mutation(self) -> None:
        root, _, config = self.prepare()
        self.plan(root)
        paths = common.Paths(root)
        state = common.read_json(paths.state, paths.schemas / "state.schema.json")
        tasks = common.read_json(paths.tasks, paths.schemas / "tasks.schema.json")
        bugs = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")
        base = common.ensure_integration_branch(root, config, state)
        task = tasks["tasks"][0]
        task.update(
            status="integrated", attemptCount=1, branch="worktree/TASK-0001", baseSha=base, resultSha=base,
            pullRequest={
                "id": "task", "url": "https://example.invalid/task", "state": "merged", "repository": "owner/repo",
                "head": "worktree/TASK-0001", "headSha": base, "base": config["integrationBranch"],
                "baseSha": base, "mergeSha": base,
            },
        )
        tasks["status"] = "complete"
        common.write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
        finding = {
            "bugId": "BUG-0001", "title": "Fix candidate", "severity": "high", "category": "correctness",
            "requirementIds": ["REQ-ONE"], "description": "wrong", "evidence": "reproduced",
            "actualBehavior": "wrong", "requiredBehavior": "correct", "impact": "workflow blocks",
            "requiredCorrection": "correct it", "acceptanceTest": "check bug", "dependencies": [],
            "allowedPaths": ["bug.txt"], "exclusiveResources": [],
        }
        bug = audit_loop.persisted_bug(finding)
        bug.update(status="ready_to_publish", disposition="fixed", attemptCount=1, branch="worktree/BUG-0001", baseSha=base)
        worktree = common.new_worktree(root, config, bug["bugId"], bug["branch"], base)
        (worktree / "bug.txt").write_text("candidate\n", encoding="utf-8")
        self.git(worktree, "add", "bug.txt")
        self.git(worktree, "commit", "-m", "candidate")
        bug.update(worktree=str(worktree), resultSha=self.git(worktree, "rev-parse", "HEAD"))
        self.persist_reviews(paths, bug, "bug")
        bugs.update(
            status="active", auditCycle=1, auditSha=base, bugs=[bug],
            definitionHash=common.definition_hash([bug], "bug"),
        )
        common.write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")
        state.update(stage="audit", stageStatus="running", integrationSha=base, bugDefinitionHash=bugs["definitionHash"])
        common.save_state(state, paths)
        before = common.pretty_json(bug)
        candidate = bug["resultSha"]

        tree = self.git(root, "rev-parse", f"{base}^{{tree}}")
        unowned = self.git(root, "commit-tree", tree, "-p", base, "-m", "unowned remote commit")
        self.git(root, "push", "origin", f"{unowned}:refs/heads/{config['integrationBranch']}")
        with (
            patch.object(audit_loop, "assert_prerequisites"),
            patch.object(audit_loop, "get_pull_request", return_value=None),
            self.assertRaisesRegex(common.BraceError, "unowned commits"),
        ):
            audit_loop.run(root)

        persisted = common.read_json(paths.bugs, paths.schemas / "bugs.schema.json")["bugs"][0]
        self.assertEqual(common.pretty_json(persisted), before)
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), candidate)
