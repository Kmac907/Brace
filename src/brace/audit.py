from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .common import (
    BraceError,
    WorkflowLock,
    assert_assignment_commit,
    assert_graph,
    assert_ledger_identity,
    assert_plan_drift,
    assert_prerequisites,
    assert_state_identity,
    assert_target_drift,
    attempt_path,
    canonicalize_graph_identities,
    complete_pull_request,
    definition_hash,
    ensure_integration_branch,
    get_configuration,
    get_pull_request,
    has_matching_review_results,
    initialize_state_files,
    invoke_role,
    new_audit_worktree,
    new_pull_request,
    new_worktree,
    pretty_json,
    publish_assignment,
    read_attempt_result,
    read_review_result,
    read_json,
    recover_committed_attempt,
    require_approved_reviews,
    reset_rejected_assignment,
    remove_audit_worktree,
    remove_empty_worktree_containers,
    remove_merged_assignment,
    remove_worktree,
    repository_root,
    review_failure,
    run_assignment,
    run_reviews,
    run_native,
    save_state,
    select_ready_items,
    set_blocked,
    show_status,
    utc_now,
    validate_json,
    worktree_base,
    write_immutable_json,
    write_json_atomic,
    write_summary,
)
from .project_manager import (
    invoke_pm_resolution,
    is_semantic_blocker,
    structured_blocker,
)
from .ui import status, success, warning

InputReader = Callable[[dict[str, Any], str, dict[str, Any] | None], str]


def save_ledger(ledger: dict[str, Any], paths: Any) -> None:
    ledger["revision"] += 1
    write_json_atomic(paths.bugs, ledger, paths.schemas / "bugs.schema.json")


def known_merges(tasks: dict[str, Any], bugs: dict[str, Any]) -> list[str]:
    return [item["pullRequest"]["mergeSha"] for item in tasks["tasks"] + bugs["bugs"] if (item.get("pullRequest") or {}).get("mergeSha")]


def persisted_bug(bug: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: bug[key] for key in (
            "bugId", "title", "severity", "category", "requirementIds", "description", "evidence",
            "actualBehavior", "requiredBehavior", "impact", "requiredCorrection", "acceptanceTest",
            "dependencies", "allowedPaths", "exclusiveResources",
        )},
        "status": "open", "disposition": None, "dispositionEvidence": None, "attemptCount": 0,
        "branch": None, "worktree": None, "baseSha": None, "resultSha": None, "pullRequest": None,
        "lastError": None, "amendmentId": None,
    }


