from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAXIMUM_RESULT_BYTES = 1024 * 1024
MAXIMUM_LOG_BYTES = 2 * 1024 * 1024
SEMANTIC_BLOCKERS = {
    "missing_information",
    "contract_conflict",
    "scope_gap",
    "task_decomposition",
    "bug_disposition",
}

PONYTAIL_GUIDANCE = """# Optional Ponytail efficiency guidance

When the `ponytail:ponytail` skill is available and this assignment involves implementation, implementation design, refactoring, debugging, or code review, load and use it at full level.
If it is unavailable, continue without it; Ponytail is optional and its absence is not a blocker.
Never use it to weaken explicit requirements, input validation, security, accessibility, error handling, or required tests.
The role's required JSON output schema overrides Ponytail's response-format guidance.
Do not use it for work that is only prose, status reporting, or a semantic user decision.
"""


class BraceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def repository_root(path: str | Path = ".") -> Path:
    value = run_native("git", ["-C", str(path), "rev-parse", "--show-toplevel"]).output.strip()
    if not value:
        raise BraceError(f"Unable to determine the Git repository root from {path}.")
    return Path(value).resolve()


class Paths:
    def __init__(self, root: str | Path):
        self.repository_root = Path(root).resolve()
        self.codex = self.repository_root / ".codex"
        self.config = self.codex / "workflow.json"
        self.state = self.codex / "state.json"
        self.tasks = self.codex / "tasks.json"
        self.bugs = self.codex / "bugs.json"
        self.planning_summary = self.codex / "planning-summary.json"
        self.build_summary = self.codex / "build-summary.json"
        self.audit_summary = self.codex / "audit-summary.json"
        self.assignments = self.codex / "assignments"
        self.results = self.codex / "results"
        self.logs = self.codex / "logs"
        self.lock = self.codex / "workflow.lock"
        self.prompts = self.codex / "prompts"
        self.schemas = self.codex / "schemas"


class NativeResult:
    def __init__(self, returncode: int, output: str):
        self.returncode = returncode
        self.output = output
        self.lines = output.splitlines()


def _command_line(command: str, arguments: list[str]) -> list[str]:
    source = shutil.which(command)
    if not source:
        candidate = Path(command)
        if not candidate.exists():
            raise BraceError(f"Required command is unavailable: {command}")
        source = str(candidate.resolve())
    suffix = Path(source).suffix.lower()
    if suffix == ".ps1":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            raise BraceError("Required command is unavailable: pwsh")
        return [pwsh, "-NoProfile", "-NonInteractive", "-File", source, *arguments]
    if suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("COMSPEC")
        if not comspec:
            raise BraceError(f"Windows command script cannot run on this platform: {source}")
        return [comspec, "/d", "/c", source, *arguments]
    return [source, *arguments]


def _popen_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_tree(process: subprocess.Popen[str], grace_seconds: int) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired as exc:
        raise BraceError("Process tree did not stop within cleanup grace.") from exc


