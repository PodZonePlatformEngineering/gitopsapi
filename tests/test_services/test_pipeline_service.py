"""
Unit tests for PipelineService — mocks GitService and GitHubService.
"""

import asyncio
import pytest
import yaml
from unittest.mock import AsyncMock

from gitopsgui.models.pipeline import PipelineSpec, ChangeSpec
from gitopsgui.services.pipeline_service import PipelineService, _pipeline_yaml_path, _change_yaml_path


_SPEC = PipelineSpec(
    name="my-pipeline",
    dev_cluster_id="dev",
    ete_cluster_id="ete",
    prod_cluster_id="prod",
    app_id="my-app",
    chart_version="1.0.0",
    release_id="r001",
)

_CHANGE = ChangeSpec(
    change_request_id="CHG001",
    change_name="Add feature X",
    description="Adds feature X to my-app",
    app_branch="feature/x",
)


def _svc() -> PipelineService:
    svc = PipelineService()
    svc._git = AsyncMock()
    svc._gh = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------

def test_pipeline_yaml_path():
    assert _pipeline_yaml_path("pipe") == "pipelines/pipe/pipeline.yaml"


def test_change_yaml_path():
    assert _change_yaml_path("pipe", "CHG001") == "pipelines/pipe/changes/CHG001/change.yaml"


# ---------------------------------------------------------------------------
# get_pipeline
# ---------------------------------------------------------------------------

def test_get_pipeline_not_found_returns_none():
    svc = _svc()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError)
    result = asyncio.get_event_loop().run_until_complete(svc.get_pipeline("missing"))
    assert result is None


def test_get_pipeline_parses_yaml():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value=yaml.dump({
        "name": "my-pipeline",
        "dev_cluster_id": "dev",
        "ete_cluster_id": "ete",
        "prod_cluster_id": "prod",
        "app_id": "my-app",
        "chart_version": "1.0.0",
        "release_id": "r001",
    }))
    result = asyncio.get_event_loop().run_until_complete(svc.get_pipeline("my-pipeline"))
    assert result is not None
    assert result.spec.app_id == "my-app"
    assert result.spec.release_id == "r001"


# ---------------------------------------------------------------------------
# create_pipeline
# ---------------------------------------------------------------------------

def test_create_pipeline_writes_yaml_and_opens_pr():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/5")

    result = asyncio.get_event_loop().run_until_complete(svc.create_pipeline(_SPEC))

    svc._git.write_file.assert_called_once()
    written_path, written_content = svc._git.write_file.call_args.args
    assert "pipelines/my-pipeline/pipeline.yaml" == written_path
    data = yaml.safe_load(written_content)
    assert data["app_id"] == "my-app"
    assert result.pr_url == "https://github.com/test/repo/pull/5"


# ---------------------------------------------------------------------------
# create_change
# ---------------------------------------------------------------------------

def test_create_change_writes_change_yaml():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/6")
    svc._git.read_file = AsyncMock(return_value=yaml.dump({
        "name": "my-pipeline", "dev_cluster_id": "dev", "ete_cluster_id": "ete",
        "prod_cluster_id": "prod", "app_id": "my-app", "chart_version": "1.0.0",
        "release_id": "r001",
    }))
    svc._git.list_dir = AsyncMock(return_value=[])

    asyncio.get_event_loop().run_until_complete(svc.create_change("my-pipeline", _CHANGE))

    written_path, written_content = svc._git.write_file.call_args.args
    assert "CHG001" in written_path
    data = yaml.safe_load(written_content)
    assert data["change_name"] == "Add feature X"
    assert data["change_request_id"] == "CHG001"


def test_create_change_pr_labelled_stage_dev():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/7")
    svc._git.read_file = AsyncMock(return_value=yaml.dump({
        "name": "my-pipeline", "dev_cluster_id": "dev", "ete_cluster_id": "ete",
        "prod_cluster_id": "prod", "app_id": "my-app", "chart_version": "1.0.0",
        "release_id": "r001",
    }))
    svc._git.list_dir = AsyncMock(return_value=[])

    asyncio.get_event_loop().run_until_complete(svc.create_change("my-pipeline", _CHANGE))

    labels = svc._gh.create_pr.call_args.kwargs.get("labels") or svc._gh.create_pr.call_args.args[3]
    assert "stage:dev" in labels


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def test_promote_creates_pr_with_correct_stage_label():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/8")
    svc._git.read_file = AsyncMock(return_value=yaml.dump({
        "name": "my-pipeline", "dev_cluster_id": "dev", "ete_cluster_id": "ete",
        "prod_cluster_id": "prod", "app_id": "my-app", "chart_version": "1.0.0",
        "release_id": "r001",
    }))
    svc._git.list_dir = AsyncMock(return_value=[])

    result = asyncio.get_event_loop().run_until_complete(svc.promote("my-pipeline", "production"))

    labels = svc._gh.create_pr.call_args.kwargs.get("labels") or svc._gh.create_pr.call_args.args[3]
    assert "stage:production" in labels
    assert "promotion" in labels
    assert result.pr_url == "https://github.com/test/repo/pull/8"


def test_promote_missing_pipeline_raises_404():
    from fastapi import HTTPException
    svc = _svc()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError)
    svc._git.list_dir = AsyncMock(return_value=[])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.get_event_loop().run_until_complete(svc.promote("missing", "ete"))
    assert exc_info.value.status_code == 404
