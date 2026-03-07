"""
GITGUI-003 — Git repo service.
Clones/pulls the gitops repo on startup; provides branch + file helpers.
All writes go via feature branches — no direct commits to main.
SSH key auth via mounted Kubernetes secret (path: GITOPS_SSH_KEY_PATH).
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

import git  # gitpython

REPO_URL = os.environ.get("GITOPS_REPO_URL", "")
REPO_BRANCH = os.environ.get("GITOPS_BRANCH", "main")
REPO_LOCAL_PATH = Path(os.environ.get("GITOPS_LOCAL_PATH", "/tmp/gitops-repo"))
SSH_KEY_PATH = os.environ.get("GITOPS_SSH_KEY_PATH", "/etc/gitops-ssh/id_rsa")

GIT_AUTHOR_NAME = "GitOpsAPI"
GIT_AUTHOR_EMAIL = "gitopsapi@gitopsgui"


def _ssh_env() -> dict:
    return {"GIT_SSH_COMMAND": f"ssh -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no"}


class GitService:
    _repo: Optional[git.Repo] = None

    async def init(self) -> None:
        """Clone or pull the gitops repo on startup."""
        await asyncio.to_thread(self._sync_init)

    def _sync_init(self) -> None:
        env = _ssh_env()
        if REPO_LOCAL_PATH.exists() and (REPO_LOCAL_PATH / ".git").exists():
            repo = git.Repo(str(REPO_LOCAL_PATH))
            repo.remotes.origin.pull(REPO_BRANCH, env=env)
        else:
            REPO_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            repo = git.Repo.clone_from(
                REPO_URL,
                str(REPO_LOCAL_PATH),
                branch=REPO_BRANCH,
                env=env,
            )
        GitService._repo = repo

    def _get_repo(self) -> git.Repo:
        if GitService._repo is None:
            raise RuntimeError("GitService not initialised — call init() first")
        return GitService._repo

    async def read_file(self, path: str) -> str:
        full_path = REPO_LOCAL_PATH / path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found in repo: {path}")
        return full_path.read_text()

    async def list_dir(self, path: str) -> list[str]:
        """Return immediate subdirectory names under path."""
        full_path = REPO_LOCAL_PATH / path
        if not full_path.exists():
            return []
        return [p.name for p in full_path.iterdir() if p.is_dir()]

    async def create_branch(self, branch_name: str) -> None:
        """Create and check out a new feature branch from main."""
        def _run():
            repo = self._get_repo()
            repo.git.fetch("origin", env=_ssh_env())
            repo.git.checkout(REPO_BRANCH)
            repo.git.pull("origin", REPO_BRANCH, env=_ssh_env())
            repo.git.checkout("-b", branch_name)
        await asyncio.to_thread(_run)

    async def write_file(self, path: str, content: str) -> None:
        """Write content to a file on the current branch and stage it."""
        full_path = REPO_LOCAL_PATH / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        repo = self._get_repo()
        repo.index.add([str(full_path.relative_to(REPO_LOCAL_PATH))])

    async def commit(self, message: str) -> str:
        """Commit staged changes; returns the commit SHA."""
        def _run() -> str:
            repo = self._get_repo()
            c = repo.index.commit(
                message,
                author=git.Actor(GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL),
                committer=git.Actor(GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL),
            )
            return c.hexsha
        return await asyncio.to_thread(_run)

    async def push(self) -> None:
        """Push the current branch to origin."""
        def _run():
            repo = self._get_repo()
            branch = repo.active_branch.name
            repo.remotes.origin.push(
                refspec=f"{branch}:{branch}",
                env=_ssh_env(),
            )
        await asyncio.to_thread(_run)

    async def checkout_main(self) -> None:
        """Return to main and pull (call after PR is merged)."""
        def _run():
            repo = self._get_repo()
            repo.git.checkout(REPO_BRANCH)
            repo.git.pull("origin", REPO_BRANCH, env=_ssh_env())
        await asyncio.to_thread(_run)
