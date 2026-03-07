"""
Pydantic model validation tests for PR models.
"""

import pytest
from pydantic import ValidationError
from gitopsgui.models.pr import PRDetail, ReviewerStatus


def test_pr_detail_defaults():
    pr = PRDetail(
        pr_number=1,
        title="Test PR",
        state="open",
        diff_url="https://github.com/test/repo/pull/1.diff",
        pr_url="https://github.com/test/repo/pull/1",
    )
    assert pr.labels == []
    assert pr.reviews == []
    assert pr.required_approvers == []
    assert pr.approvals_satisfied is False
    assert pr.stage is None
    assert pr.resource_type is None


def test_pr_detail_with_stage():
    pr = PRDetail(
        pr_number=2,
        title="Promote to prod",
        state="open",
        labels=["promotion", "stage:production"],
        stage="production",
        resource_type="promotion",
        diff_url="https://github.com/test/repo/pull/2.diff",
        pr_url="https://github.com/test/repo/pull/2",
        required_approvers=["build_manager", "cluster_operator"],
        approvals_satisfied=False,
    )
    assert pr.stage == "production"
    assert len(pr.required_approvers) == 2


def test_reviewer_status():
    r = ReviewerStatus(login="alice", role="build_manager", approved=True)
    assert r.approved is True