def run_native(
    command: str,
    arguments: Iterable[Any] = (),
    cwd: str | Path = ".",
    allowed_exit_codes: Iterable[int] | None = (0,),
    timeout_seconds: int = 600,
) -> NativeResult:
    args = [str(value) for value in arguments]
    process = subprocess.Popen(
        _command_line(command, args),
        cwd=str(Path(cwd).resolve()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        **_popen_options(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_tree(process, 10)
        raise BraceError(f"Command exceeded the {timeout_seconds}-second deadline: {command}") from exc
    output = (stdout + os.linesep + stderr).rstrip()
    if allowed_exit_codes is not None and process.returncode not in set(allowed_exit_codes):
        rendered = " ".join(args)
        raise BraceError(f"Command failed with exit code {process.returncode}: {command} {rendered}\n{output}")
    return NativeResult(process.returncode, output)


def read_text(path: str | Path) -> str:
    target = Path(path)
    if not target.is_file():
        raise BraceError(f"Required file does not exist: {target}")
    return target.read_text(encoding="utf-8", errors="strict")


def write_text_atomic(path: str | Path, text: str) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def validate_json(value: Any, schema_path: str | Path) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise BraceError("Python package 'jsonschema' is required; reinstall Brace.") from exc
    path = Path(schema_path).resolve()
    if not path.is_file():
        raise BraceError(f"JSON schema does not exist: {path}")
    from referencing import Registry, Resource

    registry = Registry()
    for schema_file in path.parent.glob("*.json"):
        document = json.loads(read_text(schema_file))
        document.setdefault("$id", schema_file.resolve().as_uri())
        registry = registry.with_resource(schema_file.resolve().as_uri(), Resource.from_contents(document))
        if schema_file.resolve() == path:
            schema = document
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    errors = sorted(validator_class(schema, registry=registry).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        raise BraceError(f"JSON does not satisfy schema {path}: {errors[0].message}")


def read_json(path: str | Path, schema_path: str | Path | None = None) -> Any:
    try:
        value = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise BraceError(f"Invalid JSON in {path}: {exc}") from exc
    if schema_path:
        validate_json(value, schema_path)
    return value


def write_json_atomic(path: str | Path, value: Any, schema_path: str | Path) -> None:
    validate_json(value, schema_path)
    write_text_atomic(path, pretty_json(value))


def file_hash(path: str | Path) -> str | None:
    target = Path(path)
    if not target.is_file():
        return None
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


class WorkflowLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle: Any = None

    def __enter__(self) -> WorkflowLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(f"pid={os.getpid()}\nstarted={utc_now()}".encode())
            self.handle.flush()
            os.fsync(self.handle.fileno())
            return self
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise BraceError(f"Another Brace script is already running for this repository. Lock: {self.path}") from exc

    def close(self) -> None:
        if self.handle:
            self.handle.close()
            self.handle = None

    def __exit__(self, *_: object) -> None:
        self.close()


def get_configuration(root: str | Path) -> dict[str, Any]:
    config = read_json(Paths(root).config)
    if config.get("schemaVersion") != "1.0":
        raise BraceError(f"Unsupported workflow configuration version: {config.get('schemaVersion')}")
    if config.get("provider") not in {"github", "azure_devops"}:
        raise BraceError(f"Unsupported provider: {config.get('provider')}")
    for name in ("remote", "targetBranch", "integrationBranch"):
        if not str(config.get(name) or "").strip():
            raise BraceError(f"workflow.json is missing {name}.")
    if config["targetBranch"] == config["integrationBranch"]:
        raise BraceError("Target and integration branches must differ.")
    bounded = (
        "maximumConcurrentBuilders",
        "maximumConcurrentFixers",
        "maximumTaskAttempts",
        "maximumBugAttempts",
        "maximumPlanningQuestionRounds",
        "maximumAmendmentRounds",
    )
    for name in bounded:
        if not 1 <= int(config.get(name, 0)) <= 32:
            raise BraceError(f"workflow.json field {name} must be between 1 and 32.")
    if not 1 <= int(config.get("agentTimeoutMinutes", 0)) <= 1440:
        raise BraceError("agentTimeoutMinutes must be between 1 and 1440.")
    if not 1 <= int(config.get("agentCleanupGraceSeconds", 0)) <= 120:
        raise BraceError("agentCleanupGraceSeconds must be between 1 and 120.")
    return config


def assert_prerequisites(config: dict[str, Any], require_codex: bool = False) -> None:
    for command in ("git",):
        if not shutil.which(command):
            raise BraceError(f"Required command is unavailable: {command}")
    if require_codex and not shutil.which("codex"):
        raise BraceError("Required command is unavailable: codex")
    if config["provider"] == "github":
        if not shutil.which("gh"):
            raise BraceError("GitHub provider requires the gh CLI.")
        run_native("gh", ["auth", "status"])
    else:
        if not shutil.which("az"):
            raise BraceError("Azure DevOps provider requires the az CLI.")
        run_native("az", ["account", "show", "--output", "none"])
        run_native("az", ["extension", "show", "--name", "azure-devops", "--output", "none"])


def get_repository_identity(root: str | Path, config: dict[str, Any]) -> str:
    if config["provider"] == "github":
        configured = config.get("github", {}).get("repository")
        if configured:
            return str(configured)
        return run_native("gh", ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], root).output.strip()
    azure = config.get("azureDevOps", {})
    for field in ("organization", "project", "repository"):
        if not str(azure.get(field) or "").strip():
            raise BraceError(f"Azure DevOps configuration is missing {field}.")
    return f"{azure['organization']}|{azure['project']}|{azure['repository']}"


def new_state(root: str | Path, config: dict[str, Any], identity: str) -> dict[str, Any]:
    root = Path(root).resolve()
    now = utc_now()
    remote_url = run_native("git", ["-C", root, "config", "--get", f"remote.{config['remote']}.url"]).output.strip()
    return {
        "schemaVersion": "1.2", "revision": 0, "repositoryRoot": str(root),
        "provider": config["provider"], "repository": identity, "remote": config["remote"],
        "remoteUrl": remote_url, "targetBranch": config["targetBranch"], "targetBaseSha": None,
        "integrationBranch": config["integrationBranch"], "configurationHash": file_hash(Paths(root).config),
        "taskDefinitionHash": None, "bugDefinitionHash": None, "stage": "requirements", "stageStatus": "not_started",
        "requirementsHash": None, "planHash": None, "integrationSha": None, "acceptedIntegrationShas": [],
        "finalMergeSha": None, "blocker": None, "amendmentSequence": 0, "activeAmendment": None,
        "createdAt": now, "updatedAt": now,
    }


def save_state(state: dict[str, Any], paths: Paths) -> None:
    state["revision"] = int(state["revision"]) + 1
    state["updatedAt"] = utc_now()
    write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")


def set_blocked(state: dict[str, Any], paths: Paths, scope: str, identity: str | None, message: str, decision: str) -> None:
    state.update(stage="blocked", stageStatus="blocked", blocker={
        "scope": scope, "identity": identity or None, "message": message, "requiredDecision": decision,
    })
    save_state(state, paths)


def git_blob_identity(root: str | Path, reference: str, path: str) -> str:
    oid = run_native("git", ["-C", root, "rev-parse", f"{reference}:{path}"]).output.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
        raise BraceError(f"Unable to identify {path} at {reference}.")
    return f"gitblob:{oid}"


def read_git_text(root: str | Path, reference: str, path: str) -> str:
    return run_native("git", ["-C", root, "show", f"{reference}:{path}"]).output


def _add_missing(value: dict[str, Any], name: str, default: Any) -> None:
    if name not in value:
        value[name] = default


def update_state_schema(root: str | Path, paths: Paths) -> None:
    if paths.state.is_file():
        state = read_json(paths.state)
        _add_missing(state, "amendmentSequence", 0)
        _add_missing(state, "activeAmendment", None)
        _add_missing(state, "acceptedIntegrationShas", [])
        amendment = state.get("activeAmendment")
        if amendment:
            _add_missing(amendment, "decisionIdentity", None)
            _add_missing(amendment, "authorizedDocumentationPaths", ["requirements.md", "plan.md"])
            blocker = amendment["blocker"]
            identity = amendment.get("sourceIdentity") if re.fullmatch(r"(?:TASK|BUG)-\d{4}", str(amendment.get("sourceIdentity") or "")) else None
            _add_missing(blocker, "affectedIdentity", identity)
            _add_missing(blocker, "smallestResolution", str(blocker.get("message", "")))
            _add_missing(blocker, "prohibitedDecisions", [])
            if not str(blocker.get("evidence") or "").strip():
                blocker["evidence"] = str(blocker.get("message", ""))
        if state.get("schemaVersion") in {"1.0", "1.1"}:
            if state["schemaVersion"] == "1.0" and state.get("targetBaseSha"):
                reference = state.get("integrationSha") or state["targetBaseSha"]
                with contextlib.suppress(Exception):
                    state["requirementsHash"] = git_blob_identity(root, reference, "requirements.md")
                with contextlib.suppress(Exception):
                    state["planHash"] = git_blob_identity(root, reference, "plan.md")
            state["schemaVersion"] = "1.2"
            write_json_atomic(paths.state, state, paths.schemas / "state.schema.json")
    if paths.tasks.is_file():
        tasks = read_json(paths.tasks)
        if tasks.get("schemaVersion") == "1.0":
            for task in tasks["tasks"]:
                _add_missing(task, "amendmentId", None)
                _add_missing(task, "supersededBy", [])
            tasks["schemaVersion"] = "1.1"
            if paths.state.is_file():
                state = read_json(paths.state)
                if state.get("planHash"):
                    tasks["planHash"] = state["planHash"]
            write_json_atomic(paths.tasks, tasks, paths.schemas / "tasks.schema.json")
    if paths.bugs.is_file():
        bugs = read_json(paths.bugs)
        if bugs.get("schemaVersion") in {"1.0", "1.1"}:
            for bug in bugs["bugs"]:
                _add_missing(bug, "amendmentId", None)
                _add_missing(bug, "dispositionEvidence", None)
            bugs["schemaVersion"] = "1.2"
            write_json_atomic(paths.bugs, bugs, paths.schemas / "bugs.schema.json")


def initialize_state_files(root: str | Path, config: dict[str, Any]) -> Paths:
    paths = Paths(root)
    for directory in (paths.logs, paths.assignments, paths.results):
        directory.mkdir(parents=True, exist_ok=True)
    if not paths.state.is_file():
        write_json_atomic(paths.state, new_state(root, config, get_repository_identity(root, config)), paths.schemas / "state.schema.json")
    if not paths.tasks.is_file():
        write_json_atomic(paths.tasks, {"schemaVersion": "1.1", "revision": 0, "planHash": None, "definitionHash": None, "status": "not_planned", "tasks": []}, paths.schemas / "tasks.schema.json")
    if not paths.bugs.is_file():
        write_json_atomic(paths.bugs, {"schemaVersion": "1.2", "revision": 0, "auditSha": None, "definitionHash": None, "status": "not_audited", "bugs": []}, paths.schemas / "bugs.schema.json")
    update_state_schema(root, paths)
    return paths


def assert_state_identity(state: dict[str, Any], root: str | Path, config: dict[str, Any]) -> None:
    root = Path(root).resolve()
    if Path(state["repositoryRoot"]).resolve() != root:
        raise BraceError("state.json belongs to another repository path.")
    for field in ("provider", "remote", "targetBranch", "integrationBranch"):
        if state[field] != config[field]:
            raise BraceError(f"state.json {field} differs from workflow.json.")
    identity = get_repository_identity(root, config)
    if state["repository"] != identity:
        raise BraceError("state.json repository identity differs from the configured provider repository.")
    if state["configurationHash"] != file_hash(Paths(root).config):
        raise BraceError("workflow.json changed after workflow creation.")
    remote_url = run_native("git", ["-C", root, "config", "--get", f"remote.{config['remote']}.url"]).output.strip()
    if state["remoteUrl"] != remote_url:
        raise BraceError("Git remote URL changed after workflow creation.")
    normalized = re.sub(r"\.git$", "", remote_url.replace("\\", "/").lower())
    if config["provider"] == "github" and not normalized.endswith(identity.lower()):
        raise BraceError("Git remote URL does not match the GitHub repository identity.")
    if config["provider"] == "azure_devops":
        repo = str(config["azureDevOps"]["repository"]).lower()
        if not (normalized.endswith(f"/{repo}") or normalized.endswith(f"/_git/{repo}")):
            raise BraceError("Git remote URL does not match the Azure DevOps repository identity.")


def object_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def definition_hash(items: list[dict[str, Any]], kind: str) -> str:
    if kind == "task":
        names = ("taskId", "title", "description", "requirementIds", "planSections", "dependencies", "allowedPaths", "exclusiveResources", "acceptanceCriteria", "checks")
    else:
        names = ("bugId", "title", "severity", "category", "requirementIds", "description", "evidence", "actualBehavior", "requiredBehavior", "impact", "requiredCorrection", "acceptanceTest", "dependencies", "allowedPaths", "exclusiveResources")
    return object_hash([{name: item[name] for name in names} for item in items])


def assert_ledger_identity(state: dict[str, Any], ledger: dict[str, Any], kind: str) -> None:
    if kind == "task":
        if ledger["planHash"] != state["planHash"]:
            raise BraceError("tasks.json plan hash differs from state.json.")
        expected, actual = state["taskDefinitionHash"], definition_hash(ledger["tasks"], "task")
    else:
        expected, actual = state["bugDefinitionHash"], definition_hash(ledger["bugs"], "bug")
    if ledger["definitionHash"] != actual or expected != actual:
        raise BraceError(f"{kind} ledger definitions changed after they were frozen.")


def assert_target_drift(root: str | Path, config: dict[str, Any], state: dict[str, Any]) -> str:
    run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
    actual = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['targetBranch']}"]).output.strip()
    if state.get("targetBaseSha") and state["targetBaseSha"] != actual:
        raise BraceError("Target branch advanced after planning. Reconcile it before continuing.")
    return actual


def attempt_path(paths: Paths, kind: str, identity: str, attempt: int) -> Path:
    directory = paths.assignments if kind == "assignment" else paths.results
    return directory / f"{identity}-attempt-{attempt:03d}.json"


def write_immutable_json(path: str | Path, value: Any) -> None:
    rendered = pretty_json(value)
    target = Path(path)
    if target.is_file():
        if read_text(target).strip() != rendered.strip():
            raise BraceError(f"Immutable attempt record already exists with different content: {target}")
        return
    write_text_atomic(target, rendered)


def read_attempt_result(paths: Paths, identity: str, attempt: int) -> dict[str, Any] | None:
    path = attempt_path(paths, "result", identity, attempt)
    return read_json(path) if path.is_file() else None


def worktree_base(root: str | Path, config: dict[str, Any]) -> Path:
    configured = config.get("worktreeRoot")
    parent = Path(configured).resolve() if configured else Path(tempfile.gettempdir()) / "brace"
    repository_id = hashlib.sha256(str(Path(root).resolve()).upper().encode("utf-8")).hexdigest()[:16]
    return (parent / repository_id).resolve()


def reset_completed_workflow(root: str | Path, config: dict[str, Any], state: dict[str, Any]) -> None:
    if state.get("stage") != "complete" or state.get("stageStatus") != "complete":
        raise BraceError("Only a completed workflow may be replaced.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(state.get("finalMergeSha") or "")):
        raise BraceError("Completed workflow is missing its verified final merge SHA.")
    if run_native("git", ["-C", root, "status", "--porcelain", "--untracked-files=all"]).output.strip():
        raise BraceError("The repository must be clean before starting a new workflow.")
    base = worktree_base(root, config)
    if base.is_dir() and any(base.iterdir()):
        raise BraceError("Owned worktrees remain; clean them before starting a new workflow.")
    run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
    for ref in (f"refs/heads/{config['integrationBranch']}", f"refs/remotes/{config['remote']}/{config['integrationBranch']}"):
        if run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", ref], allowed_exit_codes=(0, 1)).returncode == 0:
            raise BraceError("The previous integration branch still exists.")
    paths = Paths(root)
    archive = paths.logs / f"completed-{state['finalMergeSha'][:12]}"
    for directory in (paths.assignments, paths.results):
        if directory.is_dir() and any(directory.iterdir()):
            archive.mkdir(parents=True, exist_ok=True)
            destination = archive / directory.name
            if destination.exists():
                raise BraceError(f"Completed attempt archive already exists: {destination}")
            shutil.move(str(directory), destination)
    for path in (paths.state, paths.tasks, paths.bugs, paths.planning_summary, paths.build_summary, paths.audit_summary):
        path.unlink(missing_ok=True)


def assert_plan_drift(state: dict[str, Any], root: str | Path, require_plan: bool = False) -> None:
    status = run_native("git", ["-C", root, "status", "--porcelain", "--", "requirements.md", "plan.md"]).output
    if status.strip():
        raise BraceError("requirements.md changed or plan.md changed in the coordinator checkout.")
    if state.get("targetBaseSha") is None:
        requirements_hash = file_hash(Path(root) / "requirements.md")
        plan_hash = file_hash(Path(root) / "plan.md")
    else:
        reference = state.get("integrationSha") or state["targetBaseSha"]
        requirements_hash = git_blob_identity(root, reference, "requirements.md")
        try:
            plan_hash = git_blob_identity(root, reference, "plan.md")
        except BraceError:
            plan_hash = None
    if require_plan and plan_hash is None:
        raise BraceError("plan.md is required. Run the planning loop first.")
    if state.get("requirementsHash") is not None and state["requirementsHash"] != requirements_hash:
        raise BraceError("requirements.md at the recorded contract commit differs from state.json.")
    if require_plan and state.get("planHash") is not None and state["planHash"] != plan_hash:
        raise BraceError("plan.md at the recorded contract commit differs from state.json.")


def safe_relative_pattern(pattern: str) -> bool:
    if not pattern or Path(pattern).is_absolute():
        return False
    normalized = pattern.replace("\\", "/").strip()
    return not normalized.startswith("/") and not re.search(r"(^|/)\.\.(/|$)", normalized) and not re.search(r"(^|/)\.git(/|$)", normalized)


PROTECTED_PATHS = (
    "requirements.md", "plan.md", ".codex/state.json", ".codex/tasks.json",
    ".codex/bugs.json", ".codex/planning-summary.json", ".codex/build-summary.json",
    ".codex/audit-summary.json", ".codex/logs", ".codex/logs/**",
)


def assert_assignment_paths(item: dict[str, Any]) -> None:
    identity = item.get("taskId") or item.get("bugId")
    for pattern in item["allowedPaths"]:
        if not safe_relative_pattern(str(pattern)):
            raise BraceError(f"{identity} contains an unsafe allowed path: {pattern}")
        normalized = str(pattern).replace("\\", "/").lstrip("./")
        pattern_base = normalized.replace("/**", "").rstrip("*/")
        if not pattern_base:
            raise BraceError(f"Assignment path is too broad to protect coordinator state: {pattern}")
        for blocked in PROTECTED_PATHS:
            blocked_base = blocked.replace("/**", "")
            if normalized == blocked or pattern_base == blocked_base or blocked_base.startswith(f"{pattern_base}/"):
                raise BraceError(f"Assignment path may include coordinator-owned content: {pattern}")


def assert_graph(items: list[dict[str, Any]], kind: str) -> None:
    key, prefix = ("taskId", "TASK") if kind == "task" else ("bugId", "BUG")
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, 1):
        expected, identity = f"{prefix}-{index:04d}", str(item[key])
        if identity != expected:
            raise BraceError(f"Expected {kind} identity {expected} but found {identity}.")
        if identity in by_id:
            raise BraceError(f"Duplicate {kind} identity: {identity}")
        by_id[identity] = item
        assert_assignment_paths(item)
    for item in items:
        identity = item[key]
        for dependency in item["dependencies"]:
            if dependency not in by_id:
                raise BraceError(f"{identity} depends on unknown {kind} {dependency}.")
            if dependency == identity:
                raise BraceError(f"{identity} depends on itself.")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identity: str) -> None:
        if identity in visiting:
            raise BraceError(f"{kind} dependency graph contains a cycle at {identity}.")
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in by_id[identity]["dependencies"]:
            visit(dependency)
        visiting.remove(identity)
        visited.add(identity)

    for identity in by_id:
        visit(identity)


def assert_task_coverage(tasks: list[dict[str, Any]], requirements_markdown: str, deferred: Iterable[str] = ()) -> None:
    deferred_set = set(deferred)
    required = {match for match in re.findall(r"\bREQ-[A-Z0-9-]+\b", requirements_markdown) if not match.startswith("REQ-NONGOAL-") and match not in deferred_set}
    covered = {requirement for task in tasks for requirement in task["requirementIds"]}
    missing = sorted(required - covered)
    if missing:
        raise BraceError(f"Task graph does not cover requirements: {', '.join(missing)}")


def items_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if set(left["exclusiveResources"]) & set(right["exclusiveResources"]):
        return True
    for left_path in left["allowedPaths"]:
        if any(char in str(left_path).replace("/**", "") for char in "*?["):
            return True
        left_base = str(left_path).replace("\\", "/").replace("/**", "").rstrip("*/")
        for right_path in right["allowedPaths"]:
            if any(char in str(right_path).replace("/**", "") for char in "*?["):
                return True
            right_base = str(right_path).replace("\\", "/").replace("/**", "").rstrip("*/")
            if left_base == right_base or left_base.startswith(f"{right_base}/") or right_base.startswith(f"{left_base}/"):
                return True
    return False


def select_ready_items(items: list[dict[str, Any]], kind: str, maximum: int) -> list[dict[str, Any]]:
    key, pending, complete = ("taskId", "pending", "integrated") if kind == "task" else ("bugId", "open", "verified")
    by_id = {item[key]: item for item in items}
    ready = [item for item in items if item["status"] == pending and all(by_id[dep]["status"] == complete for dep in item["dependencies"])]
    selected: list[dict[str, Any]] = []
    for candidate in ready:
        if len(selected) >= maximum:
            break
        if not any(items_conflict(existing, candidate) for existing in selected):
            selected.append(candidate)
    return selected


def remove_empty_worktree_containers(root: str | Path, config: dict[str, Any]) -> None:
    base = worktree_base(root, config)
    if base.is_dir() and not any(base.iterdir()):
        base.rmdir()
    configured = config.get("worktreeRoot")
    if configured:
        configured_root = Path(configured).resolve()
        if configured_root.is_dir() and base != configured_root and not any(configured_root.iterdir()):
            configured_root.rmdir()


def new_worktree(root: str | Path, config: dict[str, Any], identity: str, branch: str, base_reference: str, expected_head: str | None = None) -> Path:
    if not re.fullmatch(r"(?:TASK|BUG|AMEND)-\d{4}", identity):
        raise BraceError(f"Invalid worktree identity: {identity}")
    if branch != f"worktree/{identity}":
        raise BraceError(f"Unexpected branch for {identity}: {branch}")
    base = worktree_base(root, config)
    base.mkdir(parents=True, exist_ok=True)
    path = (base / identity).resolve()
    if path.is_dir():
        actual_root = Path(run_native("git", ["-C", path, "rev-parse", "--show-toplevel"]).output.strip()).resolve()
        actual_branch = run_native("git", ["-C", path, "branch", "--show-current"]).output.strip()
        if actual_root != path or actual_branch != branch:
            raise BraceError(f"Existing worktree does not match {identity}: {path}")
        if run_native("git", ["-C", path, "status", "--porcelain", "--untracked-files=all"]).output.strip():
            raise BraceError(f"Interrupted worktree contains uncommitted changes: {identity}")
        head = run_native("git", ["-C", path, "rev-parse", "HEAD"]).output.strip()
        if run_native("git", ["-C", path, "merge-base", "--is-ancestor", base_reference, head], allowed_exit_codes=(0, 1)).returncode != 0:
            raise BraceError(f"Existing worktree branch does not descend from its recorded base: {identity}")
        if expected_head and head != expected_head:
            raise BraceError(f"Existing worktree HEAD differs from its recorded result: {identity}")
        return path
    exists = run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], allowed_exit_codes=(0, 1)).returncode == 0
    args = ["-C", root, "worktree", "add"] + (["--", path, branch] if exists else ["-b", branch, "--", path, base_reference])
    run_native("git", args)
    head = run_native("git", ["-C", path, "rev-parse", "HEAD"]).output.strip()
    if exists and run_native("git", ["-C", path, "merge-base", "--is-ancestor", base_reference, head], allowed_exit_codes=(0, 1)).returncode != 0:
        raise BraceError(f"Existing branch does not descend from its recorded base: {identity}")
    if expected_head and head != expected_head:
        raise BraceError(f"Existing branch HEAD differs from its recorded result: {identity}")
    return path


