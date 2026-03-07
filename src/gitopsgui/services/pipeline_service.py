"""
GITGUI-007 — Change pipeline object reader/writer.

Gitops repo layout:
  pipelines/<name>/pipeline.yaml                          — pipeline spec
  pipelines/<name>/changes/<change-id>/change.yaml        — change spec
  pipelines/<name>/history/<release-id>/deployment.yaml   — deployment record
  pipelines/<name>/history/<release-id>/tests/results.yaml — test results
"""

import textwrap
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import yaml

from ..models.pipeline import (
    PipelineSpec, PipelineResponse, ChangeSpec,
    DeploymentRecord, TestResult,
)
from .git_service import GitService
from .github_service import GitHubService

_PIPELINES_BASE = "pipelines"

_STAGE_LABELS = {"dev": "stage:dev", "ete": "stage:ete", "production": "stage:production"}
_STAGE_ORDER = ["dev", "ete", "production"]


def _pipeline_yaml_path(name: str) -> str:
    return f"{_PIPELINES_BASE}/{name}/pipeline.yaml"


def _change_yaml_path(name: str, change_id: str) -> str:
    return f"{_PIPELINES_BASE}/{name}/changes/{change_id}/change.yaml"


def _deployment_path(name: str, release_id: str) -> str:
    return f"{_PIPELINES_BASE}/{name}/history/{release_id}/deployment.yaml"


def _test_results_path(name: str, release_id: str) -> str:
    return f"{_PIPELINES_BASE}/{name}/history/{release_id}/tests/results.yaml"


