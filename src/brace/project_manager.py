from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .common import (
    Paths,
    BraceError,
    assert_graph,
    assert_task_coverage,
    attempt_path,
    canonicalize_graph_identities,
    complete_pull_request,
    definition_hash,
    ensure_integration_branch,
    get_pull_request,
    git_blob_identity,
    invoke_role,
    new_worktree,
    new_pull_request,
    normalize_task_references,
    object_hash,
    pretty_json,
    read_attempt_result,
    read_git_text,
    read_json,
    remove_worktree,
    run_native,
    safe_relative_pattern,
    save_state,
    utc_now,
    write_immutable_json,
    write_json_atomic,
)
from .ui import ask, info

InputReader = Callable[[dict[str, Any], str, dict[str, Any] | None], str]


def structured_blocker(blocker: Any, scope: str, identity: str | None) -> dict[str, Any] | None:
    if blocker is None:
        return None
    affected = identity if re.fullmatch(r"(?:TASK|BUG)-\d{4}", identity or "") else None
    if isinstance(blocker, str):
        return {
            "kind": "operational", "message": blocker, "evidence": blocker,
            "affectedIdentity": affected, "requiresUserDecision": False, "scopeChangePossible": False,
            "smallestResolution": "Correct the reported operational failure and retry the bounded assignment.",
            "prohibitedDecisions": ["Do not change project requirements or scope to bypass an operational failure."],
        }
    required = ("kind", "message", "evidence", "affectedIdentity", "requiresUserDecision", "scopeChangePossible", "smallestResolution", "prohibitedDecisions")
    for name in required:
        if name not in blocker:
            raise BraceError(f"Structured blocker is missing {name}.")
    if blocker["kind"] not in {"operational", "missing_information", "contract_conflict", "scope_gap", "task_decomposition", "bug_disposition"}:
        raise BraceError(f"Unsupported blocker kind: {blocker['kind']}")
    if not str(blocker["evidence"]).strip():
        raise BraceError("Structured blocker evidence is empty.")
    if not str(blocker["smallestResolution"]).strip():
        raise BraceError("Structured blocker smallestResolution is empty.")
    if affected and blocker["affectedIdentity"] != affected:
        raise BraceError(f"Structured blocker identity does not match {affected}.")
    if not affected and blocker["affectedIdentity"]:
        raise BraceError("A workflow-level blocker cannot claim an unrelated task or bug identity.")
    return blocker


def is_semantic_blocker(blocker: dict[str, Any] | None) -> bool:
    return bool(blocker and blocker["kind"] != "operational" and blocker["requiresUserDecision"])


def persisted_task(definition: dict[str, Any], amendment_id: str | None = None) -> dict[str, Any]:
    return {
        "taskId": definition["taskId"], "title": definition["title"], "description": definition["description"], "status": "pending",
        "requirementIds": list(definition["requirementIds"]), "planSections": list(definition["planSections"]),
        "dependencies": list(definition["dependencies"]), "allowedPaths": list(definition["allowedPaths"]),
        "exclusiveResources": list(definition["exclusiveResources"]), "acceptanceCriteria": list(definition["acceptanceCriteria"]),
        "checks": list(definition["checks"]), "attemptCount": 0, "branch": None, "worktree": None,
        "baseSha": None, "resultSha": None, "pullRequest": None, "lastError": None,
        "amendmentId": amendment_id, "supersededBy": [],
    }


def assert_pm_analysis(analysis: dict[str, Any], tasks: dict[str, Any], bugs: dict[str, Any] | None, source_stage: str) -> None:
    if sum(bool(option["recommended"]) for option in analysis["options"]) != 1:
        raise BraceError("PM analysis must identify exactly one recommended option.")
    task_ids = {task["taskId"] for task in tasks["tasks"]}
    bug_ids = {bug["bugId"] for bug in (bugs or {}).get("bugs", [])}
    for task_id in analysis["affectedTaskIds"]:
        if task_id not in task_ids:
            raise BraceError(f"PM analysis references unknown task {task_id}.")
    for bug_id in analysis["affectedBugIds"]:
        if bug_id not in bug_ids:
            raise BraceError(f"PM analysis references unknown bug {bug_id}.")
    for option in analysis["options"]:
        if option["requiresInput"] and not str(option.get("inputPrompt") or "").strip():
            raise BraceError(f"PM option {option['optionId']} requires input but has no input prompt.")
        if option["action"] == "disposition":
            if source_stage != "audit" or not option["bugDispositions"]:
                raise BraceError("Bug disposition options are valid only during audit and must contain a disposition.")
            if option["authorizedDocumentationPaths"]:
                raise BraceError("A disposition-only option cannot authorize documentation changes.")
            for change in option["bugDispositions"]:
                if change["bugId"] not in analysis["affectedBugIds"]:
                    raise BraceError(f"Disposition for {change['bugId']} is not in the affected bug set.")
        elif option["bugDispositions"]:
            raise BraceError(f"PM option {option['optionId']} includes bug dispositions but is not a disposition action.")