def remove_worktree(root: str | Path, config: dict[str, Any], identity: str, branch: str) -> None:
    base = worktree_base(root, config)
    path = (base / identity).resolve()
    if path.parent != base or path.name != identity:
        raise BraceError(f"Refusing to remove unexpected worktree path: {path}")
    if path.is_dir():
        run_native("git", ["-C", root, "worktree", "remove", "--force", "--", path])
    if run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], allowed_exit_codes=(0, 1)).returncode == 0:
        run_native("git", ["-C", root, "branch", "-D", "--", branch])
    remove_empty_worktree_containers(root, config)


def reset_rejected_assignment(root: str | Path, config: dict[str, Any], item: dict[str, Any], kind: str) -> None:
    identity = item["taskId" if kind == "task" else "bugId"]
    path = new_worktree(root, config, identity, item["branch"], item["baseSha"])
    head = run_native("git", ["-C", path, "rev-parse", "HEAD"]).output.strip()
    if head not in {item["baseSha"], item["resultSha"]}:
        raise BraceError(f"Rejected assignment HEAD differs from its recorded result: {identity}")
    if head != item["baseSha"]:
        run_native("git", ["-C", path, "reset", "--hard", item["baseSha"]])


def _matches_path(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path.lower(), pattern.replace("\\", "/").lower())


