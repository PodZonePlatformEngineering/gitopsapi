"""
Unit tests for KubeconfigService — mocks kubernetes client and ClusterService.
"""

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.services.kubeconfig_service import (
    KubeconfigService,
    rewrite_kubeconfig_server,
    _cluster_type_from_name,
)


# ---------------------------------------------------------------------------
# _cluster_type_from_name
# ---------------------------------------------------------------------------

def test_cluster_type_dev():
    assert _cluster_type_from_name("gitopsdev") == "dev"


def test_cluster_type_ete():
    assert _cluster_type_from_name("gitopsete") == "ete"


def test_cluster_type_prod():
    assert _cluster_type_from_name("gitopsprod") == "production"


def test_cluster_type_platform_services():
    assert _cluster_type_from_name("platform-services") == "production"


def test_cluster_type_management():
    assert _cluster_type_from_name("management") == "production"


def test_cluster_type_agentsonly():
    assert _cluster_type_from_name("agentsonly") == "production"


# ---------------------------------------------------------------------------
# rewrite_kubeconfig_server
# ---------------------------------------------------------------------------

_SAMPLE_KUBECONFIG = """\
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: dGVzdA==
    server: https://192.168.4.120:6443
  name: gitopsdev
contexts:
- context:
    cluster: gitopsdev
    user: gitopsdev-admin
  name: gitopsdev-admin@gitopsdev
current-context: gitopsdev-admin@gitopsdev
kind: Config
users:
- name: gitopsdev-admin
  user:
    client-certificate-data: dGVzdA==
    client-key-data: dGVzdA==
"""


def test_rewrite_kubeconfig_server_replaces_address():
    result = rewrite_kubeconfig_server(_SAMPLE_KUBECONFIG, "192.168.1.80", 6442)
    assert "https://192.168.1.80:6442" in result
    assert "192.168.4.120" not in result


def test_rewrite_kubeconfig_server_preserves_ca():
    result = rewrite_kubeconfig_server(_SAMPLE_KUBECONFIG, "freyr", 6442)
    assert "certificate-authority-data" in result
    assert "dGVzdA==" in result


def test_rewrite_kubeconfig_server_multiple_clusters():
    kc = """\
apiVersion: v1
clusters:
- cluster:
    server: https://10.0.0.1:6443
  name: cluster-a
- cluster:
    server: https://10.0.0.2:6443
  name: cluster-b
kind: Config
"""
    result = rewrite_kubeconfig_server(kc, "bastion", 9000)
    assert result.count("https://bastion:9000") == 2
    assert "10.0.0" not in result


# ---------------------------------------------------------------------------
# KubeconfigService.extract_kubeconfig
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_kubeconfig_returns_yaml(tmp_path):
    kubeconfig_bytes = _SAMPLE_KUBECONFIG.encode()
    encoded = base64.b64encode(kubeconfig_bytes).decode()

    mock_secret = MagicMock()
    mock_secret.data = {"value": encoded}

    mock_v1 = MagicMock()
    mock_v1.read_namespaced_secret.return_value = mock_secret

    kube_config_path = tmp_path / "mgmt-kubeconfig"
    kube_config_path.write_text("placeholder")

    with patch("gitopsgui.services.kubeconfig_service.MGMT_KUBECONFIG_PATH", str(kube_config_path)), \
         patch("kubernetes.config.load_kube_config"), \
         patch("kubernetes.client.CoreV1Api", return_value=mock_v1):
        svc = KubeconfigService()
        result = await svc.extract_kubeconfig("gitopsdev")

    assert "gitopsdev" in result
    mock_v1.read_namespaced_secret.assert_called_once_with(
        name="gitopsdev-kubeconfig", namespace="gitopsdev"
    )


