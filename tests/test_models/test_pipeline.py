"""
Pydantic model validation tests for pipeline models.
"""

import pytest
from pydantic import ValidationError
from gitopsgui.models.pipeline import PipelineSpec, ChangeSpec, DeploymentRecord, TestResult


def test_pipeline_spec_required_fields():
    spec = PipelineSpec(
        name="pipe",
        dev_cluster_id="dev",
        ete_cluster_id="ete",
        prod_cluster_id="prod",
        app_id="my-app",
        chart_version="1.2.3",
        release_id="r001",
    )
    assert spec.name == "pipe"


def test_pipeline_spec_missing_field():
    with pytest.raises(ValidationError):
        PipelineSpec(
            name="pipe",
            dev_cluster_id="dev",
            # ete_cluster_id missing
            prod_cluster_id="prod",
            app_id="my-app",
            chart_version="1.2.3",
            release_id="r001",
        )


def test_change_spec_has_change_name():
    change = ChangeSpec(
        change_request_id="CHG001",
        change_name="Deploy new widget",
        description="Adds the widget feature",
        app_branch="feature/widget",
    )
    assert change.change_name == "Deploy new widget"


def test_change_spec_missing_change_name():
    with pytest.raises(ValidationError):
        ChangeSpec(
            change_request_id="CHG001",
            # change_name missing
            description="desc",
            app_branch="feature/x",
        )


def test_deployment_record_optional_fields():
    record = DeploymentRecord(
        release_id="r001",
        stage="dev",
        status="success",
        timestamp="2026-03-07T10:00:00Z",
    )
    assert record.pr_url is None
    assert record.chart_version is None


def test_test_result_defaults():
    result = TestResult(release_id="r001", passed=5, failed=1)
    assert result.test_cases == []
    assert result.raw_output is None
