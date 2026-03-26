"""
Router tests for /api/v1/application-deployments (canonical) and
/api/v1/application-configs (backwards-compatible alias).
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import (
    CLUSTER_OP_HEADERS,
    BUILD_MGR_HEADERS,
    SENIOR_DEV_HEADERS,
    NO_ROLE_HEADERS,
    APP_CONFIG_SPEC,
)

_CONFIG_RESPONSE = {
    "id": "keycloak-security",
    "app_id": "keycloak",
    "cluster_id": "security",
    "chart_version_override": None,
    "values_override": "",
    "enabled": True,
    "pipeline_stage": None,
    "gitops_source_ref": None,
    "pr_url": "https://github.com/test/repo/pull/10",
}


# ── /application-deployments (canonical) ────────────────────────────────────

def test_assign_application_deployment_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.create",
        new=AsyncMock(return_value=_CONFIG_RESPONSE),
    ):
        r = client.post("/api/v1/application-deployments", json=APP_CONFIG_SPEC, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 202
    assert r.json()["id"] == "keycloak-security"


def test_assign_application_deployment_senior_dev_rejected(client):
    r = client.post("/api/v1/application-deployments", json=APP_CONFIG_SPEC, headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403


def test_assign_application_deployment_no_role_rejected(no_auth_client):
    r = no_auth_client.post("/api/v1/application-deployments", json=APP_CONFIG_SPEC, headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_list_deployments_by_cluster(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.list_by_cluster",
        new=AsyncMock(return_value=[_CONFIG_RESPONSE]),
    ):
        r = client.get("/api/v1/application-deployments?cluster=security", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_list_deployments_by_application(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.list_by_application",
        new=AsyncMock(return_value=[_CONFIG_RESPONSE]),
    ):
        r = client.get("/api/v1/application-deployments?application=keycloak", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200


def test_list_deployments_requires_filter(client):
    r = client.get("/api/v1/application-deployments", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 400


def test_list_deployments_no_role_rejected(no_auth_client):
    r = no_auth_client.get("/api/v1/application-deployments?cluster=security", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_patch_deployment_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.patch",
        new=AsyncMock(return_value=_CONFIG_RESPONSE),
    ):
        r = client.patch(
            "/api/v1/application-deployments/keycloak-security",
            json={"values_override": "replicaCount: 2\n"},
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 202


def test_patch_deployment_senior_dev_rejected(client):
    r = client.patch(
        "/api/v1/application-deployments/keycloak-security",
        json={"values_override": "replicaCount: 2\n"},
        headers=SENIOR_DEV_HEADERS,
    )
    assert r.status_code == 403


def test_delete_deployment_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.delete",
        new=AsyncMock(return_value=_CONFIG_RESPONSE),
    ):
        r = client.delete(
            "/api/v1/application-deployments/keycloak-security",
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 202


def test_delete_deployment_build_manager_rejected(client):
    r = client.delete(
        "/api/v1/application-deployments/keycloak-security",
        headers=BUILD_MGR_HEADERS,
    )
    assert r.status_code == 403


# ── /application-configs (backwards-compatible alias) ────────────────────────

def test_assign_application_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.create",
        new=AsyncMock(return_value=_CONFIG_RESPONSE),
    ):
        r = client.post("/api/v1/application-configs", json=APP_CONFIG_SPEC, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 202
    assert r.json()["id"] == "keycloak-security"


def test_assign_application_senior_dev_rejected(client):
    r = client.post("/api/v1/application-configs", json=APP_CONFIG_SPEC, headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403


def test_assign_application_no_role_rejected(no_auth_client):
    r = no_auth_client.post("/api/v1/application-configs", json=APP_CONFIG_SPEC, headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_list_by_cluster(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.list_by_cluster",
        new=AsyncMock(return_value=[_CONFIG_RESPONSE]),
    ):
        r = client.get("/api/v1/application-configs?cluster=security", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_list_by_application(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.list_by_application",
        new=AsyncMock(return_value=[_CONFIG_RESPONSE]),
    ):
        r = client.get("/api/v1/application-configs?application=keycloak", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200


def test_list_requires_filter(client):
    r = client.get("/api/v1/application-configs", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 400


def test_list_no_role_rejected(no_auth_client):
    r = no_auth_client.get("/api/v1/application-configs?cluster=security", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_patch_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.patch",
        new=AsyncMock(return_value=_CONFIG_RESPONSE),
    ):
        r = client.patch(
            "/api/v1/application-configs/keycloak-security",
            json={"values_override": "replicaCount: 2\n"},
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 202


def test_patch_senior_dev_rejected(client):
    r = client.patch(
        "/api/v1/application-configs/keycloak-security",
        json={"values_override": "replicaCount: 2\n"},
        headers=SENIOR_DEV_HEADERS,
    )
    assert r.status_code == 403


def test_delete_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.application_configs.AppConfigService.delete",
        new=AsyncMock(return_value=_CONFIG_RESPONSE),
    ):
        r = client.delete(
            "/api/v1/application-configs/keycloak-security",
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 202


def test_delete_build_manager_rejected(client):
    r = client.delete(
        "/api/v1/application-configs/keycloak-security",
        headers=BUILD_MGR_HEADERS,
    )
    assert r.status_code == 403
