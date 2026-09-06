from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

from importlib.resources import files

from .common import get_configuration, initialize_state_files, read_json, run_native
from .ui import ask, confirm, info, success, warning


class BootstrapError(RuntimeError):
    pass


def bundled_template() -> Path:
    return Path(str(files("brace").joinpath("resources", "template")))


def required(value: str | None, prompt: str) -> str:
    result = (value or ask(prompt)).strip()
    if not result:
        raise BootstrapError(f"{prompt} is required.")
    return result


def add_section(path: Path, name: str, content: str) -> None:
    begin, end = f"# BEGIN {name}", f"# END {name}"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if begin in existing or end in existing:
        raise BootstrapError(f"The existing file contains an incomplete or duplicate {name} section: {path}")
    prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
    path.write_text(f"{prefix}{begin}\n{content.strip()}\n{end}\n", encoding="utf-8", newline="\n")


def existing_details(path: str) -> dict[str, str]:
    requested = Path(path).resolve()
    if not requested.is_dir():
        raise BootstrapError(f"Existing repository directory does not exist: {requested}")
    root_result = run_native("git", ["-C", requested, "rev-parse", "--show-toplevel"], allowed_exit_codes=(0, 1))
    if root_result.returncode:
        raise BootstrapError(f"Path is not a Git repository: {requested}")
    root = Path(root_result.output).resolve()
    if root != requested:
        info(f"Using repository root: {root}")
    if run_native("git", ["-C", root, "status", "--porcelain", "--untracked-files=all"]).output.strip():
        raise BootstrapError(f"Existing repository worktree is not clean: {root}")
    if (root / ".codex").is_dir():
        raise BootstrapError(f"Existing repository already contains .codex; refusing to overwrite or duplicate a workflow installation: {root}")
    branch = run_native("git", ["-C", root, "branch", "--show-current"]).output.strip()
    if not branch:
        raise BootstrapError("Existing repository must be checked out on a branch, not a detached HEAD.")
    remote_url = run_native("git", ["-C", root, "config", "--get", "remote.origin.url"], allowed_exit_codes=(0, 1)).output.strip()
    if not remote_url:
        raise BootstrapError("Existing repository must have an origin remote.")
    run_native("git", ["-C", root, "fetch", "origin", "--prune"])
    remote_head = run_native("git", ["-C", root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], allowed_exit_codes=(0, 1))
    target = re.sub(r"^origin/", "", remote_head.output.strip()) if remote_head.returncode == 0 and remote_head.output.strip() else branch
    if branch != target:
        raise BootstrapError(f"Existing repository must be checked out on its target branch '{target}'; current branch is '{branch}'.")
    local_sha = run_native("git", ["-C", root, "rev-parse", "HEAD"]).output.strip()
    remote_sha = run_native("git", ["-C", root, "rev-parse", f"refs/remotes/origin/{target}"], allowed_exit_codes=(0, 1))
    if remote_sha.returncode:
        raise BootstrapError(f"Origin does not contain target branch '{target}'.")
    if local_sha != remote_sha.output.strip():
        raise BootstrapError(f"Local {target} must exactly match origin/{target} before installation.")

    normalized = re.sub(r"\.git$", "", remote_url.strip())
    github = re.search(r"github\.com[:/]([^/:\s]+)/([^/\s]+)$", normalized, re.IGNORECASE)
    if github:
        owner, repository = github.groups()
        return {"root": str(root), "provider": "github", "target": target, "identity": f"{owner}/{repository}", "repository": repository, "github_owner": owner}
    azure = re.search(r"dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$", normalized, re.IGNORECASE) or re.search(r"dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$", normalized, re.IGNORECASE)
    if azure:
        organization, project, repository = map(unquote, azure.groups())
        organization_url = f"https://dev.azure.com/{organization}"
        return {"root": str(root), "provider": "azure_devops", "target": target, "identity": f"{organization_url}|{project}|{repository}", "repository": repository, "azure_organization": organization_url, "azure_project": project}
    raise BootstrapError(f"Unable to determine GitHub or Azure DevOps repository identity from origin: {remote_url}")


