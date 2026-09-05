from __future__ import annotations

import argparse
import warnings
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from common import (
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
    complete_pull_request,
    definition_hash,
    ensure_integration_branch,
    get_configuration,
    get_pull_request,
    initialize_state_files,
    invoke_role,
    new_audit_worktree,
    new_pull_request,
    new_worktree,
    pretty_json,
    publish_assignment,
    read_attempt_result,
    read_json,
    recover_committed_attempt,
    remove_audit_worktree,
    remove_empty_worktree_containers,
    remove_merged_assignment,
    remove_worktree,
    repository_root,
    run_assignment,
    run_native,
    save_state,
    select_ready_items,
    set_blocked,
    show_status,
    utc_now,
    worktree_base,
    write_immutable_json,
    write_json_atomic,
    write_summary,
)
from project_manager import (
    invoke_pm_resolution,
    is_semantic_blocker,
    structured_blocker,
)

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


def run(repository: str | Path = ".", input_reader: InputReader | None = None) -> str:
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
                print("PROJECT COMPLETE: audit, bug fixes, validation, and final merge are finished.")
                return "complete"
            if state["stage"] not in {"audit", "blocked"}:
                raise BraceError(f"The workflow is at stage {state['stage']}, not audit.")

            run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
            current_target = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['targetBranch']}"]).output.strip()
            if state.get("targetBaseSha") and current_target != state["targetBaseSha"] and state.get("integrationSha"):
                final_pr = get_pull_request(root, config, config["integrationBranch"], config["targetBranch"], state["integrationSha"])
                if not final_pr or final_pr["state"] not in {"merged", "completed"}:
                    raise BraceError("Target branch advanced without the exact workflow project pull request.")
                final_pr = complete_pull_request(root, config, final_pr)
                complete_project_cleanup(root, config, tasks, bugs, final_pr["mergeSha"])
                state.update(stage="complete", stageStatus="complete", finalMergeSha=final_pr["mergeSha"], blocker=None)
                save_state(state, paths)
                write_summary(paths.audit_summary, {
                    "completedAt": utc_now(), "recoveredAfterFinalMerge": True, "totalBugs": len(bugs["bugs"]),
                    "verifiedBugs": sum(bug["status"] == "verified" for bug in bugs["bugs"]),
                    "projectPullRequest": final_pr, "finalMergeSha": state["finalMergeSha"],
                })
                show_status(state, tasks, bugs)
                print("PROJECT COMPLETE: recovered and verified the final project merge.")
                return "complete"

            assert_target_drift(root, config, state)
            state.update(stage="audit", stageStatus="running", blocker=None)
            state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            save_state(state, paths)

            if bugs["status"] == "not_audited":
                audit_worktree = new_audit_worktree(root, config, f"{config['remote']}/{config['integrationBranch']}")
                audit_result = invoke_role(root, audit_worktree, "auditor", f"Audit the complete implementation at exact integration commit {state['integrationSha']}. Return the complete bounded finding set in one response. Do not edit the worktree.", "audit-result.schema.json", "read-only")
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
                    raise BraceError("Integration changed while the deep audit was running.")
                if audit_result["bugs"]:
                    assert_graph(audit_result["bugs"], "bug")
                persisted = [persisted_bug(bug) for bug in audit_result["bugs"]]
                bug_hash = definition_hash(persisted, "bug")
                bugs = {"schemaVersion": "1.2", "revision": bugs["revision"] + 1, "auditSha": state["integrationSha"], "definitionHash": bug_hash, "status": "ready", "bugs": persisted}
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
            for bug in (item for item in bugs["bugs"] if item["status"] in {"ready_to_publish", "result_ready"} and item.get("resultSha")):
                existing = get_pull_request(root, config, bug["branch"], config["integrationBranch"], bug["resultSha"])
                if existing:
                    merged = complete_pull_request(root, config, existing)
                    bug.update(pullRequest=merged, status="verified", lastError=None)
                    state["integrationSha"] = merged["mergeSha"]
                    save_ledger(bugs, paths)
                    save_state(state, paths)
                    try:
                        remove_merged_assignment(root, config, bug["bugId"], bug["branch"], merged)
                    except Exception as error:
                        warnings.warn(str(error))
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
                        verification = invoke_role(root, bug["worktree"], "verifier", f"Verify only this {context_label}:\n{pretty_json(bug)}\n{pretty_json(result)}", "verifier-result.schema.json", "read-only")
                        if not verification["approved"]:
                            blocker = structured_blocker(verification["blocker"], "audit", bug["bugId"])
                            if is_semantic_blocker(blocker):
                                return _handle_semantic(root, config, state, paths, tasks, bugs, "verification", bug["bugId"], blocker, input_reader)
                            raise BraceError(f"{context_label.capitalize()} was rejected: " + "; ".join(verification["findings"]))
                        _checks(root, config, state, tasks, bugs)
                        if result["status"] == "not_reproducible":
                            bug.update(disposition="not_reproducible", status="verified", lastError=None)
                            remove_worktree(root, config, bug["bugId"], bug["branch"])
                        else:
                            bug.update(disposition="fixed", status="ready_to_publish")
                            save_ledger(bugs, paths)
                    except Exception as error:
                        if state.get("activeAmendment"):
                            raise
                        bug["status"] = "ready_to_publish" if bug.get("disposition") == "fixed" and bug.get("resultSha") else "open"
                        bug["lastError"] = str(error)
                save_ledger(bugs, paths)

                for bug in (item for item in bugs["bugs"] if item["status"] == "ready_to_publish"):
                    _checks(root, config, state, tasks, bugs)
                    state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
                    merged = publish_assignment(root, bug["worktree"], config, bug, "bug")
                    bug.update(pullRequest=merged, status="verified", lastError=None)
                    state["integrationSha"] = merged["mergeSha"]
                    save_ledger(bugs, paths)
                    save_state(state, paths)
                    try:
                        remove_merged_assignment(root, config, bug["bugId"], bug["branch"], merged)
                    except Exception as error:
                        warnings.warn(str(error))
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
                    bug.update(status="active", lastError=None)
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
            bugs["status"] = "complete"
            save_ledger(bugs, paths)
            audit_worktree = new_audit_worktree(root, config, f"{config['remote']}/{config['integrationBranch']}")
            final_validation = invoke_role(root, audit_worktree, "verifier", f"Run final project validation at exact integration SHA {state['integrationSha']}. Execute the project-wide commands from plan.md.", "verifier-result.schema.json", "read-only")
            if not final_validation["approved"]:
                blocker = structured_blocker(final_validation["blocker"], "audit", None)
                if is_semantic_blocker(blocker):
                    resume = _handle_semantic(root, config, state, paths, tasks, bugs, "verification", None, blocker, input_reader)
                    remove_audit_worktree(root, config)
                    audit_worktree = None
                    return resume
                raise BraceError("Final validation failed: " + "; ".join(final_validation["findings"]))
            remove_audit_worktree(root, config)
            audit_worktree = None
            _checks(root, config, state, tasks, bugs)
            head_sha = ensure_integration_branch(root, config, state, known_merges(tasks, bugs))
            if head_sha != state["integrationSha"]:
                raise BraceError("Integration changed during final validation.")
            project_pr = new_pull_request(root, config, config["integrationBranch"], config["targetBranch"], head_sha, state["targetBaseSha"], "Complete project implementation", f"Completed Brace project and verified {len(bugs['bugs'])} audit findings at {head_sha}.")
            project_pr = complete_pull_request(root, config, project_pr)
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
                "finalValidation": final_validation, "projectPullRequest": project_pr, "finalMergeSha": final_sha, "remainingLimitations": [],
            })
            show_status(state, tasks, bugs)
            print("PROJECT COMPLETE: audit, bug fixes, validation, merge, and cleanup succeeded.")
            return "complete"
        except Exception as error:
            if audit_worktree is not None:
                try:
                    remove_audit_worktree(root, config)
                except Exception as cleanup_error:
                    warnings.warn(f"Unable to remove audit worktree while handling an error: {cleanup_error}", stacklevel=2)
            if state is not None:
                if bugs is not None:
                    try:
                        save_ledger(bugs, paths)
                    except Exception as save_error:
                        warnings.warn(f"Unable to preserve bug ledger while handling an error: {save_error}", stacklevel=2)
                set_blocked(state, paths, "audit", None, str(error), "Resolve the exact bug, provider, validation, drift, or environment blocker, then rerun audit_loop.py.")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and repair a built Brace project.")
    parser.add_argument("repository", nargs="?", default=".")
    args = parser.parse_args()
    next_stage = run(args.repository)
    while next_stage == "build":
        from build_loop import run as run_build
        next_stage = run_build(args.repository)
        if next_stage == "audit":
            next_stage = run(args.repository)


if __name__ == "__main__":
    main()