def assert_assignment_commit(worktree: str | Path, base_sha: str, item: dict[str, Any]) -> dict[str, Any]:
    if run_native("git", ["-C", worktree, "status", "--porcelain", "--untracked-files=all"]).output.strip():
        raise BraceError("Agent worktree is not clean after its reported commit.")
    head = run_native("git", ["-C", worktree, "rev-parse", "HEAD"]).output.strip()
    ancestor = run_native("git", ["-C", worktree, "merge-base", "--is-ancestor", base_sha, head], allowed_exit_codes=(0, 1)).returncode == 0
    if not ancestor or head == base_sha:
        raise BraceError("Agent did not create a descendant commit for the assignment.")
    count = int(run_native("git", ["-C", worktree, "rev-list", "--count", f"{base_sha}..{head}"]).output.strip())
    if count != 1:
        raise BraceError(f"Agent assignment must contain exactly one commit; found {count}.")
    changed = run_native("git", ["-C", worktree, "diff", "--name-only", f"{base_sha}..{head}"]).lines
    for changed_path in changed:
        normalized = changed_path.replace("\\", "/")
        if not any(_matches_path(normalized, pattern) for pattern in item["allowedPaths"]):
            raise BraceError(f"Agent modified a path outside its assignment: {normalized}")
        if normalized in {"requirements.md", "plan.md", ".codex/state.json", ".codex/tasks.json", ".codex/bugs.json"}:
            raise BraceError(f"Agent modified coordinator-owned content: {normalized}")
    return {"Head": head, "ChangedFiles": changed}