@pytest.mark.asyncio
async def test_extract_kubeconfig_503_when_no_mgmt_kubeconfig():
    from fastapi import HTTPException
    with patch("gitopsgui.services.kubeconfig_service.MGMT_KUBECONFIG_PATH", "/nonexistent/path"):
        svc = KubeconfigService()
        with pytest.raises(HTTPException) as exc_info:
            await svc.extract_kubeconfig("gitopsdev")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_extract_kubeconfig_404_when_secret_missing(tmp_path):
    from fastapi import HTTPException
    from kubernetes.client.exceptions import ApiException  # type: ignore

    kube_config_path = tmp_path / "mgmt-kubeconfig"
    kube_config_path.write_text("placeholder")

    mock_v1 = MagicMock()
    mock_v1.read_namespaced_secret.side_effect = ApiException(status=404)

    with patch("gitopsgui.services.kubeconfig_service.MGMT_KUBECONFIG_PATH", str(kube_config_path)), \
         patch("kubernetes.config.load_kube_config"), \
         patch("kubernetes.client.CoreV1Api", return_value=mock_v1):
        svc = KubeconfigService()
        with pytest.raises(HTTPException) as exc_info:
            await svc.extract_kubeconfig("gitopsdev")

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# KubeconfigService.get_kubeconfig — role access + bastion rewrite
# ---------------------------------------------------------------------------

def _make_svc_with_extract(kubeconfig_yaml: str):
    svc = KubeconfigService()
    svc.extract_kubeconfig = AsyncMock(return_value=kubeconfig_yaml)
    return svc


@pytest.mark.asyncio
async def test_get_kubeconfig_cluster_operator_any_cluster():
    from gitopsgui.models.cluster import ClusterResponse, ClusterSpec, ClusterDimensions
    svc = _make_svc_with_extract(_SAMPLE_KUBECONFIG)

    mock_cluster = ClusterResponse(
        name="gitopsprod",
        spec=ClusterSpec(
            name="gitopsprod", vip="192.168.4.190",
            ip_range="192.168.4.191-192.168.4.197",
            dimensions=ClusterDimensions(),
            sops_secret_ref="gitopsapi-age-key",
        ),
    )
    with patch(
        "gitopsgui.services.cluster_service.ClusterService.get_cluster",
        new=AsyncMock(return_value=mock_cluster),
    ):
        result = await svc.get_kubeconfig("gitopsprod", "cluster_operator")

    assert "gitopsdev" in result  # kubeconfig content unchanged (no bastion)


@pytest.mark.asyncio
async def test_get_kubeconfig_build_manager_blocked_on_prod():
    from fastapi import HTTPException
    svc = _make_svc_with_extract(_SAMPLE_KUBECONFIG)

    with pytest.raises(HTTPException) as exc_info:
        await svc.get_kubeconfig("gitopsprod", "build_manager")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_kubeconfig_rewrites_bastion_url():
    from gitopsgui.models.cluster import (
        ClusterResponse, ClusterSpec, ClusterDimensions, BastionSpec
    )
    svc = _make_svc_with_extract(_SAMPLE_KUBECONFIG)

    mock_cluster = ClusterResponse(
        name="gitopsdev",
        spec=ClusterSpec(
            name="gitopsdev", vip="192.168.4.120",
            ip_range="192.168.4.121-192.168.4.127",
            dimensions=ClusterDimensions(),
            sops_secret_ref="gitopsapi-age-key",
            bastion=BastionSpec(hostname="freyr", ip="192.168.1.80", api_port=6442),
        ),
    )
    with patch(
        "gitopsgui.services.cluster_service.ClusterService.get_cluster",
        new=AsyncMock(return_value=mock_cluster),
    ):
        result = await svc.get_kubeconfig("gitopsdev", "cluster_operator")

    assert "https://192.168.1.80:6442" in result
    assert "192.168.4.120:6443" not in result


@pytest.mark.asyncio
async def test_get_kubeconfig_no_bastion_returns_raw():
    from gitopsgui.models.cluster import ClusterResponse, ClusterSpec, ClusterDimensions
    svc = _make_svc_with_extract(_SAMPLE_KUBECONFIG)

    mock_cluster = ClusterResponse(
        name="gitopsdev",
        spec=ClusterSpec(
            name="gitopsdev", vip="192.168.4.120",
            ip_range="192.168.4.121-192.168.4.127",
            dimensions=ClusterDimensions(),
            sops_secret_ref="gitopsapi-age-key",
            bastion=None,
        ),
    )
    with patch(
        "gitopsgui.services.cluster_service.ClusterService.get_cluster",
        new=AsyncMock(return_value=mock_cluster),
    ):
        result = await svc.get_kubeconfig("gitopsdev", "cluster_operator")

    assert "192.168.4.120:6443" in result