class PipelineService:
    def __init__(self):
        self._git = GitService()
        self._gh = GitHubService()

    async def list_pipelines(self) -> List[PipelineResponse]:
        names = await self._git.list_dir(_PIPELINES_BASE)
        results = []
        for name in names:
            pipeline = await self.get_pipeline(name)
            if pipeline:
                results.append(pipeline)
        return results

    async def get_pipeline(self, name: str) -> Optional[PipelineResponse]:
        try:
            raw = await self._git.read_file(_pipeline_yaml_path(name))
            data = yaml.safe_load(raw)
        except FileNotFoundError:
            return None

        spec = PipelineSpec(
            name=name,
            dev_cluster_id=data.get("dev_cluster_id", ""),
            ete_cluster_id=data.get("ete_cluster_id", ""),
            prod_cluster_id=data.get("prod_cluster_id", ""),
            app_id=data.get("app_id", ""),
            chart_version=data.get("chart_version", ""),
            release_id=data.get("release_id", ""),
        )
        return PipelineResponse(name=name, spec=spec)

    async def create_pipeline(self, spec: PipelineSpec) -> PipelineResponse:
        branch = f"pipeline/create-{spec.name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        data = {
            "name": spec.name,
            "dev_cluster_id": spec.dev_cluster_id,
            "ete_cluster_id": spec.ete_cluster_id,
            "prod_cluster_id": spec.prod_cluster_id,
            "app_id": spec.app_id,
            "chart_version": spec.chart_version,
            "release_id": spec.release_id,
        }
        await self._git.write_file(_pipeline_yaml_path(spec.name), yaml.dump(data))
        await self._git.commit(f"chore: create pipeline {spec.name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Create pipeline: {spec.name}",
            body=f"New change pipeline for application `{spec.app_id}`.",
            labels=["pipeline"],
            reviewers=[],
        )

        return PipelineResponse(name=spec.name, spec=spec, pr_url=pr_url)

    async def create_change(self, pipeline_name: str, change: ChangeSpec) -> PipelineResponse:
        branch = f"pipeline/change-{pipeline_name}-{change.change_request_id}-{uuid.uuid4().hex[:6]}"
        await self._git.create_branch(branch)

        data = {
            "change_request_id": change.change_request_id,
            "change_name": change.change_name,
            "description": change.description,
            "app_branch": change.app_branch,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._git.write_file(
            _change_yaml_path(pipeline_name, change.change_request_id),
            yaml.dump(data),
        )
        await self._git.commit(f"chore: add change {change.change_request_id} to pipeline {pipeline_name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Change {change.change_request_id}: {change.change_name}",
            body=f"**Pipeline**: {pipeline_name}\n**Change**: {change.change_request_id} — {change.change_name}\n\n{change.description}",
            labels=["pipeline", "stage:dev"],
            reviewers=[],
        )

        pipeline = await self.get_pipeline(pipeline_name)
        if pipeline:
            pipeline.pr_url = pr_url
        return pipeline or PipelineResponse(
            name=pipeline_name,
            spec=PipelineSpec(name=pipeline_name, dev_cluster_id="", ete_cluster_id="",
                              prod_cluster_id="", app_id="", chart_version="", release_id=""),
            pr_url=pr_url,
        )

    async def get_history(self, pipeline_name: str) -> List[DeploymentRecord]:
        try:
            release_dirs = await self._git.list_dir(f"{_PIPELINES_BASE}/{pipeline_name}/history")
        except Exception:
            return []

        records = []
        for release_id in release_dirs:
            try:
                raw = await self._git.read_file(_deployment_path(pipeline_name, release_id))
                data = yaml.safe_load(raw)
                records.append(DeploymentRecord(
                    release_id=release_id,
                    stage=data.get("stage", ""),
                    status=data.get("status", ""),
                    timestamp=data.get("timestamp", ""),
                    pr_url=data.get("pr_url"),
                    chart_version=data.get("chart_version"),
                ))
            except FileNotFoundError:
                continue
        return sorted(records, key=lambda r: r.timestamp, reverse=True)

    async def get_test_results(self, pipeline_name: str, release_id: str) -> TestResult:
        raw = await self._git.read_file(_test_results_path(pipeline_name, release_id))
        data = yaml.safe_load(raw)
        return TestResult(
            release_id=release_id,
            passed=data.get("passed", 0),
            failed=data.get("failed", 0),
            test_cases=data.get("test_cases", []),
            raw_output=data.get("raw_output"),
        )

    async def promote(self, pipeline_name: str, target_stage: str) -> PipelineResponse:
        pipeline = await self.get_pipeline(pipeline_name)
        if not pipeline:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_name!r} not found")

        branch = f"pipeline/promote-{pipeline_name}-{target_stage}-{uuid.uuid4().hex[:6]}"
        await self._git.create_branch(branch)

        # Update release_id to record the promotion
        spec = pipeline.spec
        spec_data = {
            "name": spec.name,
            "dev_cluster_id": spec.dev_cluster_id,
            "ete_cluster_id": spec.ete_cluster_id,
            "prod_cluster_id": spec.prod_cluster_id,
            "app_id": spec.app_id,
            "chart_version": spec.chart_version,
            "release_id": spec.release_id,
            "last_promoted_stage": target_stage,
            "last_promoted_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._git.write_file(_pipeline_yaml_path(pipeline_name), yaml.dump(spec_data))
        await self._git.commit(f"chore: promote pipeline {pipeline_name} to {target_stage}")
        await self._git.push()

        stage_label = _STAGE_LABELS.get(target_stage, f"stage:{target_stage}")
        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Promote {pipeline_name} → {target_stage}",
            body=f"Promotion of pipeline `{pipeline_name}` to `{target_stage}`.",
            labels=["promotion", stage_label],
            reviewers=[],
        )

        pipeline.pr_url = pr_url
        return pipeline

    async def record_deployment(self, pipeline_name: str, release_id: str, status: str) -> None:
        data = {
            "release_id": release_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._git.write_file(_deployment_path(pipeline_name, release_id), yaml.dump(data))
        await self._git.commit(f"chore: record deployment {release_id} for {pipeline_name}")
        await self._git.push()

    async def record_test_results(self, pipeline_name: str, release_id: str, results: dict) -> None:
        await self._git.write_file(_test_results_path(pipeline_name, release_id), yaml.dump(results))
        await self._git.commit(f"chore: record test results for {pipeline_name}/{release_id}")
        await self._git.push()