def recover_committed_attempt(root: str | Path, paths: Paths, item: dict[str, Any], kind: str) -> dict[str, Any] | None:
    worktree = item.get("worktree")
    if not worktree or not Path(worktree).is_dir():
        return None
    identity = item["taskId" if kind == "task" else "bugId"]
    if not item.get("baseSha") or int(item.get("attemptCount", 0)) < 1:
        raise BraceError(f"Cannot recover {identity}: base or attempt identity is missing.")
    if run_native("git", ["-C", worktree, "status", "--porcelain", "--untracked-files=all"]).output.strip():
        raise BraceError(f"Interrupted worktree contains uncommitted changes: {identity}")
    head = run_native("git", ["-C", worktree, "rev-parse", "HEAD"]).output.strip()
    if run_native("git", ["-C", worktree, "merge-base", "--is-ancestor", item["baseSha"], head], allowed_exit_codes=(0, 1)).returncode != 0:
        raise BraceError(f"Interrupted worktree does not descend from its recorded base: {identity}")
    if head == item["baseSha"]:
        return None
    schema, role, expected = ("builder-result.schema.json", "builder", "completed") if kind == "task" else ("fixer-result.schema.json", "bug-fixer", "fixed")
    context = f"Recovery mode for {identity} attempt {item['attemptCount']}. A commit exists but the coordinator result record was interrupted. Inspect exact base {item['baseSha']} and HEAD {head}. Do not edit, commit, or rewrite history. Return the structured result for the existing commit only.\nAssignment:\n{pretty_json(item)}"
    result = invoke_role(root, worktree, role, context, schema, "read-only")
    if result["status"] != expected or result.get("commitSha") != head:
        raise BraceError(f"Recovered result does not identify the existing {identity} commit.")
    if run_native("git", ["-C", worktree, "status", "--porcelain", "--untracked-files=all"]).output.strip() or run_native("git", ["-C", worktree, "rev-parse", "HEAD"]).output.strip() != head:
        raise BraceError(f"Recovery agent modified the {identity} worktree.")
    record = {"schemaVersion": "1.0", "identity": identity, "attempt": int(item["attemptCount"]), "succeeded": True, "result": result, "error": None, "completedAt": utc_now()}
    write_immutable_json(attempt_path(paths, "result", identity, int(item["attemptCount"])), record)
    return record


