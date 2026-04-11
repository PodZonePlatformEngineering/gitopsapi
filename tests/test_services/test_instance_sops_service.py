"""
Unit tests for InstanceSopsService (CC-187 / PROJ-012/S1-GAP-K).
All subprocess calls and K8s API calls are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.services.sops_service import _SOPSKeyPair
from gitopsgui.services.instance_sops_service import (
    InstanceSopsService,
    _store_instance_sops_secret,
    _SECRET_NAME,
)


# ---------------------------------------------------------------------------
# _store_instance_sops_secret
# ---------------------------------------------------------------------------

def test_store_secret_skips_when_skip_k8s():
    with patch("gitopsgui.services.instance_sops_service.SKIP_K8S", True), \
         patch("gitopsgui.services.instance_sops_service.config") as mock_cfg, \
         patch("gitopsgui.services.instance_sops_service.client"):
        _store_instance_sops_secret("gitopsapi", "PRIV")

    mock_cfg.load_incluster_config.assert_not_called()
    mock_cfg.load_kube_config.assert_not_called()


def test_store_secret_creates_secret_with_correct_name_and_namespace():
    from kubernetes import config as real_config
    mock_v1 = MagicMock()
    with patch("gitopsgui.services.instance_sops_service.SKIP_K8S", False), \
         patch("gitopsgui.services.instance_sops_service.config.load_incluster_config",
               side_effect=real_config.ConfigException("not in cluster")), \
         patch("gitopsgui.services.instance_sops_service.config.load_kube_config"), \
         patch("gitopsgui.services.instance_sops_service.client") as mock_client:
        mock_client.CoreV1Api.return_value = mock_v1
        mock_client.V1Secret = MagicMock(return_value=MagicMock())
        mock_client.V1ObjectMeta = MagicMock(return_value=MagicMock())
        _store_instance_sops_secret("gitopsapi", "PRIV_KEY")

    mock_v1.create_namespaced_secret.assert_called_once()
    assert mock_v1.create_namespaced_secret.call_args.args[0] == "gitopsapi"


def test_store_secret_upserts_on_409():
    from kubernetes import config as real_config
    from kubernetes.client.exceptions import ApiException
    mock_v1 = MagicMock()
    mock_v1.create_namespaced_secret.side_effect = ApiException(status=409)
    with patch("gitopsgui.services.instance_sops_service.SKIP_K8S", False), \
         patch("gitopsgui.services.instance_sops_service.config.load_incluster_config",
               side_effect=real_config.ConfigException("not in cluster")), \
         patch("gitopsgui.services.instance_sops_service.config.load_kube_config"), \
         patch("gitopsgui.services.instance_sops_service.client") as mock_client:
        mock_client.CoreV1Api.return_value = mock_v1
        mock_client.V1Secret = MagicMock(return_value=MagicMock())
        mock_client.V1ObjectMeta = MagicMock(return_value=MagicMock())
        _store_instance_sops_secret("gitopsapi", "PRIV_KEY")

    mock_v1.replace_namespaced_secret.assert_called_once()
    assert mock_v1.replace_namespaced_secret.call_args.args[0] == _SECRET_NAME


# ---------------------------------------------------------------------------
# InstanceSopsService.bootstrap
# ---------------------------------------------------------------------------

async def test_bootstrap_returns_public_key():
    fake_key = _SOPSKeyPair(private_key="AGE-SECRET-KEY-1FAKE", public_key="age1fakepub")
    with patch("gitopsgui.services.instance_sops_service._generate_sops_key", return_value=fake_key), \
         patch("gitopsgui.services.instance_sops_service.SKIP_K8S", True):
        svc = InstanceSopsService()
        result = await svc.bootstrap()

    assert result == "age1fakepub"


async def test_bootstrap_creates_secret_with_correct_key():
    fake_key = _SOPSKeyPair(private_key="AGE-SECRET-KEY-1PRIV", public_key="age1pub")
    captured = {}

    def capture_store(namespace, private_key):
        captured["namespace"] = namespace
        captured["private_key"] = private_key

    with patch("gitopsgui.services.instance_sops_service._generate_sops_key", return_value=fake_key), \
         patch("gitopsgui.services.instance_sops_service._store_instance_sops_secret", side_effect=capture_store), \
         patch("gitopsgui.services.instance_sops_service.GITOPSAPI_NAMESPACE", "gitopsapi"):
        svc = InstanceSopsService()
        await svc.bootstrap()

    assert captured["namespace"] == "gitopsapi"
    assert captured["private_key"] == "AGE-SECRET-KEY-1PRIV"


async def test_bootstrap_idempotent():
    """Second call overwrites the Secret (rotation) — store is called each time."""
    fake_key = _SOPSKeyPair(private_key="AGE-SECRET-KEY-1NEW", public_key="age1newpub")
    store_calls = []

    def record_store(namespace, private_key):
        store_calls.append(private_key)

    with patch("gitopsgui.services.instance_sops_service._generate_sops_key", return_value=fake_key), \
         patch("gitopsgui.services.instance_sops_service._store_instance_sops_secret", side_effect=record_store):
        svc = InstanceSopsService()
        await svc.bootstrap()
        await svc.bootstrap()

    assert len(store_calls) == 2
