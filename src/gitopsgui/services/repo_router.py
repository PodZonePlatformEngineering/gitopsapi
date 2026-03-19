"""
Cluster → repository routing.

Derives the correct GitService / GitHubService instance for a cluster's
`{cluster}-apps` or `{cluster}-infra` repository.

Repo URL convention:
  {cluster}-apps  → git@github.com:{GITHUB_ORG}/{cluster}-apps.git
  {cluster}-infra → git@github.com:{GITHUB_ORG}/{cluster}-infra.git

Local clone paths:
  {cluster}-apps  → /tmp/gitops-repos/{cluster}-apps/
  {cluster}-infra → /tmp/gitops-repos/{cluster}-infra/
"""

import os
from pathlib import Path


_GITOPS_REPOS_BASE = Path(os.environ.get("GITOPS_REPOS_BASE", "/tmp/gitops-repos"))


def _owner() -> str:
    from .github_service import GITHUB_ORG, GITHUB_REPO
    if GITHUB_ORG:
        return GITHUB_ORG
    return GITHUB_REPO.split("/")[0] if "/" in GITHUB_REPO else GITHUB_REPO


def apps_repo_name(cluster: str) -> str:
    return f"{cluster}-apps"


def infra_repo_name(cluster: str) -> str:
    return f"{cluster}-infra"


def apps_repo_url(cluster: str) -> str:
    return f"https://github.com/{_owner()}/{apps_repo_name(cluster)}.git"


def infra_repo_url(cluster: str) -> str:
    return f"https://github.com/{_owner()}/{infra_repo_name(cluster)}.git"


def git_for_apps(cluster: str):
    from .git_service import GitService
    return GitService(
        repo_url=apps_repo_url(cluster),
        local_path=_GITOPS_REPOS_BASE / apps_repo_name(cluster),
    )


def git_for_infra(cluster: str):
    from .git_service import GitService
    return GitService(
        repo_url=infra_repo_url(cluster),
        local_path=_GITOPS_REPOS_BASE / infra_repo_name(cluster),
    )


def github_for_apps(cluster: str):
    from .github_service import GitHubService
    return GitHubService(repo_name=f"{_owner()}/{apps_repo_name(cluster)}")


def github_for_infra(cluster: str):
    from .github_service import GitHubService
    return GitHubService(repo_name=f"{_owner()}/{infra_repo_name(cluster)}")