def ensure_integration_branch(root: str | Path, config: dict[str, Any], state: dict[str, Any], allowed_merge_shas: Iterable[str] = ()) -> str:
    remote, branch, target = config["remote"], config["integrationBranch"], config["targetBranch"]
    run_native("git", ["-C", root, "fetch", remote, "--prune"])
    local_exists = run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], allowed_exit_codes=(0, 1)).returncode == 0
    remote_exists = run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"], allowed_exit_codes=(0, 1)).returncode == 0
    if not local_exists and not remote_exists:
        run_native("git", ["-C", root, "branch", branch, f"{remote}/{target}"])
        run_native("git", ["-C", root, "push", "--set-upstream", remote, branch])
    elif not local_exists:
        run_native("git", ["-C", root, "branch", "--track", branch, f"{remote}/{branch}"])
    elif not remote_exists:
        raise BraceError(f"Local integration branch exists without its remote counterpart: {branch}")
    local_sha = run_native("git", ["-C", root, "rev-parse", branch]).output.strip()
    remote_sha = run_native("git", ["-C", root, "rev-parse", f"{remote}/{branch}"]).output.strip()
    allowed = set(allowed_merge_shas) | set(state.get("acceptedIntegrationShas", []))
    for previous in (local_sha, state.get("integrationSha")):
        if not previous or previous == remote_sha:
            continue
        if run_native("git", ["-C", root, "merge-base", "--is-ancestor", previous, remote_sha], allowed_exit_codes=(0, 1)).returncode != 0:
            raise BraceError(f"Integration branch diverged from recorded state: {branch}")
        unknown = [line for line in run_native("git", ["-C", root, "rev-list", "--first-parent", f"{previous}..{remote_sha}"]).lines if line not in allowed]
        if unknown:
            raise BraceError(f"Integration branch contains unowned commits: {', '.join(unknown)}")
    if local_sha != remote_sha:
        run_native("git", ["-C", root, "branch", "-f", branch, remote_sha])
    return remote_sha


