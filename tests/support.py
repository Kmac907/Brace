from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from brace.bootstrap import bundled_template


_seed_temporary = tempfile.TemporaryDirectory()
_seed = Path(_seed_temporary.name) / "repository.git"
_worktree_root = Path(_seed_temporary.name) / "worktrees"
_real_which = shutil.which


@lru_cache
def _cached_which(command: str, mode: int, path: str | None, path_value: str | None, path_ext: str | None, cwd: str) -> str | None:
    return _real_which(command, mode, path)


def _which(command: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
    return _cached_which(command, mode, path, os.environ.get("PATH"), os.environ.get("PATHEXT"), os.getcwd())


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.which_patch = patch.object(shutil, "which", _which)
        self.which_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repository_roots: list[Path] = []

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
            for root in self.repository_roots:
                repository_id = sha256(str(root.resolve()).upper().encode("utf-8")).hexdigest()[:16]
                worktrees = _worktree_root / repository_id
                if worktrees.is_dir():
                    shutil.rmtree(worktrees)
            if _worktree_root.is_dir() and not any(_worktree_root.iterdir()):
                _worktree_root.rmdir()
        finally:
            self.which_patch.stop()

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
        remote.parent.mkdir()
        if not _seed.is_dir():
            seed_root = _seed.with_name("working")
            seed_root.mkdir()
            self.git(seed_root, "init", "-b", "main")
            self.git(seed_root, "config", "user.name", "Test")
            self.git(seed_root, "config", "user.email", "test@example.invalid")
            shutil.copytree(bundled_template() / ".codex", seed_root / ".codex")
            (seed_root / ".codex" / "logs").mkdir()
            shutil.copy2(bundled_template() / ".gitignore", seed_root / ".gitignore")
            self._write_seed_files(seed_root, "# Plan\n")
            self.git(seed_root, "add", ".")
            self.git(seed_root, "commit", "-m", "initial")
            (seed_root / "plan.md").write_text("# Plan\n\nImplement REQ-ONE.\n", encoding="utf-8")
            self.git(seed_root, "add", "plan.md")
            self.git(seed_root, "commit", "-m", "plan")
            subprocess.run(["git", "clone", "--bare", "--local", str(seed_root), str(_seed)], check=True, capture_output=True)
        shutil.copytree(_seed, remote)
        subprocess.run([
            "git", "clone", "--local", "--config", "user.name=Test", "--config",
            "user.email=test@example.invalid", str(remote), str(root),
        ], check=True, capture_output=True)
        self.repository_roots.append(root)
        (root / ".codex" / "prompts").mkdir(exist_ok=True)
        (root / ".codex" / "logs").mkdir(exist_ok=True)
        config = json.loads((root / ".codex" / "workflow.json").read_text(encoding="utf-8"))
        return root, remote, config

    @staticmethod
    def _write_seed_files(root: Path, plan: str) -> None:
        config = {
            "schemaVersion": "1.0", "provider": "github", "remote": "origin", "targetBranch": "main",
            "integrationBranch": "brace/integration", "deleteMergedBranches": True,
            "worktreeRoot": str(_worktree_root), "maximumConcurrentBuilders": 2,
            "maximumConcurrentFixers": 2, "maximumTaskAttempts": 3, "maximumBugAttempts": 3,
            "maximumPlanningQuestionRounds": 3, "maximumAmendmentRounds": 3,
            "agentTimeoutMinutes": 1, "agentCleanupGraceSeconds": 1,
            "github": {"repository": "owner/repo"},
            "azureDevOps": {"organization": "", "project": "", "repository": ""},
        }
        (root / ".codex" / "workflow.json").write_text(json.dumps(config), encoding="utf-8")
        (root / "requirements.md").write_text("# Requirements\n\nREQ-ONE\n", encoding="utf-8")
        (root / "plan.md").write_text(plan, encoding="utf-8")