def copy_template(template: Path, target: Path, existing: bool) -> None:
    if not existing:
        shutil.copytree(template, target, dirs_exist_ok=True)
        return
    shutil.copytree(template / ".codex", target / ".codex")
    for name in ("requirements.md", "REQUIREMENTS-PROMPT.md"):
        destination = target / name
        if destination.exists():
            info(f"Preserved existing {name}")
        else:
            shutil.copy2(template / name, destination)
    add_section(target / ".gitignore", "BRACE", (template / ".gitignore").read_text(encoding="utf-8"))
    add_section(target / ".gitattributes", "BRACE", (template / ".gitattributes").read_text(encoding="utf-8"))
    agents = target / "AGENTS.md"
    if agents.exists():
        add_section(agents, "BRACE", (template / "AGENTS.md").read_text(encoding="utf-8"))
    else:
        shutil.copy2(template / "AGENTS.md", agents)


def bootstrap(args: argparse.Namespace) -> None:
    use_existing = bool(args.existing_repository_path)
    if use_existing and (args.project_name or args.parent_directory):
        raise BootstrapError("Existing repository path cannot be combined with project name or parent directory.")
    if not use_existing and not args.project_name and not args.parent_directory and confirm("Is this an existing Git repository?", default=False):
        use_existing = True
        args.existing_repository_path = ask("Existing repository path", default=str(Path.cwd())).strip()

    for command in ("git", "codex"):
        if not shutil.which(command):
            raise BootstrapError(f"Required command is unavailable: {command}")

    target_branch, identity = "main", None
    if use_existing:
        details = existing_details(args.existing_repository_path)
        target = Path(details["root"])
        target_branch = details["target"]
        if args.provider and args.provider != details["provider"]:
            raise BootstrapError(f"Configured provider '{args.provider}' does not match the origin remote provider '{details['provider']}'.")
        args.provider, args.project_name, identity = details["provider"], details["repository"], details["identity"]
        if args.provider == "github":
            if args.github_owner and args.github_owner != details["github_owner"]:
                raise BootstrapError("GitHub owner does not match origin.")
            args.github_owner = details["github_owner"]
        else:
            if args.azure_organization and args.azure_organization.rstrip("/") != details["azure_organization"]:
                raise BootstrapError("Azure organization does not match origin.")
            if args.azure_project and args.azure_project != details["azure_project"]:
                raise BootstrapError("Azure project does not match origin.")
            args.azure_organization, args.azure_project = details["azure_organization"], details["azure_project"]
    else:
        args.project_name = required(args.project_name, "Project name")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", args.project_name):
            raise BootstrapError("Project name contains unsupported characters.")
        args.parent_directory = required(args.parent_directory, "Local parent directory")
        args.provider = (args.provider or ask("Provider (github or azure_devops)")).strip().lower()
        if args.provider not in {"github", "azure_devops"}:
            raise BootstrapError("Provider must be github or azure_devops.")
        target = (Path(args.parent_directory).resolve() / args.project_name).resolve()
        if target.is_dir() and any(target.iterdir()):
            raise BootstrapError(f"Destination is not empty: {target}")
        target.mkdir(parents=True, exist_ok=True)

    if args.provider == "github":
        run_native("gh", ["auth", "status"])
        if not use_existing:
            args.github_owner = args.github_owner or run_native("gh", ["api", "user", "--jq", ".login"]).output.strip()
            identity = f"{args.github_owner}/{args.project_name}"
            existing = run_native("gh", ["repo", "view", identity, "--json", "nameWithOwner"], allowed_exit_codes=None)
            if existing.returncode == 0:
                raise BootstrapError(f"GitHub repository already exists: {identity}")
            if existing.returncode != 1:
                raise BootstrapError("Unable to check whether the GitHub repository already exists.")
    else:
        run_native("az", ["account", "show", "--output", "none"])
        run_native("az", ["extension", "show", "--name", "azure-devops", "--output", "none"])
        if not use_existing:
            args.azure_organization = required(args.azure_organization, "Azure DevOps organization URL")
            args.azure_project = required(args.azure_project, "Azure DevOps project")
            identity = f"{args.azure_organization}|{args.azure_project}|{args.project_name}"
            existing = run_native("az", ["repos", "show", "--organization", args.azure_organization, "--project", args.azure_project, "--repository", args.project_name, "--output", "none"], allowed_exit_codes=None)
            if existing.returncode == 0:
                raise BootstrapError(f"Azure DevOps repository already exists: {args.project_name}")

    succeeded = False
    try:
        copy_template(bundled_template(), target, use_existing)
        workflow_path = target / ".codex" / "workflow.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow.update(provider=args.provider, targetBranch=target_branch, maximumConcurrentBuilders=args.maximum_concurrent_builders, maximumConcurrentFixers=args.maximum_concurrent_fixers, worktreeRoot=str(Path(args.worktree_root).resolve()) if args.worktree_root else None)
        if args.provider == "github":
            workflow["github"]["repository"] = identity
        else:
            workflow["azureDevOps"].update(organization=args.azure_organization, project=args.azure_project, repository=args.project_name)
        workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

        if not use_existing:
            run_native("git", ["init", "-b", target_branch], target)
        if args.git_user_name:
            run_native("git", ["config", "user.name", args.git_user_name.strip()], target)
        if args.git_user_email:
            run_native("git", ["config", "user.email", args.git_user_email.strip()], target)
        if not run_native("git", ["config", "user.name"], target, allowed_exit_codes=(0, 1)).output.strip() or not run_native("git", ["config", "user.email"], target, allowed_exit_codes=(0, 1)).output.strip():
            raise BootstrapError("Git user.name and user.email must be configured before bootstrap.")

        run_native("git", ["add", "--force", "--", ".codex"], target)
        run_native("git", ["add", "--", ".gitignore", ".gitattributes", "AGENTS.md", "requirements.md", "REQUIREMENTS-PROMPT.md"], target)
        staged = [line for line in run_native("git", ["diff", "--cached", "--name-only"], target).lines if line]
        unexpected = [path for path in staged if not re.match(r"^(\.codex/|\.gitignore$|\.gitattributes$|AGENTS\.md$|requirements\.md$|REQUIREMENTS-PROMPT\.md$)", path)]
        if unexpected:
            raise BootstrapError("Bootstrap staged unexpected paths: " + ", ".join(unexpected))
        if not staged or ".codex/workflow.json" not in staged:
            raise BootstrapError("Bootstrap did not stage the required workflow payload.")
        info("FILES TO COMMIT:\n  " + "\n  ".join(staged))
        run_native("git", ["commit", "-m", "Install Brace workflow" if use_existing else "Initialize Brace project"], target)

        if use_existing:
            run_native("git", ["push", "origin", target_branch], target)
        elif args.provider == "github":
            run_native("gh", ["repo", "create", identity, f"--{args.visibility}", "--source", target, "--remote", "origin", "--push"], target)
        else:
            created = json.loads(run_native("az", ["repos", "create", "--organization", args.azure_organization, "--project", args.azure_project, "--name", args.project_name, "--output", "json"]).output)
            if not created.get("remoteUrl"):
                raise BootstrapError("Azure DevOps did not return a repository remote URL.")
            run_native("git", ["remote", "add", "origin", created["remoteUrl"]], target)
            run_native("git", ["push", "--set-upstream", "origin", target_branch], target)

        run_native("git", ["fetch", "origin"], target)
        local_sha = run_native("git", ["rev-parse", "HEAD"], target).output.strip()
        if local_sha != run_native("git", ["rev-parse", f"origin/{target_branch}"], target).output.strip():
            raise BootstrapError(f"Remote {target_branch} does not match the bootstrap commit.")
        paths = initialize_state_files(target, get_configuration(target))
        state = read_json(paths.state, paths.schemas / "state.schema.json")
        if state["repository"] != identity:
            raise BootstrapError("Initialized state does not match the remote repository.")
        succeeded = True
        success(f"\n{'BRACE INSTALLED' if use_existing else 'BRACE PROJECT CREATED'}\nPROJECT:      {target}\nREMOTE:       {identity}\nTARGET:       {target_branch}\nINITIAL SHA:  {local_sha}\nNEXT:         Complete requirements.md, then run brace plan.")
    finally:
        if not succeeded:
            warning(f"Bootstrap failed. The destination was preserved: {target}")


def add_arguments(result: argparse.ArgumentParser) -> None:
    result.add_argument("--existing-repository-path")
    result.add_argument("--project-name")
    result.add_argument("--parent-directory")
    result.add_argument("--provider", choices=("github", "azure_devops"))
    result.add_argument("--visibility", choices=("private", "internal", "public"), default="private")
    result.add_argument("--github-owner")
    result.add_argument("--azure-organization")
    result.add_argument("--azure-project")
    result.add_argument("--maximum-concurrent-builders", type=int, choices=range(1, 33), default=3)
    result.add_argument("--maximum-concurrent-fixers", type=int, choices=range(1, 33), default=3)
    result.add_argument("--worktree-root")
    result.add_argument("--git-user-name")
    result.add_argument("--git-user-email")