def _pull_request_record(value: dict[str, Any], provider: str, repository: str) -> dict[str, Any]:
    if provider == "github":
        merge = value.get("mergeCommit")
        return {"id": str(value["number"]), "url": str(value["url"]), "state": str(value["state"]).lower(), "repository": repository, "head": str(value["headRefName"]), "headSha": str(value["headRefOid"]), "base": str(value["baseRefName"]), "baseSha": str(value["baseRefOid"]), "mergeSha": str(merge["oid"]) if merge else None}
    source = str(value["sourceRefName"]).removeprefix("refs/heads/")
    target = str(value["targetRefName"]).removeprefix("refs/heads/")
    merge = value.get("lastMergeCommit")
    return {"id": str(value["pullRequestId"]), "url": str(value["url"]), "state": str(value["status"]).lower(), "repository": repository, "head": source, "headSha": str(value["lastMergeSourceCommit"]["commitId"]), "base": target, "baseSha": str(value["lastMergeTargetCommit"]["commitId"]), "mergeSha": str(merge["commitId"]) if merge else None}


def get_pull_request(root: str | Path, config: dict[str, Any], head: str, base: str, expected_head_sha: str | None = None, pull_request_id: str | None = None) -> dict[str, Any] | None:
    repository = get_repository_identity(root, config)
    if config["provider"] == "github":
        output = run_native("gh", ["pr", "list", "--repo", repository, "--state", "all", "--head", head, "--base", base, "--limit", "50", "--json", "number,url,state,headRefName,headRefOid,baseRefName,baseRefOid,mergeCommit"], root).output
    else:
        azure = config["azureDevOps"]
        output = run_native("az", ["repos", "pr", "list", "--organization", azure["organization"], "--project", azure["project"], "--repository", azure["repository"], "--source-branch", head, "--target-branch", base, "--status", "all", "--output", "json"], root).output
    records = [_pull_request_record(value, config["provider"], repository) for value in json.loads(output or "[]")]
    if pull_request_id:
        records = [record for record in records if record["id"] == str(pull_request_id)]
    if expected_head_sha:
        exact = [record for record in records if record["headSha"] == expected_head_sha]
        if not exact and any(record["state"] in {"open", "active"} for record in records):
            raise BraceError(f"An open pull request for {head} has a different head SHA.")
        records = exact
    if len(records) > 1:
        raise BraceError(f"Multiple pull requests match exact assignment {head} -> {base}.")
    return records[0] if records else None


def new_pull_request(root: str | Path, config: dict[str, Any], head: str, base: str, expected_head_sha: str, expected_base_sha: str, title: str, body: str) -> dict[str, Any]:
    existing = get_pull_request(root, config, head, base, expected_head_sha)
    if existing:
        return existing
    run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
    remote_head = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{head}"]).output.strip()
    remote_base = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{base}"]).output.strip()
    if remote_head != expected_head_sha or remote_base != expected_base_sha:
        raise BraceError("Remote pull-request head or base moved before creation.")
    if config["provider"] == "github":
        repository = get_repository_identity(root, config)
        run_native("gh", ["pr", "create", "--repo", repository, "--head", head, "--base", base, "--title", title, "--body", body], root)
    else:
        azure = config["azureDevOps"]
        run_native("az", ["repos", "pr", "create", "--organization", azure["organization"], "--project", azure["project"], "--repository", azure["repository"], "--source-branch", head, "--target-branch", base, "--title", title, "--description", body, "--output", "none"], root)
    created = get_pull_request(root, config, head, base, expected_head_sha)
    if not created:
        raise BraceError("Provider did not return the newly created exact pull request.")
    created["baseSha"] = expected_base_sha
    return created


def complete_pull_request(root: str | Path, config: dict[str, Any], pull_request: dict[str, Any]) -> dict[str, Any]:
    if pull_request["repository"] != get_repository_identity(root, config):
        raise BraceError("Pull request repository identity does not match this workflow.")
    if pull_request["state"] not in {"merged", "completed"}:
        if pull_request["state"] not in {"open", "active"}:
            raise BraceError(f"Pull request {pull_request['id']} cannot be merged from state {pull_request['state']}.")
        if config["provider"] == "github":
            args = ["pr", "merge", pull_request["id"], "--repo", pull_request["repository"], "--squash"]
            if config.get("deleteMergedBranches"):
                args.append("--delete-branch")
            run_native("gh", args, root)
        else:
            azure = config["azureDevOps"]
            args = ["repos", "pr", "update", "--organization", azure["organization"], "--id", pull_request["id"], "--status", "completed", "--squash", "true", "--output", "none"]
            if config.get("deleteMergedBranches"):
                args += ["--delete-source-branch", "true"]
            run_native("az", args, root)
    merged = get_pull_request(root, config, pull_request["head"], pull_request["base"], pull_request["headSha"], pull_request["id"])
    if not merged or merged["state"] not in {"merged", "completed"} or not merged.get("mergeSha"):
        raise BraceError("Provider did not return a verifiable merged pull request.")
    run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
    base_sha = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{pull_request['base']}"]).output.strip()
    if run_native("git", ["-C", root, "merge-base", "--is-ancestor", merged["mergeSha"], base_sha], allowed_exit_codes=(0, 1)).returncode != 0:
        raise BraceError("Remote base does not contain the provider merge result.")
    merged["baseSha"] = pull_request["baseSha"]
    return merged


