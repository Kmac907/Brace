from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from brace.bootstrap import bundled_template


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def task(self, identity: str = "TASK-0001", dependencies: list[str] | None = None, paths: list[str] | None = None) -> dict:
        return {
            "taskId": identity, "title": identity, "description": "one session",
            "requirementIds": ["REQ-ONE"], "planSections": ["1"], "dependencies": dependencies or [],
            "allowedPaths": paths or ["src/**"], "exclusiveResources": [], "acceptanceCriteria": ["works"],
            "checks": ["python -m unittest"], "status": "pending", "attemptCount": 0, "branch": None,
            "worktree": None, "baseSha": None, "resultSha": None, "pullRequest": None,
            "lastError": None, "amendmentId": None, "supersededBy": [],
        }

    def git(self, root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
        process = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", check=False)
        if process.returncode not in allowed:
            self.fail(process.stdout + process.stderr)
        return process.stdout.strip()

    def make_repository(self) -> tuple[Path, Path, dict]:
        root, remote = self.base / "working", self.base / "owner" / "repo.git"
        root.mkdir()
        remote.parent.mkdir()
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Test")
        self.git(root, "config", "user.email", "test@example.invalid")
        shutil.copytree(bundled_template() / ".codex" / "schemas", root / ".codex" / "schemas")
        (root / ".codex" / "prompts").mkdir()
        (root / ".codex" / "logs").mkdir()
        (root / ".codex" / "AGENTS.md").write_text("test", encoding="utf-8")
        config = {
            "schemaVersion": "1.0", "provider": "github", "remote": "origin", "targetBranch": "main",
            "integrationBranch": "brace/integration", "deleteMergedBranches": True,
            "worktreeRoot": str(self.base / "worktrees"), "maximumConcurrentBuilders": 2,
            "maximumConcurrentFixers": 2, "maximumTaskAttempts": 3, "maximumBugAttempts": 3,
            "maximumPlanningQuestionRounds": 3, "maximumAmendmentRounds": 3,
            "agentTimeoutMinutes": 1, "agentCleanupGraceSeconds": 1,
            "github": {"repository": "owner/repo"},
            "azureDevOps": {"organization": "", "project": "", "repository": ""},
        }
        (root / ".codex" / "workflow.json").write_text(json.dumps(config), encoding="utf-8")
        (root / "requirements.md").write_text("# Requirements\n\nREQ-ONE\n", encoding="utf-8")
        (root / "plan.md").write_text("# Plan\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "initial")
        self.git(root, "remote", "add", "origin", str(remote))
        self.git(root, "push", "-u", "origin", "main")
        self.git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        return root, remote, config