def authorized_documentation_paths(option: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for value in ["requirements.md", "plan.md", *option["authorizedDocumentationPaths"]]:
        normalized = str(value).replace("\\", "/").strip()
        if normalized in result:
            continue
        if not safe_relative_pattern(normalized) or re.search(r"(^|/)\.codex(/|$)", normalized) or not normalized.lower().endswith(".md"):
            raise BraceError(f"PM documentation path is not a safe Markdown path: {value}")
        result.append(normalized)
    return result


def decision_identity(amendment_id: str, option_id: str, response: str, question: str) -> str:
    return object_hash({"amendmentId": amendment_id, "optionId": option_id, "response": response, "question": question})


def assert_amendment_commit(worktree: str | Path, base_sha: str, authorized_paths: list[str]) -> dict[str, Any]:
    if run_native("git", ["-C", worktree, "status", "--porcelain", "--untracked-files=all"]).output.strip():
        raise BraceError("PM amendment worktree is not clean.")
    head = run_native("git", ["-C", worktree, "rev-parse", "HEAD"]).output.strip()
    if head == base_sha or run_native("git", ["-C", worktree, "merge-base", "--is-ancestor", base_sha, head], allowed_exit_codes=(0, 1)).returncode != 0:
        raise BraceError("PM did not create a descendant amendment commit.")
    count = int(run_native("git", ["-C", worktree, "rev-list", "--count", f"{base_sha}..{head}"]).output.strip())
    if count != 1:
        raise BraceError(f"PM amendment must contain exactly one commit; found {count}.")
    changed = [line for line in run_native("git", ["-C", worktree, "diff", "--name-only", f"{base_sha}..{head}"]).lines if line]
    for path in changed:
        if path not in authorized_paths:
            raise BraceError(f"PM modified an unauthorized path: {path}")
    if not changed:
        raise BraceError("PM amendment commit changed no approved documentation.")
    return {"Head": head, "ChangedFiles": changed}


def assert_pm_result_identity(result: dict[str, Any], amendment: dict[str, Any], commit: dict[str, Any]) -> None:
    if result["decisionIdentity"] != amendment["decisionIdentity"] or result["selectedOptionId"] != amendment["selectedOptionId"]:
        raise BraceError("PM result does not match the approved user decision.")
    if result["commitSha"] != commit["Head"]:
        raise BraceError("PM result commit SHA does not match amendment worktree HEAD.")
    if sorted(result["changedFiles"]) != sorted(commit["ChangedFiles"]):
        raise BraceError("PM result changedFiles does not match the amendment commit.")


def save_pm_task_ledger(tasks: dict[str, Any], paths: Paths) -> None:
    tasks["revision"] += 1
    write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")


def save_pm_bug_ledger(bugs: dict[str, Any], paths: Paths) -> None:
    bugs["revision"] += 1
    write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")


def complete_disposition_decision(root: str | Path, config: dict[str, Any], state: dict[str, Any], paths: Paths, tasks: dict[str, Any], bugs: dict[str, Any], amendment: dict[str, Any], option: dict[str, Any]) -> dict[str, Any]:
    changed = False
    by_id = {bug["bugId"]: bug for bug in bugs["bugs"]}
    for disposition in option["bugDispositions"]:
        bug = by_id.get(disposition["bugId"])
        if not bug:
            raise BraceError(f"Disposition references unknown bug {disposition['bugId']}.")
        if bug["status"] == "verified":
            if bug["disposition"] != disposition["disposition"] or bug["amendmentId"] != amendment["amendmentId"] or bug["dispositionEvidence"] != disposition["evidence"]:
                raise BraceError(f"Bug {bug['bugId']} was verified by a different decision.")
            continue
        bug.update(status="verified", disposition=disposition["disposition"], amendmentId=amendment["amendmentId"], lastError=None, dispositionEvidence=disposition["evidence"])
        changed = True
    if changed:
        bugs["definitionHash"] = definition_hash(bugs["bugs"], "bug")
        state["bugDefinitionHash"] = bugs["definitionHash"]
        save_pm_bug_ledger(bugs, paths)
    if amendment["status"] != "applied":
        amendment["status"] = "applied"
        state.update(stage="audit", stageStatus="running")
        save_state(state, paths)
    remove_worktree(root, config, amendment["amendmentId"], amendment["branch"])
    state.update(activeAmendment=None, blocker=None)
    save_state(state, paths)
    return {"action": "disposition", "resumeStage": "audit", "amendmentId": amendment["amendmentId"], "sourceSuperseded": True}


def _select_option(analysis: dict[str, Any], amendment: dict[str, Any], input_reader: InputReader | None) -> dict[str, Any]:
    info(f"\nPM DECISION REQUIRED: {amendment['amendmentId']}\n{analysis['summary']}", "warning")
    info(f"Recommendation: {analysis['recommendation']}")
    effects = analysis["effects"]
    info("Effects: " + "; ".join(f"{key}={effects[key]}" for key in ("requirements", "plan", "tasks", "bugs", "completedWork", "schedule")))
    for option in analysis["options"]:
        info(f"  {option['optionId']}: {option['label']}{' [recommended]' if option['recommended'] else ''}\n    {option['description']}")
    info(analysis["question"], "brace")
    selection = (input_reader(analysis, "option", None) if input_reader else ask("Select an option ID")).strip().upper()
    matches = [option for option in analysis["options"] if option["optionId"] == selection]
    if len(matches) != 1:
        raise BraceError(f"Unknown PM option: {selection}")
    option = matches[0]
    response = selection
    if option["requiresInput"]:
        response = (input_reader(analysis, "value", option) if input_reader else ask(option["inputPrompt"])).strip()
        if not response:
            raise BraceError(f"PM option {selection} requires a nonempty response.")
        if len(response) > 4096:
            raise BraceError("PM response exceeds 4096 characters.")
    amendment.update(selectedOptionId=selection, userResponse=response)
    amendment["decisionIdentity"] = decision_identity(amendment["amendmentId"], selection, response, analysis["question"])
    return option


def invoke_pm_resolution(
    root: str | Path, config: dict[str, Any], state: dict[str, Any], paths: Paths,
    tasks: dict[str, Any], bugs: dict[str, Any] | None, source_stage: str,
    source_kind: str, source_identity: str | None, blocker: Any,
    input_reader: InputReader | None = None,
) -> dict[str, Any]:
    if not state.get("activeAmendment"):
        value = structured_blocker(blocker, source_stage, source_identity)
        if not is_semantic_blocker(value):
            raise BraceError(f"Operational blocker requires correction, not a PM decision: {value['message']}")
        sequence = int(state["amendmentSequence"]) + 1
        if sequence > config["maximumAmendmentRounds"]:
            raise BraceError(f"Semantic amendment limit exhausted at {sequence} attempts.")
        identity = f"AMEND-{sequence:04d}"
        state["amendmentSequence"] = sequence
        state["activeAmendment"] = {
            "amendmentId": identity, "sourceStage": source_stage, "sourceKind": source_kind,
            "sourceIdentity": source_identity, "status": "analyzing", "blocker": value,
            "analysisResultPath": None, "selectedOptionId": None, "userResponse": None,
            "decisionIdentity": None, "authorizedDocumentationPaths": [], "branch": f"worktree/{identity}",
            "worktree": None, "baseSha": None, "resultSha": None, "pullRequest": None,
            "affectedTaskIds": [], "affectedBugIds": [], "resumeStage": source_stage, "attemptCount": 0,
        }
        state.update(stage=source_stage, stageStatus="amending")
        save_state(state, paths)

    amendment = state["activeAmendment"]
    identity = amendment["amendmentId"]
    analysis_path = paths.results / f"{identity}-analysis.json"
    if amendment["status"] == "analyzing":
        entries = tasks["tasks"] + ([] if bugs is None else bugs["bugs"])
        known = [entry["pullRequest"]["mergeSha"] for entry in entries if (entry.get("pullRequest") or {}).get("mergeSha")]
        state["integrationSha"] = ensure_integration_branch(root, config, state, known)
        amendment["baseSha"] = state["integrationSha"]
        amendment["worktree"] = str(new_worktree(root, config, identity, amendment["branch"], amendment["baseSha"]))
        if analysis_path.exists():
            analysis = read_json(analysis_path, paths.schemas / "pm-blocker-result.schema.json")
        else:
            context = "\n".join((
                "Mode: analyze", f"Amendment: {identity}", f"Source stage: {source_stage}",
                f"Source identity: {source_identity or ''}", f"Exact integration SHA: {state['integrationSha']}",
                f"Structured blocker:\n{pretty_json(amendment['blocker'])}", f"Task ledger:\n{pretty_json(tasks)}",
                f"Bug ledger:\n{pretty_json(bugs) if bugs is not None else 'not available'}",
            ))
            analysis = invoke_role(root, amendment["worktree"], "project-manager", context, "pm-blocker-result.schema.json", "read-only")
            write_immutable_json(analysis_path, analysis)
        assert_pm_analysis(analysis, tasks, bugs, source_stage)
        amendment.update(analysisResultPath=str(analysis_path), affectedTaskIds=analysis["affectedTaskIds"], affectedBugIds=analysis["affectedBugIds"], status="awaiting_user")
        state["stageStatus"] = "awaiting_user"
        save_state(state, paths)

    analysis = read_json(amendment["analysisResultPath"], paths.schemas / "pm-blocker-result.schema.json")
    assert_pm_analysis(analysis, tasks, bugs, source_stage)
    options = {option["optionId"]: option for option in analysis["options"]}
    if amendment["status"] == "applied" and amendment.get("selectedOptionId") in options and options[amendment["selectedOptionId"]]["action"] == "disposition":
        return complete_disposition_decision(root, config, state, paths, tasks, bugs, amendment, options[amendment["selectedOptionId"]])

    if amendment["status"] == "awaiting_user":
        option = options.get(amendment.get("selectedOptionId")) or _select_option(analysis, amendment, input_reader)
        save_state(state, paths)
        if option["action"] == "stop":
            raise BraceError(f"User selected stop for {identity}.")
        if option["action"] == "retry":
            remove_worktree(root, config, identity, amendment["branch"])
            state.update(activeAmendment=None, blocker=None, stage=source_stage, stageStatus="running")
            save_state(state, paths)
            return {"action": "retry", "resumeStage": source_stage, "amendmentId": identity, "sourceSuperseded": False}
        if option["action"] == "disposition":
            if bugs is None:
                raise BraceError("A disposition requires a bug ledger.")
            return complete_disposition_decision(root, config, state, paths, tasks, bugs, amendment, option)
        if not analysis["amendmentRequired"]:
            raise BraceError("The selected amendment conflicts with PM analysis stating that no amendment is required.")
        amendment.update(authorizedDocumentationPaths=authorized_documentation_paths(option), status="approved")
        state["stageStatus"] = "amending"
        save_state(state, paths)

    result_path = attempt_path(paths, "result", identity, 1)
    if amendment["status"] in {"approved", "agent_active"}:
        amendment["worktree"] = str(new_worktree(root, config, identity, amendment["branch"], amendment["baseSha"], amendment.get("resultSha")))
        amendment.update(attemptCount=1, status="agent_active")
        save_state(state, paths)
        assignment_path = attempt_path(paths, "assignment", identity, 1)
        if not assignment_path.exists():
            write_immutable_json(assignment_path, {
                "schemaVersion": "1.0", "identity": identity, "attempt": 1, "baseSha": amendment["baseSha"],
                "selectedOptionId": amendment["selectedOptionId"], "userResponse": amendment["userResponse"],
                "decisionIdentity": amendment["decisionIdentity"], "authorizedDocumentationPaths": amendment["authorizedDocumentationPaths"],
                "blocker": amendment["blocker"], "createdAt": utc_now(),
            })
        if result_path.exists():
            record = read_json(result_path)
        else:
            head = run_native("git", ["-C", amendment["worktree"], "rev-parse", "HEAD"]).output.strip()
            mode = "recover_result" if head != amendment["baseSha"] else "amend"
            first_task_id = max((int(task["taskId"][5:]) for task in tasks["tasks"]), default=0) + 1
            context = "\n".join((
                f"Mode: {mode}", f"Amendment: {identity}", f"Approved option: {amendment['selectedOptionId']}",
                f"User response: {amendment['userResponse']}", f"Decision identity: {amendment['decisionIdentity']}",
                f"Authorized documentation paths: {', '.join(amendment['authorizedDocumentationPaths'])}",
                f"Base SHA: {amendment['baseSha']}", f"First available follow-up task ID: TASK-{first_task_id:04d}",
                f"Analysis:\n{pretty_json(analysis)}", f"Current tasks:\n{pretty_json(tasks)}",
            ))
            result = invoke_role(root, amendment["worktree"], "project-manager", context, "pm-amendment-result.schema.json", "read-only" if mode == "recover_result" else "workspace-write")
            if result["status"] != "completed":
                raise BraceError(f"PM amendment blocked: {result.get('blocker')}")
            record = {"schemaVersion": "1.0", "identity": identity, "attempt": 1, "succeeded": True, "result": result, "error": None, "completedAt": utc_now()}
            write_immutable_json(result_path, record)
        amendment["status"] = "result_ready"
        save_state(state, paths)

    record = read_json(result_path)
    result = record["result"]
    superseded = set(result["supersededTaskIds"])
    prior_tasks = [task for task in tasks["tasks"] if task.get("amendmentId") != identity or task["taskId"] in superseded]
    expected = max((int(task["taskId"][5:]) for task in prior_tasks), default=0) + 1
    mapping = canonicalize_graph_identities(result["newTasks"], "task", expected, (task["taskId"] for task in prior_tasks))
    if amendment["status"] == "result_ready":
        commit = assert_amendment_commit(amendment["worktree"], amendment["baseSha"], amendment["authorizedDocumentationPaths"])
        assert_pm_result_identity(result, amendment, commit)
        amendment["resultSha"] = commit["Head"]
        if result["supersededTaskIds"] and not result["newTasks"]:
            raise BraceError("A superseded task requires at least one replacement follow-up task.")
        for task_id in result["supersededTaskIds"]:
            matches = [task for task in tasks["tasks"] if task["taskId"] == task_id]
            if len(matches) != 1:
                raise BraceError(f"PM superseded an unknown task: {task_id}")
            task = matches[0]
            if task["status"] in {"integrated", "active", "submitted"}:
                raise BraceError(f"PM cannot supersede task {task_id} because it is active, integrated, or submitted.")
            if task.get("resultSha"):
                durable = read_attempt_result(paths, task_id, task["attemptCount"])
                if not durable or not durable["succeeded"] or task["status"] not in {"result_ready", "verified_ready"}:
                    raise BraceError(f"PM cannot supersede task {task_id} because its unintegrated result is not durably preserved.")
            if task.get("worktree") and Path(task["worktree"]).is_dir():
                dirty = run_native("git", ["-C", task["worktree"], "status", "--porcelain", "--untracked-files=all"]).output.strip()
                head = run_native("git", ["-C", task["worktree"], "rev-parse", "HEAD"]).output.strip()
                if dirty or (head != task["baseSha"] and head != task.get("resultSha")):
                    raise BraceError(f"PM cannot supersede task {task_id} because its worktree contains unrecorded work.")
        candidate = tasks["tasks"] + [persisted_task(task, identity) for task in result["newTasks"]]
        assert_graph(candidate, "task")
        plan = read_git_text(amendment["worktree"], commit["Head"], "plan.md")
        normalize_task_references(plan, mapping, (task["taskId"] for task in candidate))
        for task in candidate:
            if task["taskId"] not in superseded and superseded.intersection(task["dependencies"]):
                raise BraceError(f"{task['taskId']} depends on a superseded task.")
        requirements = read_git_text(amendment["worktree"], commit["Head"], "requirements.md")
        assert_task_coverage([task for task in candidate if task["taskId"] not in superseded], requirements)
        verification = invoke_role(root, amendment["worktree"], "verifier", "Verify this approved project amendment against the exact user decision.\n" + pretty_json(result), "verifier-result.schema.json", "read-only")
        if not verification["approved"]:
            raise BraceError("PM amendment semantic verification failed: " + "; ".join(verification["findings"]))
        run_native("git", ["-C", amendment["worktree"], "push", "--set-upstream", config["remote"], amendment["branch"]])
        run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
        base = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['integrationBranch']}"]).output.strip()
        amendment["pullRequest"] = new_pull_request(root, config, amendment["branch"], config["integrationBranch"], amendment["resultSha"], base, f"{identity} project contract amendment", result["summary"])
        amendment["status"] = "submitted"
        save_state(state, paths)

    if amendment["status"] == "submitted":
        pr = get_pull_request(root, config, amendment["branch"], config["integrationBranch"], amendment["resultSha"], amendment["pullRequest"]["id"])
        if pr is None:
            raise BraceError("Unable to reconcile the amendment pull request.")
        merged = complete_pull_request(root, config, pr)
        amendment.update(pullRequest=merged, status="integrated")
        state["integrationSha"] = merged["mergeSha"]
        state["acceptedIntegrationShas"] = list(dict.fromkeys([*state["acceptedIntegrationShas"], merged["mergeSha"]]))
        save_state(state, paths)

    if amendment["status"] == "integrated":
        new_ids = [task["taskId"] for task in result["newTasks"]]
        previous_tasks = object_hash(tasks)
        for task in tasks["tasks"]:
            if task["taskId"] in result["supersededTaskIds"]:
                task.update(status="superseded", amendmentId=identity, supersededBy=new_ids)
        existing = {task["taskId"]: task for task in tasks["tasks"]}
        for definition in result["newTasks"]:
            follow_up = persisted_task(definition, identity)
            current = existing.get(follow_up["taskId"])
            if current is None:
                tasks["tasks"].append(follow_up)
            elif current.get("amendmentId") != identity or definition_hash([current], "task") != definition_hash([follow_up], "task"):
                raise BraceError(f"Persisted follow-up task conflicts with {identity}: {follow_up['taskId']}")
        state["requirementsHash"] = git_blob_identity(root, state["integrationSha"], "requirements.md")
        state["planHash"] = git_blob_identity(root, state["integrationSha"], "plan.md")
        tasks.update(planHash=state["planHash"], definitionHash=definition_hash(tasks["tasks"], "task"), status="active")
        state["taskDefinitionHash"] = tasks["definitionHash"]
        if object_hash(tasks) != previous_tasks:
            save_pm_task_ledger(tasks, paths)
        resume = "build" if source_stage == "build" or result["newTasks"] or result["supersededTaskIds"] else result["resumeStage"]
        if source_stage == "audit" and bugs is not None:
            history = paths.results / f"{identity}-pre-expansion-audit.json"
            bugs_changed = bugs["auditSha"] is not None or bugs["definitionHash"] is not None or bugs["status"] != "not_audited" or bool(bugs["bugs"])
            if bugs_changed:
                if not history.exists():
                    write_immutable_json(history, bugs)
                bugs.update(schemaVersion="1.2", revision=bugs["revision"] + 1, auditSha=None, definitionHash=None, status="not_audited", bugs=[])
                write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")
            state["bugDefinitionHash"] = None
        amendment.update(resumeStage=resume, status="applied")
        state.update(stage=resume, stageStatus="amending")
        save_state(state, paths)

    if amendment["status"] == "applied":
        from .common import remove_merged_assignment

        remove_merged_assignment(root, config, identity, amendment["branch"], amendment["pullRequest"])
        source_superseded = source_identity in result["supersededTaskIds"]
        resume = amendment["resumeStage"]
        state.update(activeAmendment=None, blocker=None, stage=resume, stageStatus="running")
        save_state(state, paths)
        return {"action": "amended", "resumeStage": resume, "amendmentId": identity, "sourceSuperseded": source_superseded}
    raise BraceError(f"Unsupported amendment state: {amendment['status']}")
