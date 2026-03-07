"""
GITGUI-004 — GitHub PR service.
Creates and manages PRs via the GitHub API (PyGitHub).
Labels follow the convention: resource type (cluster/application/pipeline/promotion)
and target stage (stage:dev / stage:ete / stage:production).
"""

import asyncio
import os
from typing import List, Optional

from github import Github, GithubException
from github.PullRequest import PullRequest as GHPullRequest

from ..models.pr import PRDetail, ReviewerStatus

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # e.g. "org/cluster09-gitops"

# Maps stage label → required reviewer roles (for display/UX; forge enforces at branch protection level)
_STAGE_REQUIRED_ROLES = {
    "dev":        ["build_manager"],
    "ete":        ["build_manager"],
    "production": ["build_manager", "cluster_operator"],
}


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
            role="unknown",  # role resolved from Keycloak; not available via GitHub API
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


class GitHubService:
    def _repo(self):
        return _client().get_repo(GITHUB_REPO)

    async def create_pr(
        self,
        branch: str,
        title: str,
        body: str,
        labels: List[str],
        reviewers: List[str],
    ) -> str:
        """Open a PR against main; returns the PR HTML URL."""
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
        def _run() -> Optional[PRDetail]:
            try:
                pr = self._repo().get_pull(pr_number)
                return _map_pr(pr)
            except GithubException:
                return None
        return await asyncio.to_thread(_run)

    async def approve_pr(self, pr_number: int, username: str) -> None:
        def _run():
            pr = self._repo().get_pull(pr_number)
            pr.create_review(event="APPROVE", body=f"Approved via GitOpsGUI by {username}")
        await asyncio.to_thread(_run)

    async def merge_pr(self, pr_number: int) -> None:
        def _run():
            pr = self._repo().get_pull(pr_number)
            pr.merge(merge_method="squash")
        await asyncio.to_thread(_run)

    async def tag_deployment(self, commit_sha: str, tag: str) -> None:
        """Apply a lightweight git tag to the merge commit for deployment provenance."""
        def _run():
            repo = self._repo()
            repo.create_git_ref(ref=f"refs/tags/{tag}", sha=commit_sha)
        await asyncio.to_thread(_run)