def publish_assignment(root: str | Path, worktree: str | Path, config: dict[str, Any], item: dict[str, Any], kind: str) -> dict[str, Any]:
    identity = item["taskId" if kind == "task" else "bugId"]
    branch = item["branch"]
    run_native("git", ["-C", worktree, "push", "--set-upstream", config["remote"], branch])
    run_native("git", ["-C", root, "fetch", config["remote"], "--prune"])
    base_sha = run_native("git", ["-C", root, "rev-parse", f"{config['remote']}/{config['integrationBranch']}"]).output.strip()
    pull_request = new_pull_request(root, config, branch, config["integrationBranch"], item["resultSha"], base_sha, f"{identity} {item['title']}", f"Brace {kind} {identity}")
    return complete_pull_request(root, config, pull_request)


def remove_merged_assignment(root: str | Path, config: dict[str, Any], identity: str, branch: str, pull_request: dict[str, Any]) -> None:
    if pull_request["state"] not in {"merged", "completed"}:
        raise BraceError(f"Refusing cleanup because pull request {pull_request['id']} is not merged.")
    remove_worktree(root, config, identity, branch)
    remote_ref = f"refs/remotes/{config['remote']}/{branch}"
    exists = run_native("git", ["-C", root, "show-ref", "--verify", "--quiet", remote_ref], allowed_exit_codes=(0, 1)).returncode == 0
    if exists and config.get("deleteMergedBranches"):
        run_native("git", ["-C", root, "push", config["remote"], "--delete", branch])


def invoke_codex(prompt: str, cwd: str | Path, schema_path: str | Path, sandbox: str, log_directory: str | Path, identity: str = "agent", timeout_minutes: int = 90, cleanup_grace_seconds: int = 10, timeout_seconds: int = 0) -> dict[str, Any]:
    token, safe_identity = uuid.uuid4().hex, re.sub(r"[^A-Za-z0-9_.-]", "_", identity)
    log_directory = Path(log_directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    result_path = log_directory / f"{safe_identity}-{token}.result.json"
    log_path = log_directory / f"{safe_identity}-{token}.log"
    command = _command_line("codex", ["exec", "--ephemeral", "--color", "never", "--sandbox", sandbox, "--output-schema", str(Path(schema_path).resolve()), "--output-last-message", str(result_path), "-"])
    process = subprocess.Popen(command, cwd=str(Path(cwd).resolve()), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict", **_popen_options())
    timeout = timeout_seconds or timeout_minutes * 60
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_tree(process, cleanup_grace_seconds)
        raise BraceError(f"Codex exceeded the {timeout_minutes}-minute deadline; its process tree was terminated.") from exc
    output = stdout + os.linesep + stderr
    if len(output.encode("utf-8")) > MAXIMUM_LOG_BYTES:
        encoded = output.encode("utf-8")[:MAXIMUM_LOG_BYTES]
        output = encoded.decode("utf-8", errors="ignore") + "\n[log truncated]"
    write_text_atomic(log_path, output)
    if process.returncode != 0:
        raise BraceError(f"Codex exited with code {process.returncode}. Log: {log_path}")
    if not result_path.is_file():
        raise BraceError(f"Codex did not create its final result. Log: {log_path}")
    if result_path.stat().st_size > MAXIMUM_RESULT_BYTES:
        raise BraceError(f"Codex result exceeded 1 MiB: {result_path}")
    return read_json(result_path, schema_path)


def invoke_role(root: str | Path, cwd: str | Path, role: str, context: str, schema_name: str, sandbox: str) -> dict[str, Any]:
    paths, config = Paths(root), get_configuration(root)
    prompt = f"{read_text(paths.codex / 'AGENTS.md')}\n\n{PONYTAIL_GUIDANCE}\n{read_text(paths.prompts / f'{role}.md')}\n\n# Assignment context\n\n{context}"
    return invoke_codex(prompt, cwd, paths.schemas / schema_name, sandbox, paths.logs, role, int(config["agentTimeoutMinutes"]), int(config["agentCleanupGraceSeconds"]))


def write_summary(path: str | Path, summary: dict[str, Any]) -> None:
    write_text_atomic(path, pretty_json(summary))


def show_status(state: dict[str, Any], tasks: dict[str, Any] | None = None, bugs: dict[str, Any] | None = None) -> None:
    from .ui import render_status

    render_status(state, tasks, bugs)


def new_audit_worktree(root: str | Path, config: dict[str, Any], reference: str) -> Path:
    base = worktree_base(root, config)
    base.mkdir(parents=True, exist_ok=True)
    path = (base / "AUDIT").resolve()
    if path.is_dir():
        run_native("git", ["-C", root, "worktree", "remove", "--force", "--", path])
    run_native("git", ["-C", root, "worktree", "add", "--detach", "--", path, reference])
    return path


def remove_audit_worktree(root: str | Path, config: dict[str, Any]) -> None:
    base = worktree_base(root, config)
    path = (base / "AUDIT").resolve()
    if path.parent != base or path.name != "AUDIT":
        raise BraceError(f"Refusing to remove unexpected audit worktree: {path}")
    if path.is_dir():
        run_native("git", ["-C", root, "worktree", "remove", "--force", "--", path])
    remove_empty_worktree_containers(root, config)


def run_assignment(root: str | Path, worktree: str | Path, item: dict[str, Any], kind: str, paths: Paths) -> dict[str, Any]:
    identity = item["taskId" if kind == "task" else "bugId"]
    role, schema, label = ("builder", "builder-result.schema.json", "Task") if kind == "task" else ("bug-fixer", "fixer-result.schema.json", "Bug")
    try:
        result = invoke_role(root, worktree, role, f"{label} assignment:\n{pretty_json(item)}", schema, "workspace-write")
        record = {"schemaVersion": "1.0", "identity": identity, "attempt": int(item["attemptCount"]), "succeeded": True, "result": result, "error": None, "completedAt": utc_now()}
    except Exception as exc:
        record = {"schemaVersion": "1.0", "identity": identity, "attempt": int(item["attemptCount"]), "succeeded": False, "result": None, "error": str(exc), "completedAt": utc_now()}
    write_immutable_json(attempt_path(paths, "result", identity, int(item["attemptCount"])), record)
    return record
