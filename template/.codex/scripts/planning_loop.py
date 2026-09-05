from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    RalphError,
    WorkflowLock,
    assert_graph,
    assert_ledger_identity,
    assert_plan_drift,
    assert_prerequisites,
    assert_state_identity,
    assert_target_drift,
    assert_task_coverage,
    definition_hash,
    get_configuration,
    git_blob_identity,
    initialize_state_files,
    invoke_role,
    read_json,
    read_text,
    repository_root,
    reset_completed_workflow,
    run_native,
    save_state,
    set_blocked,
    show_status,
    utc_now,
    write_json_atomic,
    write_summary,
    write_text_atomic,
)
from project_manager import persisted_task


def run(repository: str | Path = ".", start_new_workflow: bool = False) -> None:
    root = repository_root(repository)
    config = get_configuration(root)
    assert_prerequisites(config, require_codex=True)
    paths = initialize_state_files(root, config)
    state = None
    with WorkflowLock(paths.lock):
        try:
            state = read_json(paths.state, paths.schemas / "state.schema.json")
            assert_state_identity(state, root, config)
            if start_new_workflow:
                reset_completed_workflow(root, config, state)
                paths = initialize_state_files(root, config)
                state = read_json(paths.state, paths.schemas / "state.schema.json")
            tasks = read_json(paths.tasks, paths.schemas / "tasks.schema.json")
            if tasks["status"] in {"active", "complete"} or state["stage"] in {"audit", "complete"} or any(task["attemptCount"] > 0 for task in tasks["tasks"]):
                raise RalphError("Planning cannot replace a task queue after implementation has begun.")
            if tasks["status"] == "ready" and state["stage"] == "build":
                assert_plan_drift(state, root, require_plan=True)
                assert_ledger_identity(state, tasks, "task")
                assert_target_drift(root, config, state)
                show_status(state, tasks)
                print("Planning is already complete. The build loop may run.")
                return

            requirements_path = root / "requirements.md"
            if not requirements_path.exists():
                raise RalphError("requirements.md does not exist.")
            run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
            current_branch = run_native("git", ["-C", root, "branch", "--show-current"]).output.strip()
            if current_branch != config["targetBranch"]:
                raise RalphError(f"Planning must run from the target branch {config['targetBranch']}, not {current_branch}.")
            local_sha = run_native("git", ["-C", root, "rev-parse", "HEAD"]).output.strip()
            remote_sha = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['targetBranch']}"]).output.strip()
            if local_sha != remote_sha:
                raise RalphError("Local target branch must exactly match its remote before planning.")
            pending = [line[3:].replace("\\", "/") for line in run_native("git", ["-C", root, "status", "--porcelain", "--untracked-files=all"]).lines if len(line) > 3]
            unexpected = [path for path in pending if path not in {"requirements.md", "plan.md"}]
            if unexpected:
                raise RalphError("Planning found unrelated uncommitted work: " + ", ".join(unexpected))

            state.update(stage="planning", stageStatus="running", blocker=None)
            save_state(state, paths)
            completed = None
            for round_number in range(1, config["maximumPlanningQuestionRounds"] + 1):
                context = (
                    f"Repository root: {root}\nQuestion round: {round_number} of {config['maximumPlanningQuestionRounds']}\n\n"
                    "Inspect requirements.md and the existing repository directly. If confirmed clarifications are present, "
                    "treat them as authoritative. If the requirements are sufficient, return the normalized requirements, "
                    "complete plan, and complete task graph now."
                )
                result = invoke_role(root, root, "planner", context, "planning-result.schema.json", "read-only")
                if result["status"] == "questions":
                    if not result["questions"]:
                        raise RalphError("Planner requested clarification without returning questions.")
                    answers = []
                    for question in result["questions"]:
                        print(f"\nPLANNING QUESTION {question['questionId']}\n{question['question']}\nWhy this is needed: {question['reason']}")
                        answer = input("Answer: ").strip()
                        if not answer:
                            raise RalphError(f"No answer was supplied for {question['questionId']}.")
                        answers.append(f"### {question['questionId']}\n\nQuestion: {question['question']}\n\nAnswer: {answer}")
                    write_text_atomic(requirements_path, read_text(requirements_path).rstrip() + "\n\n" + "\n\n".join(answers) + "\n")
                    continue
                if not result["normalizedRequirementsMarkdown"].strip():
                    raise RalphError("Planner completed without normalized requirements.")
                if not result["planMarkdown"].strip():
                    raise RalphError("Planner completed without plan.md content.")
                if not result["tasks"]:
                    raise RalphError("Planner completed without implementation tasks.")
                assert_graph(result["tasks"], "task")
                assert_task_coverage(result["tasks"], result["normalizedRequirementsMarkdown"], result["summary"]["deferredRequirementIds"])
                completed = result
                break
            if completed is None:
                raise RalphError(f"Planning did not complete within {config['maximumPlanningQuestionRounds']} clarification rounds.")

            write_text_atomic(requirements_path, completed["normalizedRequirementsMarkdown"].rstrip())
            write_text_atomic(root / "plan.md", completed["planMarkdown"].rstrip())
            run_native("git", ["-C", root, "add", "--", "requirements.md", "plan.md"])
            if run_native("git", ["-C", root, "diff", "--cached", "--quiet"], allowed_exit_codes=(0, 1)).returncode == 1:
                run_native("git", ["-C", root, "commit", "-m", "Plan project implementation"])
            run_native("git", ["-C", root, "push", config["remote"], config["targetBranch"]])
            run_native("git", ["-C", root, "fetch", config["remote"]])
            local_sha = run_native("git", ["-C", root, "rev-parse", "HEAD"]).output.strip()
            remote_sha = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['targetBranch']}"]).output.strip()
            if local_sha != remote_sha:
                raise RalphError("Remote target branch does not match the committed planning result.")

            persisted = [persisted_task(task) for task in completed["tasks"]]
            plan_hash = git_blob_identity(root, remote_sha, "plan.md")
            task_hash = definition_hash(persisted, "task")
            tasks = {
                "schemaVersion": "1.1", "revision": tasks["revision"] + 1, "planHash": plan_hash,
                "definitionHash": task_hash, "status": "ready", "tasks": persisted,
            }
            write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
            state.update(
                stage="build", stageStatus="not_started", requirementsHash=git_blob_identity(root, remote_sha, "requirements.md"),
                planHash=plan_hash, targetBaseSha=remote_sha, taskDefinitionHash=task_hash, bugDefinitionHash=None, blocker=None,
            )
            save_state(state, paths)
            summary = {
                "completedAt": utc_now(), "requirementsHash": state["requirementsHash"], "planHash": plan_hash,
                "requirementsCount": completed["summary"]["requirementsCount"], "taskCount": len(persisted),
                "parallelizableTaskCount": completed["summary"]["parallelizableTaskCount"],
                "dependencyCount": sum(len(task["dependencies"]) for task in persisted),
                "assumptions": completed["summary"]["assumptions"], "deferredScope": completed["summary"]["deferredScope"],
                "deferredRequirementIds": completed["summary"]["deferredRequirementIds"],
                "filesCreatedOrUpdated": ["requirements.md", "plan.md", ".codex/tasks.json", ".codex/state.json", ".codex/planning-summary.json"],
            }
            write_summary(paths.planning_summary, summary)
            show_status(state, tasks)
            print("PLANNING COMPLETE: requirements, plan, and task queue are ready.")
        except Exception as error:
            if state is not None:
                set_blocked(state, paths, "planning", None, str(error), "Correct the reported requirement, configuration, or environment issue, then rerun planning_loop.py.")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan a Worktree Ralph project.")
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--start-new-workflow", action="store_true")
    args = parser.parse_args()
    run(args.repository, args.start_new_workflow)


if __name__ == "__main__":
    main()
