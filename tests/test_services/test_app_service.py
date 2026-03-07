"""
Unit tests for AppService — mocks GitService and GitHubService.
"""

import asyncio
import pytest
import yaml
import textwrap
from unittest.mock import AsyncMock

from gitopsgui.models.application import ApplicationSpec
from gitopsgui.services.app_service import (
    AppService,
    _app_yaml_path,
    _app_values_path,
    _kustomization_path,
    _render_app_yaml,
    _render_kustomization,
)


_SPEC = ApplicationSpec(
    name="my-app",
    cluster="production",
    helm_repo_url="https://charts.example.com",
    chart_name="my-chart",
    chart_version="2.3.4",
    values_yaml="replicaCount: 2\n",
)


def _svc() -> AppService:
    svc = AppService()
    svc._git = AsyncMock()
    svc._gh = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------

def test_app_yaml_path():
    assert _app_yaml_path("my-app") == "gitops/gitops-apps/my-app/my-app.yaml"


def test_app_values_path():
    assert _app_values_path("my-app") == "gitops/gitops-apps/my-app/my-app-values.yaml"


def test_kustomization_path():
    assert _kustomization_path("my-app") == "gitops/gitops-apps/my-app/kustomization.yaml"


# ---------------------------------------------------------------------------
# render helpers
# ---------------------------------------------------------------------------

def test_render_app_yaml_contains_helm_release():
    out = _render_app_yaml(_SPEC)
    assert "HelmRelease" in out
    assert "my-app" in out
    assert "https://charts.example.com" in out
    assert "my-chart" in out
    assert "2.3.4" in out


def test_render_app_yaml_contains_namespace():
    out = _render_app_yaml(_SPEC)
    assert "kind: Namespace" in out


def test_render_app_yaml_references_values_configmap():
    out = _render_app_yaml(_SPEC)
    assert "my-app-values" in out


def test_render_kustomization_references_name():
    out = _render_kustomization("my-app")
    assert "my-app.yaml" in out
    assert "my-app-values" in out
    assert "kustomizeconfig.yaml" in out


# ---------------------------------------------------------------------------
# get_application
# ---------------------------------------------------------------------------

def test_get_application_not_found_returns_none():
    svc = _svc()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError)
    result = asyncio.get_event_loop().run_until_complete(svc.get_application("missing"))
    assert result is None


def test_get_application_parses_multi_doc_yaml():
    svc = _svc()
    multi_doc = textwrap.dedent("""\
        ---
        apiVersion: v1
        kind: Namespace
        metadata:
          name: my-app
        ---
        apiVersion: source.toolkit.fluxcd.io/v1
        kind: HelmRepository
        metadata:
          name: my-app
          namespace: flux-system
        spec:
          interval: 24h
          url: https://charts.example.com
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: my-app
          namespace: flux-system
        spec:
          targetNamespace: production
          interval: 30m
          chart:
            spec:
              chart: my-chart
              version: "2.3.4"
              sourceRef:
                kind: HelmRepository
                name: my-app
                namespace: flux-system
          valuesFrom:
            - kind: ConfigMap
              name: my-app-values
    """)
    svc._git.read_file = AsyncMock(return_value=multi_doc)
    result = asyncio.get_event_loop().run_until_complete(svc.get_application("my-app"))
    assert result is not None
    assert result.spec.chart_name == "my-chart"
    assert result.spec.chart_version == "2.3.4"
    assert result.spec.helm_repo_url == "https://charts.example.com"


# ---------------------------------------------------------------------------
# list_applications
# ---------------------------------------------------------------------------

def test_list_applications_returns_all_found():
    svc = _svc()
    svc._git.list_dir = AsyncMock(return_value=["app-a", "app-b"])
    # Both apps found (get_application returns non-None)
    multi_doc_a = _render_app_yaml(ApplicationSpec(
        name="app-a", cluster="dev", helm_repo_url="https://repo.example.com",
        chart_name="chart-a", chart_version="1.0.0",
    ))
    multi_doc_b = _render_app_yaml(ApplicationSpec(
        name="app-b", cluster="ete", helm_repo_url="https://repo.example.com",
        chart_name="chart-b", chart_version="2.0.0",
    ))
    svc._git.read_file = AsyncMock(side_effect=[multi_doc_a, multi_doc_b])
    results = asyncio.get_event_loop().run_until_complete(svc.list_applications())
    assert len(results) == 2


def test_list_applications_skips_missing():
    svc = _svc()
    svc._git.list_dir = AsyncMock(return_value=["app-a", "broken"])
    multi_doc_a = _render_app_yaml(ApplicationSpec(
        name="app-a", cluster="dev", helm_repo_url="https://repo.example.com",
        chart_name="chart-a", chart_version="1.0.0",
    ))
    svc._git.read_file = AsyncMock(side_effect=[multi_doc_a, FileNotFoundError])
    results = asyncio.get_event_loop().run_until_complete(svc.list_applications())
    assert len(results) == 1


# ---------------------------------------------------------------------------
# create_application
# ---------------------------------------------------------------------------

def test_create_application_writes_four_files_and_opens_pr():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/9")

    result = asyncio.get_event_loop().run_until_complete(svc.create_application(_SPEC))

    assert svc._git.write_file.call_count == 4  # app.yaml + values + kustomization + kustomizeconfig
    svc._gh.create_pr.assert_called_once()
    assert result.pr_url == "https://github.com/test/repo/pull/9"


def test_create_application_pr_labelled_application_and_cluster():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/10")

    asyncio.get_event_loop().run_until_complete(svc.create_application(_SPEC))

    labels = svc._gh.create_pr.call_args.kwargs.get("labels") or svc._gh.create_pr.call_args.args[3]
    assert "application" in labels
    assert "stage:production" in labels


def test_create_application_writes_values_yaml():
    svc = _svc()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/11")

    asyncio.get_event_loop().run_until_complete(svc.create_application(_SPEC))

    all_paths = [call.args[0] for call in svc._git.write_file.call_args_list]
    assert any("my-app-values.yaml" in p for p in all_paths)
