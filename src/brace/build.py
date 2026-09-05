from __future__ import annotations

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
    ensure_integration_branch,
    get_configuration,
    get_pull_request,
    initialize_state_files,
    invoke_role,
    new_audit_worktree,
    new_worktree,
    pretty_json,
    publish_assignment,
    read_attempt_result,
    read_json,
    recover_committed_attempt,
    remove_audit_worktree,
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
    write_immutable_json,
    write_json_atomic,
    write_summary,
)
from .project_manager import (
    invoke_pm_resolution,
    is_semantic_blocker,
    structured_blocker,
)
from .ui import info, status, success, warning

InputReader = Callable[[dict[str, Any], str, dict[str, Any] | None], str]


def save_ledger(ledger: dict[str, Any], paths: Any) -> None:
    ledger["revision"] += 1
    write_json_atomic(paths.tasks, ledger, paths.schemas / "tasks.schema.json")


def known_merges(tasks: dict[str, Any]) -> list[str]:
    return [task["pullRequest"]["mergeSha"] for task in tasks["tasks"] if (task.get("pullRequest") or {}).get("mergeSha")]


def reset_after_amendment(task: dict[str, Any], root: Path, config: dict[str, Any]) -> None:
    worktree = task.get("worktree")
    if worktree and Path(worktree).is_dir():
        if run_native("git", ["-C", worktree, "status", "--porcelain", "--untracked-files=all"]).output.strip():
            raise BraceError(f"Preserving {task['taskId']}: its pre-amendment worktree is dirty.")
        head = run_native("git", ["-C", worktree, "rev-parse", "HEAD"]).output.strip()
        if task["status"] == "superseded":
            if head != task["baseSha"] and head != task.get("resultSha"):
                raise BraceError(f"Preserving {task['taskId']}: a superseded task has unrecorded work.")
            remove_worktree(root, config, task["taskId"], task["branch"])
            task.update(branch=None, worktree=None, baseSha=None, resultSha=None, pullRequest=None)
            return
        run_native("git", ["-C", worktree, "fetch", config["remote"], "--prune"])
        run_native("git", ["-C", worktree, "merge", "--no-edit", f"{config['remote']}/{config['integrationBranch']}"])
        task["baseSha"] = run_native("git", ["-C", worktree, "rev-parse", "HEAD"]).output.strip()
    else:
        task.update(branch=None, worktree=None, baseSha=None)
    task.update(status="pending", resultSha=None, pullRequest=None, lastError=None)


def _checks(root: Path, config: dict[str, Any], state: dict[str, Any], tasks: dict[str, Any]) -> None:
    assert_plan_drift(state, root, require_plan=True)
    assert_ledger_identity(state, tasks, "task")
    assert_target_drift(root, config, state)


def _semantic_resolution(root: Path, config: dict[str, Any], state: dict[str, Any], paths: Any, tasks: dict[str, Any], bugs: dict[str, Any], task: dict[str, Any] | None, source_kind: str, blocker: dict[str, Any], input_reader: InputReader | None) -> dict[str, Any]:
    return invoke_pm_resolution(root, config, state, paths, tasks, bugs, "build", source_kind, task["taskId"] if task else None, blocker, input_reader)


