"""
GITGUI-003 — Git repo service.
Clones/pulls a gitops repo on first use; provides branch + file helpers.
All writes go via feature branches — no direct commits to main.
SSH key auth via mounted Kubernetes secret (path: GITOPS_SSH_KEY_PATH).

Each GitService instance is independent — constructor accepts repo_url and
local_path overrides so multiple repos can be operated concurrently.
Default constructor targets GITOPS_REPO_URL (the platform management repo).
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
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Local dev flags — set to "1" to skip remote git/GitHub operations
SKIP_INIT = os.environ.get("GITOPS_SKIP_INIT", "") == "1"
SKIP_PUSH = os.environ.get("GITOPS_SKIP_PUSH", "") == "1"

GIT_AUTHOR_NAME = "GitOpsAPI"
GIT_AUTHOR_EMAIL = "gitopsapi@gitopsgui"


def _ssh_env() -> dict:
    return {"GIT_SSH_COMMAND": f"ssh -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no"}


_HTTPS_PREFIX = "https://"


def _auth_url(url: str) -> str:
    """Inject GITHUB_TOKEN into HTTPS URLs for git authentication."""
    if GITHUB_TOKEN and url.startswith(_HTTPS_PREFIX) and "@" not in url[8:]:
        return url.replace(_HTTPS_PREFIX, f"{_HTTPS_PREFIX}{GITHUB_TOKEN}@", 1)
    return url


def _git_env(url: str) -> dict:
    """Return git auth environment. HTTPS URLs use embedded credentials; SSH uses key."""
    if url.startswith(_HTTPS_PREFIX):
        return {}
    return _ssh_env()


class GitService:
    def __init__(
        self,
        repo_url: Optional[str] = None,
        local_path: Optional[Path] = None,
    ):
        """Create a GitService for a specific repo.

        repo_url: SSH or HTTPS URL of the git repo. Defaults to GITOPS_REPO_URL.
        local_path: Where to clone the repo locally. Defaults to GITOPS_LOCAL_PATH.

        Each instance is independent — multiple instances can target different repos.
        Repos are cloned lazily on first read/write (or eagerly via init()).
        """
        self._repo_url = REPO_URL if repo_url is None else repo_url
        self._local_path = local_path or REPO_LOCAL_PATH
        self._git_repo: Optional[git.Repo] = None

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Eagerly clone or pull. Called by lifespan for the default management repo."""
        await asyncio.to_thread(self._sync_init)

    def _sync_init(self) -> None:
        if SKIP_INIT and self._local_path.exists() and (self._local_path / ".git").exists():
            self._git_repo = git.Repo(str(self._local_path))
            return
        env = _git_env(self._repo_url)
        if self._local_path.exists() and (self._local_path / ".git").exists():
            repo = git.Repo(str(self._local_path))
            repo.remotes.origin.pull(REPO_BRANCH, env=env)
        else:
            self._local_path.parent.mkdir(parents=True, exist_ok=True)
            repo = git.Repo.clone_from(
                _auth_url(self._repo_url),
                str(self._local_path),
                branch=REPO_BRANCH,
                env=env,
            )
        self._git_repo = repo

    def _get_repo(self) -> git.Repo:
        """Lazily initialise and return the git.Repo instance."""
        if self._git_repo is None:
            if not self._repo_url and not (
                SKIP_INIT
                and self._local_path.exists()
                and (self._local_path / ".git").exists()
            ):
                raise RuntimeError(
                    "GitService not initialised: no repo_url configured. "
                    "Set GITOPS_REPO_URL or pass repo_url to the constructor."
                )
            self._sync_init()
        return self._git_repo

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def read_file(self, path: str) -> str:
        full_path = self._local_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found in repo: {path}")
        return full_path.read_text()

    async def list_dir(self, path: str) -> list[str]:
        """Return immediate subdirectory names under path."""
        full_path = self._local_path / path
        if not full_path.exists():
            return []
        return [p.name for p in full_path.iterdir() if p.is_dir()]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_branch(self, branch_name: str) -> None:
        """Create and check out a new feature branch from main."""
        def _run():
            repo = self._get_repo()
            env = _git_env(self._repo_url)
            if not SKIP_INIT:
                repo.git.fetch("origin", env=env)
                repo.git.pull("origin", REPO_BRANCH, env=env)
            repo.git.checkout(REPO_BRANCH)
            repo.git.checkout("-b", branch_name)
        await asyncio.to_thread(_run)

    async def write_file(self, path: str, content: str) -> None:
        """Write content to a file on the current branch and stage it."""
        full_path = self._local_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        repo = self._get_repo()
        repo.index.add([str(full_path.relative_to(self._local_path))])

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
        if SKIP_PUSH:
            return
        def _run():
            repo = self._get_repo()
            branch = repo.active_branch.name
            env = _git_env(self._repo_url)
            repo.remotes.origin.push(
                refspec=f"{branch}:{branch}",
                **({"env": env} if env else {}),
            )
        await asyncio.to_thread(_run)

    async def delete_file(self, path: str) -> None:
        """Remove a file from the working tree and stage the deletion."""
        full_path = self._local_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found in repo: {path}")
        full_path.unlink()
        repo = self._get_repo()
        repo.index.remove([str(full_path.relative_to(self._local_path))])

    async def checkout_main(self) -> None:
        """Return to main and pull (call after PR is merged)."""
        def _run():
            repo = self._get_repo()
            repo.git.checkout(REPO_BRANCH)
            repo.git.pull("origin", REPO_BRANCH, env=_git_env(self._repo_url))
        await asyncio.to_thread(_run)
