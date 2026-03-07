"""
Unit tests for GitHubService — mocks PyGitHub so no real API calls are made.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from gitopsgui.services.github_service import (
    GitHubService,
    _extract_stage,
    _extract_resource_type,
)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def test_extract_stage_finds_label():
    assert _extract_stage(["cluster", "stage:production"]) == "production"


def test_extract_stage_returns_none_when_absent():
    assert _extract_stage(["cluster", "promotion"]) is None


def test_extract_resource_type_cluster():
    assert _extract_resource_type(["cluster", "stage:production"]) == "cluster"


def test_extract_resource_type_promotion():
    assert _extract_resource_type(["promotion", "stage:ete"]) == "promotion"


def test_extract_resource_type_none():
    assert _extract_resource_type(["stage:dev"]) is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mock_label(name: str) -> MagicMock:
    lb = MagicMock()
    lb.name = name
    return lb


def _mock_review(login: str, state: str) -> MagicMock:
    r = MagicMock()
    r.user.login = login
    r.state = state
    return r


def _mock_pr(
    number: int = 1,
    title: str = "Test PR",
    state: str = "open",
    labels=None,
    reviews=None,
    html_url: str = "https://github.com/test/repo/pull/1",
    diff_url: str = "https://github.com/test/repo/pull/1.diff",
    mergeable: bool = True,
) -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.state = state
    pr.labels = [_mock_label(lb) for lb in (labels or [])]
    pr.get_reviews.return_value = reviews or []
    pr.html_url = html_url
    pr.diff_url = diff_url
    pr.mergeable = mergeable
    return pr


# ---------------------------------------------------------------------------
# create_pr
# ---------------------------------------------------------------------------

def test_create_pr_returns_html_url():
    svc = GitHubService()
    mock_pr = _mock_pr(html_url="https://github.com/test/repo/pull/42")
    mock_repo = MagicMock()
    mock_repo.create_pull.return_value = mock_pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        result = asyncio.get_event_loop().run_until_complete(
            svc.create_pr("my-branch", "Title", "Body", ["cluster"], [])
        )

    assert result == "https://github.com/test/repo/pull/42"
    mock_repo.create_pull.assert_called_once()


def test_create_pr_adds_labels():
    svc = GitHubService()
    mock_pr = _mock_pr()
    mock_repo = MagicMock()
    mock_repo.create_pull.return_value = mock_pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        asyncio.get_event_loop().run_until_complete(
            svc.create_pr("branch", "Title", "Body", ["cluster", "stage:production"], [])
        )

    assert mock_pr.add_to_labels.call_count == 2


def test_create_pr_requests_reviewers():
    svc = GitHubService()
    mock_pr = _mock_pr()
    mock_repo = MagicMock()
    mock_repo.create_pull.return_value = mock_pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        asyncio.get_event_loop().run_until_complete(
            svc.create_pr("branch", "Title", "Body", [], ["alice", "bob"])
        )

    mock_pr.create_review_request.assert_called_once_with(reviewers=["alice", "bob"])


def test_create_pr_skips_review_request_when_no_reviewers():
    svc = GitHubService()
    mock_pr = _mock_pr()
    mock_repo = MagicMock()
    mock_repo.create_pull.return_value = mock_pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        asyncio.get_event_loop().run_until_complete(
            svc.create_pr("branch", "Title", "Body", [], [])
        )

    mock_pr.create_review_request.assert_not_called()


# ---------------------------------------------------------------------------
# list_prs
# ---------------------------------------------------------------------------

def test_list_prs_returns_all_open():
    svc = GitHubService()
    pr1 = _mock_pr(number=1, labels=["cluster", "stage:production"])
    pr2 = _mock_pr(number=2, labels=["application", "stage:dev"])
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = [pr1, pr2]

    with patch.object(svc, "_repo", return_value=mock_repo):
        results = asyncio.get_event_loop().run_until_complete(svc.list_prs())

    assert len(results) == 2


def test_list_prs_filters_by_label():
    svc = GitHubService()
    pr1 = _mock_pr(number=1, labels=["cluster", "stage:production"])
    pr2 = _mock_pr(number=2, labels=["application", "stage:dev"])
    mock_repo = MagicMock()
    mock_repo.get_pulls.return_value = [pr1, pr2]

    with patch.object(svc, "_repo", return_value=mock_repo):
        results = asyncio.get_event_loop().run_until_complete(svc.list_prs(label="cluster"))

    assert len(results) == 1
    assert results[0].resource_type == "cluster"


# ---------------------------------------------------------------------------
# get_pr
# ---------------------------------------------------------------------------

def test_get_pr_returns_pr_detail():
    svc = GitHubService()
    pr = _mock_pr(number=5, labels=["pipeline", "stage:ete"])
    mock_repo = MagicMock()
    mock_repo.get_pull.return_value = pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        result = asyncio.get_event_loop().run_until_complete(svc.get_pr(5))

    assert result is not None
    assert result.pr_number == 5
    assert result.stage == "ete"


def test_get_pr_returns_none_on_github_exception():
    from github import GithubException
    svc = GitHubService()
    mock_repo = MagicMock()
    mock_repo.get_pull.side_effect = GithubException(404, "Not Found")

    with patch.object(svc, "_repo", return_value=mock_repo):
        result = asyncio.get_event_loop().run_until_complete(svc.get_pr(999))

    assert result is None


# ---------------------------------------------------------------------------
# approve_pr
# ---------------------------------------------------------------------------

def test_approve_pr_creates_review():
    svc = GitHubService()
    pr = _mock_pr(number=3)
    mock_repo = MagicMock()
    mock_repo.get_pull.return_value = pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        asyncio.get_event_loop().run_until_complete(svc.approve_pr(3, "alice"))

    pr.create_review.assert_called_once()
    call_kwargs = pr.create_review.call_args.kwargs
    assert call_kwargs["event"] == "APPROVE"
    assert "alice" in call_kwargs["body"]


# ---------------------------------------------------------------------------
# merge_pr
# ---------------------------------------------------------------------------

def test_merge_pr_squash():
    svc = GitHubService()
    pr = _mock_pr(number=4)
    mock_repo = MagicMock()
    mock_repo.get_pull.return_value = pr

    with patch.object(svc, "_repo", return_value=mock_repo):
        asyncio.get_event_loop().run_until_complete(svc.merge_pr(4))

    pr.merge.assert_called_once_with(merge_method="squash")


# ---------------------------------------------------------------------------
# tag_deployment
# ---------------------------------------------------------------------------

def test_tag_deployment_creates_ref():
    svc = GitHubService()
    mock_repo = MagicMock()

    with patch.object(svc, "_repo", return_value=mock_repo):
        asyncio.get_event_loop().run_until_complete(
            svc.tag_deployment("abc123", "deploy/my-app/r001")
        )

    mock_repo.create_git_ref.assert_called_once_with(
        ref="refs/tags/deploy/my-app/r001", sha="abc123"
    )
