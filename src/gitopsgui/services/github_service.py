"""
GITGUI-004 — GitHub PR service.
Creates and manages PRs via the GitHub API (PyGitHub).
Labels follow the convention: resource type (cluster/application/pipeline/promotion)
and target stage (stage:dev / stage:ete / stage:production).

When GITOPS_SKIP_GITHUB=1 all operations run against a LocalPRStore backed by a
JSON file in the gitops repo directory and do real git merges locally.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

from github import Github, GithubException
from github.PullRequest import PullRequest as GHPullRequest

from ..models.pr import PRDetail, ReviewerStatus

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # e.g. "org/cluster09-gitops"

# Local dev flag — use LocalPRStore instead of calling GitHub API
SKIP_GITHUB = os.environ.get("GITOPS_SKIP_GITHUB", "") == "1"

_REPO_LOCAL_PATH = Path(os.environ.get("GITOPS_LOCAL_PATH", "/tmp/gitops-repo"))
_LOCAL_PR_STORE_PATH = _REPO_LOCAL_PATH / ".local-prs.json"
_GITOPS_BRANCH = os.environ.get("GITOPS_BRANCH", "main")

# Maps stage label → required reviewer roles
_STAGE_REQUIRED_ROLES = {
    "dev":        ["build_manager"],
    "ete":        ["build_manager"],
    "production": ["build_manager", "cluster_operator"],
}


# ---------------------------------------------------------------------------
# Local PR store — used when GITOPS_SKIP_GITHUB=1
# ---------------------------------------------------------------------------

class LocalPRStore:
    """File-backed PR state for local E2E testing against the workspace repo."""

    @staticmethod
    def _load() -> dict:
        if _LOCAL_PR_STORE_PATH.exists():
            return json.loads(_LOCAL_PR_STORE_PATH.read_text())
        return {"next_id": 1, "prs": {}}

    @staticmethod
    def _save(data: dict) -> None:
        _LOCAL_PR_STORE_PATH.write_text(json.dumps(data, indent=2))

    @classmethod
    def create(cls, branch: str, title: str, body: str, labels: List[str]) -> dict:
        data = cls._load()
        pr_id = data["next_id"]
        data["next_id"] += 1
        stage = next((lb.split(":", 1)[1] for lb in labels if lb.startswith("stage:")), None)
        resource_type = next((lb for lb in labels if lb in {"cluster", "application", "pipeline", "promotion"}), None)
        pr = {
            "number": pr_id,
            "branch": branch,
            "base": _GITOPS_BRANCH,
            "title": title,
            "body": body,
            "labels": labels,
            "stage": stage,
            "resource_type": resource_type,
            "state": "open",
            "approvals": [],
            "pr_url": f"[local] PR #{pr_id}: {title}",
        }
        data["prs"][str(pr_id)] = pr
        cls._save(data)
        return pr

    @classmethod
    def get(cls, pr_number: int) -> Optional[dict]:
        data = cls._load()
        return data["prs"].get(str(pr_number))

    @classmethod
    def list_all(cls, state: str = "open", label: Optional[str] = None) -> List[dict]:
        data = cls._load()
        prs = list(data["prs"].values())
        if state != "all":
            prs = [p for p in prs if p["state"] == state]
        if label:
            prs = [p for p in prs if label in p.get("labels", [])]
        return prs

    @classmethod
    def approve(cls, pr_number: int, username: str) -> None:
        data = cls._load()
        pr = data["prs"].get(str(pr_number))
        if pr and username not in pr["approvals"]:
            pr["approvals"].append(username)
            cls._save(data)

    @classmethod
    def merge(cls, pr_number: int) -> str:
        """Squash-merge the PR branch into base; returns the merge commit SHA."""
        import git as gitlib
        data = cls._load()
        pr = data["prs"].get(str(pr_number))
        if not pr:
            raise ValueError(f"Local PR #{pr_number} not found")
        repo = gitlib.Repo(str(_REPO_LOCAL_PATH))
        repo.git.checkout(pr["base"])
        repo.git.merge(pr["branch"], "--squash")
        commit = repo.index.commit(
            f"Squash merge PR #{pr_number}: {pr['title']}",
            author=gitlib.Actor("GitOpsAPI", "gitopsapi@gitopsgui"),
            committer=gitlib.Actor("GitOpsAPI", "gitopsapi@gitopsgui"),
        )
        pr["state"] = "merged"
        cls._save(data)
        return commit.hexsha

    @classmethod
    def tag(cls, commit_sha: str, tag: str) -> None:
        import git as gitlib
        repo = gitlib.Repo(str(_REPO_LOCAL_PATH))
        repo.create_tag(tag, ref=commit_sha)


def _local_pr_to_detail(pr: dict) -> PRDetail:
    required = _STAGE_REQUIRED_ROLES.get(pr.get("stage") or "", [])
    approvals = pr.get("approvals", [])
    reviews = [ReviewerStatus(login=u, role="unknown", approved=True) for u in approvals]
    return PRDetail(
        pr_number=pr["number"],
        title=pr["title"],
        state=pr["state"],
        labels=pr.get("labels", []),
        stage=pr.get("stage"),
        resource_type=pr.get("resource_type"),
        diff_url=f"[local] diff for branch {pr['branch']}",
        reviews=reviews,
        required_approvers=required,
        approvals_satisfied=len(approvals) >= max(len(required), 1),
        pr_url=pr["pr_url"],
    )


# ---------------------------------------------------------------------------
# GitHub helpers (real API path)
# ---------------------------------------------------------------------------

def _client() -> Github:
    return Github(GITHUB_TOKEN)


def _extract_stage(labels: List[str]) -> Optional[str]:
    for label in labels:
        if label.startswith("stage:"):
            return label.split(":", 1)[1]
    return None


def _extract_resource_type(labels: List[str]) -> Optional[str]:
    for label in labels:
        if label in {"cluster", "application", "pipeline", "promotion"}:
            return label
    return None


def _map_pr(pr: GHPullRequest) -> PRDetail:
    label_names = [lb.name for lb in pr.labels]
    stage = _extract_stage(label_names)
    reviews = pr.get_reviews()
    reviewer_statuses = [
        ReviewerStatus(
            login=r.user.login,
            role="unknown",
            approved=r.state == "APPROVED",
        )
        for r in reviews
    ]
    approvals = sum(1 for r in reviewer_statuses if r.approved)
    required = _STAGE_REQUIRED_ROLES.get(stage or "", [])
    return PRDetail(
        pr_number=pr.number,
        title=pr.title,
        state=pr.state,
        labels=label_names,
        stage=stage,
        resource_type=_extract_resource_type(label_names),
        diff_url=pr.diff_url,
        reviews=reviewer_statuses,
        required_approvers=required,
        approvals_satisfied=pr.mergeable and approvals >= len(required),
        pr_url=pr.html_url,
    )


GITHUB_ORG = os.environ.get("GITHUB_ORG", "")  # e.g. "MoTTTT"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class GitHubService:
    def __init__(self, repo_name: Optional[str] = None):
        """Create a GitHubService targeting a specific repo.

        repo_name: "owner/repo" string. Defaults to GITHUB_REPO env var.
        Per-cluster instances pass e.g. "MoTTTT/security-apps".
        """
        self._repo_name = repo_name or GITHUB_REPO

    def _repo(self):
        return _client().get_repo(self._repo_name)

    def _owner(self) -> str:
        """Org or user that owns managed repos — GITHUB_ORG if set, else derived from GITHUB_REPO."""
        if GITHUB_ORG:
            return GITHUB_ORG
        return GITHUB_REPO.split("/")[0] if "/" in GITHUB_REPO else GITHUB_REPO

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = True,
    ) -> str:
        """Create a private repository under the platform org/user. Returns the SSH clone URL.

        Idempotent: if the repo already exists and is private, returns its SSH URL without error.
        Raises RuntimeError if the repo exists but is public (TR-032 violation).
        """
        if SKIP_GITHUB:
            return f"git@github.com:{self._owner()}/{name}.git"

        def _run() -> str:
            gh = _client()
            owner = self._owner()
            # Check if repo already exists
            try:
                existing = gh.get_repo(f"{owner}/{name}")
                if not existing.private:
                    raise RuntimeError(
                        f"Repository {owner}/{name} already exists but is public — "
                        f"TR-032 requires all platform-managed repos to be private."
                    )
                return existing.clone_url
            except GithubException as exc:
                if exc.status != 404:
                    raise
            # Create new repo under org or user
            try:
                org = gh.get_organization(owner)
                repo = org.create_repo(name, description=description, private=True, auto_init=True)
            except GithubException:
                # owner is a user (not an org)
                user = gh.get_user()
                repo = user.create_repo(name, description=description, private=True, auto_init=True)
            return repo.clone_url

        return await asyncio.to_thread(_run)

    async def add_deploy_key(
        self,
        repo_name: str,
        title: str,
        public_key: str,
        read_only: bool = False,
    ) -> int:
        """Register an SSH public key as a deploy key on owner/repo_name. Returns the key ID.

        read_only=False grants write access (Flux requires this to update status annotations).
        """
        if SKIP_GITHUB:
            return 0  # stub key ID for local dev

        def _run() -> int:
            gh = _client()
            repo = gh.get_repo(f"{self._owner()}/{repo_name}")
            key = repo.create_key(title=title, key=public_key, read_only=read_only)
            return key.id

        return await asyncio.to_thread(_run)

    async def delete_deploy_key(self, repo_name: str, key_id: int) -> None:
        """Revoke a deploy key by ID — called on cluster/repo decommission (TR-038)."""
        if SKIP_GITHUB:
            return

        def _run():
            gh = _client()
            repo = gh.get_repo(f"{self._owner()}/{repo_name}")
            repo.get_key(key_id).delete()

        await asyncio.to_thread(_run)

    async def create_pr(
        self,
        branch: str,
        title: str,
        body: str,
        labels: List[str],
        reviewers: List[str],
    ) -> str:
        """Open a PR against main; returns the PR HTML URL."""
        if SKIP_GITHUB:
            pr = LocalPRStore.create(branch, title, body, labels)
            return pr["pr_url"]
        def _run() -> str:
            repo = self._repo()
            pr = repo.create_pull(
                title=title,
                body=body,
                head=branch,
                base=os.environ.get("GITOPS_BRANCH", "main"),
            )
            for label in labels:
                try:
                    pr.add_to_labels(label)
                except GithubException:
                    pass  # label may not exist yet; non-fatal
            if reviewers:
                pr.create_review_request(reviewers=reviewers)
            return pr.html_url
        return await asyncio.to_thread(_run)

    async def list_prs(self, state: str = "open", label: Optional[str] = None) -> List[PRDetail]:
        if SKIP_GITHUB:
            return [_local_pr_to_detail(p) for p in LocalPRStore.list_all(state, label)]
        def _run() -> List[PRDetail]:
            repo = self._repo()
            pulls = repo.get_pulls(state=state, sort="updated", direction="desc")
            results = []
            for pr in pulls:
                label_names = [lb.name for lb in pr.labels]
                if label and label not in label_names:
                    continue
                results.append(_map_pr(pr))
            return results
        return await asyncio.to_thread(_run)

    async def get_pr(self, pr_number: int) -> Optional[PRDetail]:
        if SKIP_GITHUB:
            pr = LocalPRStore.get(pr_number)
            return _local_pr_to_detail(pr) if pr else None
        def _run() -> Optional[PRDetail]:
            try:
                pr = self._repo().get_pull(pr_number)
                return _map_pr(pr)
            except GithubException:
                return None
        return await asyncio.to_thread(_run)

    async def approve_pr(self, pr_number: int, username: str) -> None:
        if SKIP_GITHUB:
            LocalPRStore.approve(pr_number, username)
            return
        def _run():
            pr = self._repo().get_pull(pr_number)
            pr.create_review(event="APPROVE", body=f"Approved via GitOpsGUI by {username}")
        await asyncio.to_thread(_run)

    async def merge_pr(self, pr_number: int) -> None:
        if SKIP_GITHUB:
            LocalPRStore.merge(pr_number)
            return
        def _run():
            pr = self._repo().get_pull(pr_number)
            pr.merge(merge_method="squash")
        await asyncio.to_thread(_run)

    async def archive_repo(self, repo_name: str) -> None:
        """Mark owner/repo_name as archived (read-only). Preserves full history.

        Idempotent: no-op if the repo is already archived.
        """
        if SKIP_GITHUB:
            return

        def _run():
            gh = _client()
            repo = gh.get_repo(f"{self._owner()}/{repo_name}")
            if not repo.archived:
                repo.edit(archived=True)

        await asyncio.to_thread(_run)

    async def tag_deployment(self, commit_sha: str, tag: str) -> None:
        """Apply a lightweight git tag to the merge commit for deployment provenance."""
        if SKIP_GITHUB:
            LocalPRStore.tag(commit_sha, tag)
            return
        def _run():
            repo = self._repo()
            repo.create_git_ref(ref=f"refs/tags/{tag}", sha=commit_sha)
        await asyncio.to_thread(_run)