def run(repository: str | Path = ".", input_reader: InputReader | None = None) -> str:
    root = repository_root(repository)
    config = get_configuration(root)
    assert_prerequisites(config, require_codex=True)
    paths = initialize_state_files(root, config)
    state = tasks = bugs = None
    with WorkflowLock(paths.lock):
        try:
            state = read_json(paths.state, paths.schemas / "state.schema.json")
            tasks = read_json(paths.tasks, paths.schemas / "tasks.schema.json")
            bugs = read_json(paths.bugs, paths.schemas / "bugs.schema.json")
            assert_state_identity(state, root, config)
            if state.get("activeAmendment"):
                amendment = state["activeAmendment"]
                state["stage"] = amendment["sourceStage"]
                resolution = invoke_pm_resolution(root, config, state, paths, tasks, bugs, amendment["sourceStage"], amendment["sourceKind"], amendment["sourceIdentity"], amendment["blocker"], input_reader)
                if resolution["resumeStage"] == "audit":
                    show_status(state, tasks, bugs)
                    success("PM AMENDMENT COMPLETE: resume the audit loop.")
                    return "audit"

            _checks(root, config, state, tasks)
            assert_graph(tasks["tasks"], "task")
            if tasks["status"] == "complete" and state["stage"] == "audit":
                show_status(state, tasks)
                success("BUILD COMPLETE: the audit loop may run.")
                return "audit"
            if tasks["status"] not in {"ready", "active", "blocked"}:
                raise BraceError("Planning has not produced a buildable task queue.")
            if state["stage"] not in {"build", "blocked"}:
                raise BraceError(f"The workflow is at stage {state['stage']}, not build.")
            state.update(stage="build", stageStatus="running", blocker=None)
            tasks["status"] = "active"
            save_state(state, paths)

            for task in (item for item in tasks["tasks"] if item["status"] == "active"):
                record = read_attempt_result(paths, task["taskId"], task["attemptCount"]) or recover_committed_attempt(root, paths, task, "task")
                if record and record["succeeded"]:
                    task["status"] = "result_ready"
                else:
                    task.update(status="pending", lastError="Interrupted before a durable result or commit was produced." if record is None else record["error"])

            for task in (item for item in tasks["tasks"] if item["status"] in {"result_ready", "verified_ready", "submitted"} and item.get("resultSha")):
                existing = get_pull_request(root, config, task["branch"], config["integrationBranch"], task["resultSha"])
                if existing:
                    from .common import complete_pull_request
                    merged = complete_pull_request(root, config, existing)
                    task.update(pullRequest=merged, status="integrated", lastError=None)
                    state["integrationSha"] = merged["mergeSha"]
                    save_ledger(tasks, paths)
                    save_state(state, paths)
                    try:
                        remove_merged_assignment(root, config, task["taskId"], task["branch"], merged)
                    except Exception as error:
                        warning(str(error))
            save_ledger(tasks, paths)
            state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks))
            save_state(state, paths)

            while any(task["status"] not in {"integrated", "superseded"} for task in tasks["tasks"]):
                _checks(root, config, state, tasks)
                state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks))
                amendment_handled = False

                for task in (item for item in tasks["tasks"] if item["status"] == "result_ready"):
                    record = read_attempt_result(paths, task["taskId"], task["attemptCount"])
                    if not record or not record["succeeded"]:
                        task.update(status="pending", lastError="Durable builder result is missing." if not record else record["error"])
                        continue
                    result = record["result"]
                    if result["status"] == "blocked":
                        blocker = structured_blocker(result["blocker"], "build", task["taskId"])
                        if is_semantic_blocker(blocker):
                            _semantic_resolution(root, config, state, paths, tasks, bugs, task, "task", blocker, input_reader)
                            reset_after_amendment(task, root, config)
                            save_ledger(tasks, paths)
                            amendment_handled = True
                            break
                        task.update(status="pending", lastError=blocker["message"])
                if amendment_handled:
                    continue
                save_ledger(tasks, paths)

                for task in (item for item in tasks["tasks"] if item["status"] == "result_ready"):
                    record = read_attempt_result(paths, task["taskId"], task["attemptCount"])
                    result = record["result"]
                    try:
                        _checks(root, config, state, tasks)
                        commit = assert_assignment_commit(task["worktree"], task["baseSha"], task)
                        if result["commitSha"] != commit["Head"]:
                            raise BraceError("Builder result commit SHA does not match the worktree HEAD.")
                        task["resultSha"] = commit["Head"]
                        save_ledger(tasks, paths)
                        info(f"VERIFYING TASK: {task['taskId']} attempt {task['attemptCount']}")
                        verification = invoke_role(root, task["worktree"], "verifier", f"Verify only this task:\n{pretty_json(task)}\nBuilder result:\n{pretty_json(result)}", "verifier-result.schema.json", "read-only")
                        if not verification["approved"]:
                            blocker = structured_blocker(verification["blocker"], "build", task["taskId"])
                            if is_semantic_blocker(blocker):
                                _semantic_resolution(root, config, state, paths, tasks, bugs, task, "verification", blocker, input_reader)
                                reset_after_amendment(task, root, config)
                                save_ledger(tasks, paths)
                                amendment_handled = True
                                break
                            task.update(status="pending", resultSha=None, lastError="Focused verification failed: " + "; ".join(verification["findings"]))
                            continue
                        task.update(status="verified_ready", lastError=None)
                        save_ledger(tasks, paths)
                    except Exception as error:
                        if state.get("activeAmendment"):
                            raise
                        task.update(status="pending", resultSha=None, lastError=str(error))
                        warning(f"{task['taskId']} attempt {task['attemptCount']} failed verification: {error}")
                if amendment_handled:
                    continue
                save_ledger(tasks, paths)

                for task in (item for item in tasks["tasks"] if item["status"] == "verified_ready"):
                    try:
                        _checks(root, config, state, tasks)
                        state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks))
                        info(f"PUBLISHING TASK PR: {task['taskId']} at {task['resultSha']}")
                        merged = publish_assignment(root, task["worktree"], config, task, "task")
                        success(f"TASK PR MERGED: {task['taskId']} -> {merged['mergeSha']}")
                        task.update(pullRequest=merged, status="integrated", lastError=None)
                        state["integrationSha"] = merged["mergeSha"]
                        save_ledger(tasks, paths)
                        save_state(state, paths)
                        try:
                            remove_merged_assignment(root, config, task["taskId"], task["branch"], merged)
                        except Exception as error:
                            warning(str(error))
                    except Exception as error:
                        task.update(status="verified_ready", lastError=str(error))
                        warning(f"{task['taskId']} publish failed: {error}")
                save_ledger(tasks, paths)
                if not any(task["status"] not in {"integrated", "superseded"} for task in tasks["tasks"]):
                    break

                exhausted = [task for task in tasks["tasks"] if task["status"] not in {"integrated", "superseded"} and task["attemptCount"] >= config["maximumTaskAttempts"]]
                if exhausted:
                    for task in exhausted:
                        task["status"] = "blocked"
                    tasks["status"] = "blocked"
                    save_ledger(tasks, paths)
                    raise BraceError("Task attempts exhausted: " + ", ".join(task["taskId"] for task in exhausted))
                wave = select_ready_items(tasks["tasks"], "task", config["maximumConcurrentBuilders"])
                if not wave:
                    raise BraceError("No dependency-ready, non-conflicting tasks remain. Inspect blocked dependencies and path ownership.")
                base_sha = state["integrationSha"]
                for task in wave:
                    task["branch"] = task.get("branch") or f"worktree/{task['taskId']}"
                    task["baseSha"] = task.get("baseSha") or base_sha
                    task["worktree"] = str(new_worktree(root, config, task["taskId"], task["branch"], task["baseSha"], task.get("resultSha")))
                    task["attemptCount"] += 1
                    task.update(status="active", lastError=None)
                    write_immutable_json(attempt_path(paths, "assignment", task["taskId"], task["attemptCount"]), {
                        "schemaVersion": "1.0", "identity": task["taskId"], "attempt": task["attemptCount"],
                        "baseSha": task["baseSha"], "startingHead": run_native("git", ["-C", task["worktree"], "rev-parse", "HEAD"]).output.strip(),
                        "createdAt": utc_now(), "item": task,
                    })
                save_ledger(tasks, paths)
                with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                    records = {task["taskId"]: pool.submit(run_assignment, root, task["worktree"], task, "task", paths) for task in wave}
                    for task in wave:
                        record = records[task["taskId"]].result()
                        if not record or not record["succeeded"]:
                            task.update(status="pending", lastError="Builder returned no durable result." if not record else record["error"])
                        else:
                            task["status"] = "result_ready"
                save_ledger(tasks, paths)
                show_status(state, tasks)

            info("BUILD: final drift check")
            _checks(root, config, state, tasks)
            state["integrationSha"] = ensure_integration_branch(root, config, state, known_merges(tasks))
            verification_worktree = new_audit_worktree(root, config, f"{config['remote']}/{config['integrationBranch']}")
            try:
                with status("Running integration verification"):
                    verification = invoke_role(root, verification_worktree, "verifier", f"Perform lightweight integration verification at exact SHA {state['integrationSha']}. Confirm the build and direct smoke checks only.", "verifier-result.schema.json", "read-only")
                if not verification["approved"]:
                    blocker = structured_blocker(verification["blocker"], "build", None)
                    if is_semantic_blocker(blocker):
                        _semantic_resolution(root, config, state, paths, tasks, bugs, None, "verification", blocker, input_reader)
                        return "build"
                    raise BraceError("Lightweight integration verification failed: " + "; ".join(verification["findings"]))
            finally:
                remove_audit_worktree(root, config)
            _checks(root, config, state, tasks)
            tasks["status"] = "complete"
            save_ledger(tasks, paths)
            state.update(stage="audit", stageStatus="not_started", blocker=None)
            save_state(state, paths)
            active = [task for task in tasks["tasks"] if task["status"] != "superseded"]
            attempts = [task["attemptCount"] for task in active]
            write_summary(paths.build_summary, {
                "completedAt": utc_now(), "totalTasks": len(active),
                "supersededTasks": sum(task["status"] == "superseded" for task in tasks["tasks"]),
                "integratedTasks": sum(task["status"] == "integrated" for task in active),
                "totalAttempts": sum(attempts), "averageAttempts": round(sum(attempts) / len(attempts), 2) if attempts else 0,
                "taskCommits": [task["resultSha"] for task in active if task.get("resultSha")],
                "pullRequests": [task["pullRequest"] for task in active if task.get("pullRequest")],
                "integrationSha": state["integrationSha"], "integrationVerification": verification, "remainingBlockers": [],
            })
            show_status(state, tasks)
            success("BUILD COMPLETE: all active tasks are integrated. The audit loop may run.")
            return "audit"
        except Exception as error:
            if state is not None:
                if tasks is not None:
                    try:
                        save_ledger(tasks, paths)
                    except Exception as save_error:
                        warning(f"Unable to preserve task ledger while handling an error: {save_error}")
                set_blocked(state, paths, "build", None, str(error), "Resolve the exact task, provider, drift, or environment blocker, then rerun brace build.")
            raise
