"""
Router tests for /api/v1/status — Flux status and interrogation endpoints.
"""

from unittest.mock import AsyncMock, patch
from tests.conftest import CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS

_AGGREGATE = {
    "clusters": [
        {
            "cluster": "openclaw",
            "kustomizations": [
                {"name": "flux-system", "namespace": "flux-system", "kind": "Kustomization",
                 "ready": True, "message": "Applied revision: main/abc123", "last_reconcile": "2026-03-07T10:00:00Z"}
            ],
            "helm_releases": [],
            "helm_repositories": [],
        }
    ]
}

_CLUSTER_STATUS = _AGGREGATE["clusters"][0]

_RESOURCES = [
    {"name": "ollama", "namespace": "ollama", "kind": "Deployment", "status": "Available", "conditions": []}
]

_DETAIL = {
    "name": "ollama",
    "namespace": "ollama",
    "kind": "Deployment",
    "labels": {},
    "annotations": {},
    "conditions": [{"type": "Available", "status": "True"}],
    "spec": {},
    "events": [],
}

_LOGS = {
    "pod": "ollama-abc123",
    "container": None,
    "lines": ["Starting Ollama server...", "Listening on :11434"],
}


def _patch_k8s(method: str, return_value):
    return patch(
        f"gitopsgui.api.routers.status.K8sService.{method}",
        new=AsyncMock(return_value=return_value),
    )


# ---------------------------------------------------------------------------
# All three roles can access all status endpoints
# ---------------------------------------------------------------------------

def test_aggregate_status_all_roles(client):
    with _patch_k8s("list_all_flux_status", _AGGREGATE):
        for headers in (CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS):
            r = client.get("/api/v1/status", headers=headers)
            assert r.status_code == 200


def test_aggregate_status_no_role_rejected(client):
    r = client.get("/api/v1/status", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_cluster_status(client):
    with _patch_k8s("get_cluster_flux_status", _CLUSTER_STATUS):
        r = client.get("/api/v1/status/openclaw", headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 200
    assert r.json()["cluster"] == "openclaw"
    assert len(r.json()["kustomizations"]) == 1


def test_list_resources(client):
    with _patch_k8s("list_resources", _RESOURCES):
        r = client.get("/api/v1/status/openclaw/resources", headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 200
    assert r.json()[0]["name"] == "ollama"


def test_list_resources_with_filters(client):
    with _patch_k8s("list_resources", _RESOURCES) as mock:
        client.get(
            "/api/v1/status/openclaw/resources?kind=Deployment&namespace=ollama",
            headers=BUILD_MGR_HEADERS,
        )
        mock.assert_called_once_with("openclaw", kind="Deployment", namespace="ollama")


def test_describe_resource(client):
    with _patch_k8s("describe_resource", _DETAIL):
        r = client.get(
            "/api/v1/status/openclaw/resources/Deployment/ollama/ollama",
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 200
    assert r.json()["name"] == "ollama"
    assert r.json()["conditions"][0]["type"] == "Available"


def test_get_logs(client):
    with _patch_k8s("get_logs", _LOGS):
        r = client.get(
            "/api/v1/status/openclaw/resources/Pod/ollama/ollama-abc123/logs",
            headers=SENIOR_DEV_HEADERS,
        )
    assert r.status_code == 200
    assert "Starting Ollama server" in r.json()["lines"][0]


def test_get_logs_with_params(client):
    with _patch_k8s("get_logs", _LOGS) as mock:
        client.get(
            "/api/v1/status/openclaw/resources/Pod/ollama/ollama-abc123/logs"
            "?container=main&tail_lines=50",
            headers=BUILD_MGR_HEADERS,
        )
        mock.assert_called_once_with(
            "openclaw", "ollama", "ollama-abc123",
            container="main", tail_lines=50,
        )
