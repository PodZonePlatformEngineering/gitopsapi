"""
Shared fixtures and helpers for GitOpsAPI tests.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

# Ensure dev role bypass is active and no real git/github ops run during tests
os.environ.setdefault("GITOPSGUI_DEV_ROLE", "cluster_operator")
os.environ.setdefault("GITOPS_REPO_URL", "https://github.com/test/repo")
os.environ.setdefault("GITHUB_REPO", "test/repo")
os.environ.setdefault("GITHUB_TOKEN", "test-token")


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def headers_for(role: str, username: str = "testuser") -> dict:
    """Build the OAuth2-proxy-style headers for a given role."""
    group_map = {
        "cluster_operator": "cluster-operators",
        "build_manager":    "build-managers",
        "senior_developer": "senior-developers",
    }
    return {
        "X-Forwarded-User":    username,
        "X-Auth-Request-Groups": group_map[role],
    }


CLUSTER_OP_HEADERS  = headers_for("cluster_operator")
BUILD_MGR_HEADERS   = headers_for("build_manager")
SENIOR_DEV_HEADERS  = headers_for("senior_developer")
NO_ROLE_HEADERS     = {"X-Forwarded-User": "nobody", "X-Auth-Request-Groups": ""}


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

CLUSTER_SPEC = {
    "name": "test-cluster",
    "platform": "proxmox",
    "ip_range": "192.168.1.100-192.168.1.110",
    "dimensions": {
        "control_plane_count": 1,
        "worker_count": 1,
        "cpu_per_node": 4,
        "memory_gb_per_node": 8,
        "boot_volume_gb": 50,
    },
    "gitops_repo_url": "https://github.com/test/gitops",
    "sops_secret_ref": "sops-age-key",
}

APP_SPEC = {
    "name": "test-app",
    "cluster": "test-cluster",
    "helm_repo_url": "https://charts.example.com",
    "chart_name": "test-chart",
    "chart_version": "1.0.0",
    "values_yaml": "replicaCount: 1\n",
}

PIPELINE_SPEC = {
    "name": "test-pipeline",
    "dev_cluster_id": "dev-cluster",
    "ete_cluster_id": "ete-cluster",
    "prod_cluster_id": "prod-cluster",
    "app_id": "test-app",
    "chart_version": "1.0.0",
    "release_id": "release-001",
}

CHANGE_SPEC = {
    "change_request_id": "CHG0001234",
    "change_name": "Add new feature",
    "description": "Implements the new feature X",
    "app_branch": "feature/new-feature",
}


# ---------------------------------------------------------------------------
# FastAPI TestClient fixture — patches lifespan to skip git clone
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    with patch("gitopsgui.services.git_service.GitService.init", new_callable=AsyncMock):
        from gitopsgui.api.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