def append_findings(existing: list[dict[str, Any]], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if findings:
        canonicalize_graph_identities(findings, "bug", starting_ordinal=len(existing) + 1)
    combined = existing + [persisted_bug(bug) for bug in findings]
    if combined:
        assert_graph(combined, "bug")
    return combined


def closure_path(paths: Any, cycle: int, kind: str) -> Path:
    if cycle < 1 or kind not in {"audit", "validation"}:
        raise BraceError(f"Invalid closure record identity: cycle {cycle} {kind}")
    return paths.results / f"CLOSURE-attempt-{cycle:03d}-{kind}.json"


def read_closure_result(paths: Any, cycle: int, kind: str, candidate_sha: str) -> dict[str, Any] | None:
    path = closure_path(paths, cycle, kind)
    if not path.is_file():
        return None
    record = read_json(path, paths.schemas / "closure-record.schema.json")
    return record if (record["cycle"], record["kind"], record["candidateSha"]) == (cycle, kind, candidate_sha) else None


def write_closure_result(paths: Any, cycle: int, kind: str, candidate_sha: str, result: dict[str, Any]) -> dict[str, Any]:
    record = {
        "schemaVersion": "1.0", "kind": kind, "cycle": cycle, "candidateSha": candidate_sha,
        "completedAt": utc_now(), "result": result,
    }
    validate_json(record, paths.schemas / "closure-record.schema.json")
    write_immutable_json(closure_path(paths, cycle, kind), record)
    return record


def next_closure_cycle(paths: Any, previous_cycle: int, candidate_sha: str) -> int:
    cycle = previous_cycle + 1
    while closure_path(paths, cycle, "audit").is_file() and read_closure_result(paths, cycle, "audit", candidate_sha) is None:
        cycle += 1
    return cycle


def final_review_item(state: dict[str, Any], tasks: dict[str, Any], bugs: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    requirements = sorted({requirement for task in tasks["tasks"] for requirement in task["requirementIds"]})
    checks = [check["command"] for check in validation["checks"]]
    return {
        "taskId": "TASK-0000", "title": "Final project candidate",
        "description": "Review the frozen integration candidate against the complete approved requirements and plan.",
        "requirementIds": requirements, "planSections": ["Complete approved plan"], "dependencies": [],
        "allowedPaths": ["**"], "exclusiveResources": [],
        "acceptanceCriteria": [
            "Every active task and bug is integrated or verified.",
            "The latest closure audit found no supported defect at this exact candidate SHA.",
            "Final validation passed at this exact candidate SHA.",
            "The complete original-baseline diff preserves required compatibility, recovery, and cleanup behavior.",
        ],
        "checks": checks, "attemptCount": bugs["auditCycle"],
        "baseSha": state["targetBaseSha"], "resultSha": state["integrationSha"],
    }


def previous_final_findings(paths: Any, state: dict[str, Any], bugs: dict[str, Any]) -> list[dict[str, Any]]:
    if bugs["auditCycle"] < 1 or bugs.get("auditSha") != state["integrationSha"]:
        return []
    findings: list[dict[str, Any]] = []
    for reviewer in (1, 2):
        record = read_review_result(
            paths, "TASK-0000", bugs["auditCycle"], reviewer,
            state["targetBaseSha"], state["integrationSha"],
        )
        if record:
            findings.extend(record["result"]["findings"])
    return findings


def require_final_evidence(paths: Any, state: dict[str, Any], tasks: dict[str, Any], bugs: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if bugs["status"] != "complete" or bugs["auditSha"] != state["integrationSha"] or any(bug["status"] != "verified" for bug in bugs["bugs"]):
        raise BraceError("Final project merge has no clean closure audit for its exact integration SHA.")
    validation_record = read_closure_result(paths, bugs["auditCycle"], "validation", state["integrationSha"])
    if validation_record is None or not validation_record["result"]["approved"]:
        raise BraceError("Final project merge has no matching approved durable validation result.")
    validation = validation_record["result"]
    failed = [check["command"] for check in validation["checks"] if check["result"] != "passed"]
    if failed:
        raise BraceError("Final project validation has incomplete checks: " + ", ".join(failed))
    item = final_review_item(state, tasks, bugs, validation)
    return validation, require_approved_reviews(paths, item, "task")


def remove_completed_artifacts(root: Path, config: dict[str, Any], tasks: dict[str, Any], bugs: dict[str, Any]) -> None:
    for item in tasks["tasks"] + bugs["bugs"]:
        identity = item.get("taskId") or item["bugId"]
        branch = item.get("branch")
        if not branch:
            continue
        base = worktree_base(root, config)
        local_exists = (base / identity).is_dir() or run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], allowed_exit_codes=(0, 1)).returncode == 0
        if local_exists:
            remove_worktree(root, config, identity, branch)
        if config.get("deleteMergedBranches"):
            run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
            remote_ref = f"refs/remotes/{config['remote']}/{branch}"
            if run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", remote_ref], allowed_exit_codes=(0, 1)).returncode == 0:
                run_native("git", ["-C", root, "push", config["remote"], "--delete", branch])
                run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
                if run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", remote_ref], allowed_exit_codes=(0, 1)).returncode == 0:
                    raise BraceError(f"Remote assignment branch survived final cleanup: {branch}")


def complete_project_cleanup(root: Path, config: dict[str, Any], tasks: dict[str, Any], bugs: dict[str, Any], final_sha: str) -> None:
    remove_completed_artifacts(root, config, tasks, bugs)
    run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
    current = run_native("git", ["-C", root, "branch", "--show-current"]).output.strip()
    if current == config["targetBranch"]:
        run_native("git", ["-C", root, "merge", "--ff-only", f"{config['remote']}/{config['targetBranch']}"])
    else:
        run_native("git", ["-C", root, "branch", "-f", config["targetBranch"], final_sha])
    if run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{config['integrationBranch']}"], allowed_exit_codes=(0, 1)).returncode == 0:
        run_native("git", ["-C", root, "branch", "-D", "--", config["integrationBranch"]])
    if config.get("deleteMergedBranches") and run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/remotes/{config['remote']}/{config['integrationBranch']}"], allowed_exit_codes=(0, 1)).returncode == 0:
        run_native("git", ["-C", root, "push", config["remote"], "--delete", config["integrationBranch"]])
    remove_empty_worktree_containers(root, config)
    base = worktree_base(root, config)
    if base.is_dir() and any(base.iterdir()):
        raise BraceError("Owned worktree cleanup left orphaned directories.")


def _checks(root: Path, config: dict[str, Any], state: dict[str, Any], tasks: dict[str, Any], bugs: dict[str, Any] | None = None) -> None:
    assert_plan_drift(state, root, require_plan=True)
    assert_ledger_identity(state, tasks, "task")
    if bugs is not None:
        assert_ledger_identity(state, bugs, "bug")
    assert_target_drift(root, config, state)


def _handle_semantic(root: Path, config: dict[str, Any], state: dict[str, Any], paths: Any, tasks: dict[str, Any], bugs: dict[str, Any], kind: str, identity: str | None, blocker: dict[str, Any], input_reader: InputReader | None) -> str:
    resolution = invoke_pm_resolution(root, config, state, paths, tasks, bugs, "audit", kind, identity, blocker, input_reader)
    return resolution["resumeStage"]


def _run_once(repository: str | Path = ".", input_reader: InputReader | None = None) -> str:
    root = repository_root(repository)
    config = get_configuration(root)
    assert_prerequisites(config, require_codex=True)
    paths = initialize_state_files(root, config)
    state = tasks = bugs = None
    audit_worktree = None
    with WorkflowLock(paths.lock):
        try:
            state = read_json(paths.state, paths.schemas / "state.schema.json")
            tasks = read_json(paths.tasks, paths.schemas / "tasks.schema.json")
            bugs = read_json(paths.bugs, paths.schemas / "bugs.schema.json")
            if state.get("activeAmendment"):
                amendment = state["activeAmendment"]
                state["stage"] = amendment["sourceStage"]
                resolution = invoke_pm_resolution(root, config, state, paths, tasks, bugs, amendment["sourceStage"], amendment["sourceKind"], amendment["sourceIdentity"], amendment["blocker"], input_reader)
                if resolution["resumeStage"] == "build":
                    return "build"

            assert_state_identity(state, root, config)
            assert_plan_drift(state, root, require_plan=True)
            assert_ledger_identity(state, tasks, "task")
            if tasks["status"] != "complete" or any(task["status"] not in {"integrated", "superseded"} for task in tasks["tasks"]):
                raise BraceError("Every active implementation task must be integrated before audit begins.")
            if state["stage"] == "complete":
                show_status(state, tasks, bugs)
                success("PROJECT COMPLETE: audit, bug fixes, validation, and final merge are finished.")
                return "complete"
            if state["stage"] not in {"audit", "blocked"}:
                raise BraceError(f"The workflow is at stage {state['stage']}, not audit.")

            run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
            current_target = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['targetBranch']}"]).output.strip()
            if state.get("targetBaseSha") and current_target != state["targetBaseSha"] and state.get("integrationSha"):
                require_final_evidence(paths, state, tasks, bugs)
                final_pr = get_pull_request(root, config, config["integrationBranch"], config["targetBranch"], state["integrationSha"])
                if not final_pr or final_pr["state"] not in {"merged", "completed"}:
                    raise BraceError("Target branch advanced without the exact workflow project pull request.")
                final_pr = complete_pull_request(root, config, final_pr, state["integrationSha"], state["targetBaseSha"])
                complete_project_cleanup(root, config, tasks, bugs, final_pr["mergeSha"])
                state.update(stage="complete", stageStatus="complete", finalMergeSha=final_pr["mergeSha"], blocker=None)
                save_state(state, paths)
                write_summary(paths.audit_summary, {
                    "completedAt": utc_now(), "recoveredAfterFinalMerge": True, "totalBugs": len(bugs["bugs"]),
                    "verifiedBugs": sum(bug["status"] == "verified" for bug in bugs["bugs"]),
                    "projectPullRequest": final_pr, "finalMergeSha": state["finalMergeSha"],
                })
                show_status(state, tasks, bugs)
                success("PROJECT COMPLETE: recovered and verified the final project merge.")
                return "complete"

            assert_target_drift(root, config, state)
            state.update(stage="audit", stageStatus="running", blocker=None)
            state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            save_state(state, paths)

            if bugs["status"] == "not_audited" or bugs.get("auditSha") != state["integrationSha"]:
                cycle = next_closure_cycle(paths, bugs["auditCycle"], state["integrationSha"])
                review_findings = previous_final_findings(paths, state, bugs)
                audit_worktree = new_audit_worktree(root, config, f"{config['remote']}/{config['integrationBranch']}")
                audit_record = read_closure_result(paths, cycle, "audit", state["integrationSha"])
                if audit_record is None:
                    with status(f"Running closure audit cycle {cycle}"):
                        audit_result = invoke_role(root, audit_worktree, "auditor", f"Run a fresh comprehensive closure audit of exact integration commit {state['integrationSha']} against the complete approved requirements and plan. This is closure cycle {cycle}. Return only newly supported findings for this candidate; the existing immutable bug history contains {len(bugs['bugs'])} entries. Independently reconcile these findings from the preceding final reviewers after both completed: {pretty_json(review_findings)}. Do not edit the worktree.", "audit-result.schema.json", "read-only")
                    audit_record = write_closure_result(paths, cycle, "audit", state["integrationSha"], audit_result)
                audit_result = audit_record["result"]
                if audit_result["status"] == "completed" and audit_result["missingEvidence"]:
                    raise BraceError("Closure audit is incomplete: " + "; ".join(audit_result["missingEvidence"]))
                if audit_result["status"] == "blocked":
                    blocker = structured_blocker(audit_result["blocker"], "audit", None)
                    if is_semantic_blocker(blocker):
                        resume = _handle_semantic(root, config, state, paths, tasks, bugs, "audit", None, blocker, input_reader)
                        remove_audit_worktree(root, config)
                        audit_worktree = None
                        return resume
                    raise BraceError(f"Audit blocked: {blocker['message']}")
                _checks(root, config, state, tasks)
                unchanged = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
                if unchanged != state["integrationSha"]:
                    state["integrationSha"] = unchanged
                    bugs["status"] = "not_audited"
                    save_ledger(bugs, paths)
                    save_state(state, paths)
                    remove_audit_worktree(root, config)
                    audit_worktree = None
                    return "_restart"
                persisted = append_findings(bugs["bugs"], audit_result["bugs"])
                bug_hash = definition_hash(persisted, "bug")
                bugs.update(
                    schemaVersion="1.3", revision=bugs["revision"] + 1, auditCycle=cycle,
                    auditSha=state["integrationSha"], definitionHash=bug_hash,
                    status="ready" if audit_result["bugs"] else "complete", bugs=persisted,
                )
                state["bugDefinitionHash"] = bug_hash
                write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")
                save_state(state, paths)
                remove_audit_worktree(root, config)
                audit_worktree = None
            else:
                if not bugs.get("auditSha"):
                    raise BraceError("Existing bug ledger has no audit SHA.")
                assert_ledger_identity(state, bugs, "bug")
                if bugs["bugs"]:
                    assert_graph(bugs["bugs"], "bug")

            for bug in (item for item in bugs["bugs"] if item["status"] == "active"):
                record = read_attempt_result(paths, bug["bugId"], bug["attemptCount"]) or recover_committed_attempt(root, paths, bug, "bug")
                if record and record["succeeded"]:
                    bug["status"] = "result_ready"
                else:
                    bug.update(status="open", lastError="Interrupted before a durable result or commit was produced." if record is None else record["error"])
            for bug in (item for item in bugs["bugs"] if item["status"] == "ready_to_publish" and item.get("resultSha")):
                if not has_matching_review_results(paths, bug, "bug"):
                    bug.update(status="result_ready", lastError=None)
                    continue
                require_approved_reviews(paths, bug, "bug")
                existing = get_pull_request(root, config, bug["branch"], config["integrationBranch"], bug["resultSha"])
                if existing:
                    merged = complete_pull_request(root, config, existing, bug["resultSha"], bug["baseSha"])
                    bug.update(pullRequest=merged, status="verified", lastError=None)
                    state["integrationSha"] = merged["mergeSha"]
                    save_ledger(bugs, paths)
                    save_state(state, paths)
                    try:
                        remove_merged_assignment(root, config, bug["bugId"], bug["branch"], merged)
                    except Exception as error:
                        warning(str(error))
            save_ledger(bugs, paths)
            state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            save_state(state, paths)

            while any(bug["status"] != "verified" for bug in bugs["bugs"]):
                _checks(root, config, state, tasks, bugs)
                state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
                for candidate in (item for item in bugs["bugs"] if item["status"] == "result_ready"):
                    record = read_attempt_result(paths, candidate["bugId"], candidate["attemptCount"])
                    if record and record["succeeded"] and record["result"]["status"] == "blocked":
                        blocker = structured_blocker(record["result"]["blocker"], "audit", candidate["bugId"])
                        if is_semantic_blocker(blocker):
                            resume = _handle_semantic(root, config, state, paths, tasks, bugs, "bug", candidate["bugId"], blocker, input_reader)
                            if resume == "audit":
                                candidate.update(status="open", resultSha=None, disposition=None, lastError=None)
                                save_ledger(bugs, paths)
                            return resume

                for bug in (item for item in bugs["bugs"] if item["status"] == "result_ready"):
                    record = read_attempt_result(paths, bug["bugId"], bug["attemptCount"])
                    if not record or not record["succeeded"]:
                        bug["status"] = "open"
                        continue
                    result = record["result"]
                    try:
                        if result["status"] == "blocked":
                            blocker = structured_blocker(result["blocker"], "audit", bug["bugId"])
                            if is_semantic_blocker(blocker):
                                return _handle_semantic(root, config, state, paths, tasks, bugs, "bug", bug["bugId"], blocker, input_reader)
                            raise BraceError(f"Bug fixer blocked: {blocker['message']}")
                        context_label = "not-reproducible disposition" if result["status"] == "not_reproducible" else "bug correction"
                        if result["status"] != "not_reproducible":
                            commit = assert_assignment_commit(bug["worktree"], bug["baseSha"], bug)
                            if result["commitSha"] != commit["Head"]:
                                raise BraceError("Fixer result commit SHA does not match worktree HEAD.")
                            bug["resultSha"] = commit["Head"]
                        else:
                            candidate = run_native("git", ["-C", bug["worktree"], "rev-parse", "HEAD"]).output.strip()
                            if candidate != bug["baseSha"]:
                                raise BraceError("Not-reproducible disposition modified the bug worktree.")
                    except Exception as error:
                        if bug.get("resultSha"):
                            reset_rejected_assignment(root, config, bug, "bug")
                            bug["resultSha"] = None
                        bug.update(status="open", lastError=str(error))
                        continue
                    review_item = bug if bug.get("resultSha") else {**bug, "resultSha": candidate}
                    try:
                        reviews = run_reviews(root, paths, review_item, "bug")
                    except Exception as error:
                        bug["lastError"] = str(error)
                        save_ledger(bugs, paths)
                        raise
                    if not all(review["result"]["approved"] for review in reviews):
                        for review in reviews:
                            blocker = structured_blocker(review["result"]["blocker"], "audit", bug["bugId"])
                            if is_semantic_blocker(blocker):
                                return _handle_semantic(root, config, state, paths, tasks, bugs, "review", bug["bugId"], blocker, input_reader)
                        message = review_failure(reviews)
                        if bug.get("resultSha"):
                            reset_rejected_assignment(root, config, bug, "bug")
                            bug["resultSha"] = None
                        bug.update(status="open", lastError=message)
                        continue
                    require_approved_reviews(paths, review_item, "bug")
                    _checks(root, config, state, tasks, bugs)
                    if result["status"] == "not_reproducible":
                        bug.update(disposition="not_reproducible", status="verified", lastError=None)
                        remove_worktree(root, config, bug["bugId"], bug["branch"])
                    else:
                        bug.update(disposition="fixed", status="ready_to_publish", lastError=None)
                        save_ledger(bugs, paths)
                save_ledger(bugs, paths)

                for bug in (item for item in bugs["bugs"] if item["status"] == "ready_to_publish"):
                    _checks(root, config, state, tasks, bugs)
                    require_approved_reviews(paths, bug, "bug")
                    state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
                    merged = publish_assignment(root, bug["worktree"], config, bug, "bug")
                    bug.update(pullRequest=merged, status="verified", lastError=None)
                    state["integrationSha"] = merged["mergeSha"]
                    save_ledger(bugs, paths)
                    save_state(state, paths)
                    try:
                        remove_merged_assignment(root, config, bug["bugId"], bug["branch"], merged)
                    except Exception as error:
                        warning(str(error))
                if not any(bug["status"] != "verified" for bug in bugs["bugs"]):
                    break
                exhausted = [bug for bug in bugs["bugs"] if bug["status"] != "verified" and bug["attemptCount"] >= config["maximumBugAttempts"]]
                if exhausted:
                    for bug in exhausted:
                        bug["status"] = "blocked"
                    bugs["status"] = "blocked"
                    save_ledger(bugs, paths)
                    raise BraceError("Bug attempts exhausted: " + ", ".join(bug["bugId"] for bug in exhausted))
                wave = select_ready_items(bugs["bugs"], "bug", config["maximumConcurrentFixers"])
                if not wave:
                    raise BraceError("No dependency-ready, non-conflicting bugs remain.")
                base_sha = state["integrationSha"]
                for bug in wave:
                    bug["branch"] = bug.get("branch") or f"worktree/{bug['bugId']}"
                    bug["baseSha"] = bug.get("baseSha") or base_sha
                    bug["worktree"] = str(new_worktree(root, config, bug["bugId"], bug["branch"], bug["baseSha"], bug.get("resultSha")))
                    bug["attemptCount"] += 1
                    bug["status"] = "active"
                    write_immutable_json(attempt_path(paths, "assignment", bug["bugId"], bug["attemptCount"]), {
                        "schemaVersion": "1.0", "identity": bug["bugId"], "attempt": bug["attemptCount"],
                        "baseSha": bug["baseSha"], "startingHead": run_native("git", ["-C", bug["worktree"], "rev-parse", "HEAD"]).output.strip(),
                        "createdAt": utc_now(), "item": bug,
                    })
                bugs["status"] = "active"
                save_ledger(bugs, paths)
                with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                    records = {bug["bugId"]: pool.submit(run_assignment, root, bug["worktree"], bug, "bug", paths) for bug in wave}
                    for bug in wave:
                        record = records[bug["bugId"]].result()
                        bug.update(status="result_ready" if record and record["succeeded"] else "open", lastError=None if record and record["succeeded"] else ("Bug fixer returned no durable result." if not record else record["error"]))
                save_ledger(bugs, paths)
                show_status(state, tasks, bugs)

            _checks(root, config, state, tasks, bugs)
            state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            if bugs["auditSha"] != state["integrationSha"]:
                bugs["status"] = "not_audited"
                save_ledger(bugs, paths)
                save_state(state, paths)
                return "_restart"
            bugs["status"] = "complete"
            save_ledger(bugs, paths)
            audit_worktree = new_audit_worktree(root, config, f"{config['remote']}/{config['integrationBranch']}")
            validation_record = read_closure_result(paths, bugs["auditCycle"], "validation", state["integrationSha"])
            if validation_record is None:
                with status("Running final project validation"):
                    final_validation = invoke_role(root, audit_worktree, "verifier", f"Run final project validation at exact integration SHA {state['integrationSha']}. Execute the project-wide commands from plan.md.", "verifier-result.schema.json", "read-only")
                validation_record = write_closure_result(paths, bugs["auditCycle"], "validation", state["integrationSha"], final_validation)
            final_validation = validation_record["result"]
            if not final_validation["approved"]:
                blocker = structured_blocker(final_validation["blocker"], "audit", None)
                if is_semantic_blocker(blocker):
                    resume = _handle_semantic(root, config, state, paths, tasks, bugs, "verification", None, blocker, input_reader)
                    remove_audit_worktree(root, config)
                    audit_worktree = None
                    return resume
                raise BraceError("Final validation failed: " + "; ".join(final_validation["findings"]))
            incomplete_checks = [check["command"] for check in final_validation["checks"] if check["result"] != "passed"]
            if incomplete_checks:
                raise BraceError("Final validation has incomplete checks: " + ", ".join(incomplete_checks))
            remove_audit_worktree(root, config)
            audit_worktree = None
            _checks(root, config, state, tasks, bugs)
            head_sha = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            if head_sha != state["integrationSha"]:
                bugs["status"] = "not_audited"
                save_ledger(bugs, paths)
                state["integrationSha"] = head_sha
                save_state(state, paths)
                return "_restart"
            review_item = final_review_item(state, tasks, bugs, final_validation)
            reviews = run_reviews(root, paths, review_item, "task")
            if not all(review["result"]["approved"] for review in reviews):
                for review in reviews:
                    blocker = structured_blocker(review["result"]["blocker"], "audit", None)
                    if is_semantic_blocker(blocker):
                        return _handle_semantic(root, config, state, paths, tasks, bugs, "review", None, blocker, input_reader)
                bugs["status"] = "not_audited"
                save_ledger(bugs, paths)
                return "_restart"
            require_approved_reviews(paths, review_item, "task")
            _checks(root, config, state, tasks, bugs)
            head_sha = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            if head_sha != state["integrationSha"]:
                bugs["status"] = "not_audited"
                save_ledger(bugs, paths)
                state["integrationSha"] = head_sha
                save_state(state, paths)
                return "_restart"
            project_pr = new_pull_request(root, config, config["integrationBranch"], config["targetBranch"], head_sha, state["targetBaseSha"], "Complete project implementation", f"Completed Brace project and verified {len(bugs['bugs'])} audit findings at {head_sha}.")
            project_pr = complete_pull_request(root, config, project_pr, head_sha, state["targetBaseSha"])
            final_sha = project_pr["mergeSha"]
            complete_project_cleanup(root, config, tasks, bugs, final_sha)
            state.update(stage="complete", stageStatus="complete", finalMergeSha=final_sha, blocker=None)
            save_state(state, paths)
            attempts = [bug["attemptCount"] for bug in bugs["bugs"]]
            severity = Counter(bug["severity"] for bug in bugs["bugs"])
            write_summary(paths.audit_summary, {
                "completedAt": utc_now(), "auditSha": bugs["auditSha"], "totalBugs": len(bugs["bugs"]),
                "bugsBySeverity": [{"severity": key, "count": value} for key, value in severity.items()],
                "verifiedBugs": sum(bug["status"] == "verified" for bug in bugs["bugs"]),
                "totalAttempts": sum(attempts), "averageAttempts": round(sum(attempts) / len(attempts), 2) if attempts else 0,
                "bugCommits": [bug["resultSha"] for bug in bugs["bugs"] if bug.get("resultSha")],
                "bugPullRequests": [bug["pullRequest"] for bug in bugs["bugs"] if bug.get("pullRequest")],
                "auditCycle": bugs["auditCycle"], "finalValidation": final_validation,
                "finalReviews": [review["result"] for review in reviews],
                "projectPullRequest": project_pr, "finalMergeSha": final_sha, "remainingLimitations": [],
            })
            show_status(state, tasks, bugs)
            success("PROJECT COMPLETE: audit, bug fixes, validation, merge, and cleanup succeeded.")
            return "complete"
        except Exception as error:
            if audit_worktree is not None:
                try:
                    remove_audit_worktree(root, config)
                except Exception as cleanup_error:
                    warning(f"Unable to remove audit worktree while handling an error: {cleanup_error}")
            if state is not None:
                if bugs is not None:
                    try:
                        save_ledger(bugs, paths)
                    except Exception as save_error:
                        warning(f"Unable to preserve bug ledger while handling an error: {save_error}")
                set_blocked(state, paths, "audit", None, str(error), "Resolve the exact bug, provider, validation, drift, or environment blocker, then rerun brace audit.")
            raise


def run(repository: str | Path = ".", input_reader: InputReader | None = None) -> str:
    while True:
        result = _run_once(repository, input_reader)
        if result != "_restart":
            return result
