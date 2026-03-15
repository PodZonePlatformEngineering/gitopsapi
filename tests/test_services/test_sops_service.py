"""
Unit tests for SOPSService (TR-SOPS-002).
All subprocess calls, K8s API calls, and git operations are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.models.sops import SOPSBootstrapRequest, SOPSBootstrapResponse
from gitopsgui.services.sops_service import (
    SOPSService,
    _SOPSKeyPair,
    _generate_sops_key,
    _encrypt_with_management_key,
    _install_sops_secret,
    _SOPS_YAML_TEMPLATE,
)


# ---------------------------------------------------------------------------
# _generate_sops_key
# ---------------------------------------------------------------------------

def test_generate_sops_key_returns_stub_when_skip_age():
    with patch("gitopsgui.services.sops_service.SKIP_AGE", True):
        result = _generate_sops_key("gitopsdev")

    assert result.private_key.startswith("AGE-SECRET-KEY-")
    assert result.public_key.startswith("age1")


def test_generate_sops_key_parses_age_output():
    fake_output = (
        "# created: 2026-01-01T00:00:00Z\n"
        "# public key: age1abc123\n"
        "AGE-SECRET-KEY-1FAKEKEY\n"
    )
    with patch("gitopsgui.services.sops_service.SKIP_AGE", False), \
         patch("gitopsgui.services.sops_service.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        result = _generate_sops_key("gitopsdev")

    assert result.private_key == "AGE-SECRET-KEY-1FAKEKEY"
    assert result.public_key == "age1abc123"


def test_generate_sops_key_raises_on_bad_output():
    with patch("gitopsgui.services.sops_service.SKIP_AGE", False), \
         patch("gitopsgui.services.sops_service.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="no keys here", returncode=0)
        with pytest.raises(RuntimeError, match="Failed to parse age-keygen"):
            _generate_sops_key("gitopsdev")


# ---------------------------------------------------------------------------
# _encrypt_with_management_key
# ---------------------------------------------------------------------------

def test_encrypt_returns_stub_when_skip_age():
    with patch("gitopsgui.services.sops_service.SKIP_AGE", True):
        result = _encrypt_with_management_key("PRIV", "age1pub")

    assert "AGE ENCRYPTED FILE" in result


def test_encrypt_calls_age_subprocess():
    with patch("gitopsgui.services.sops_service.SKIP_AGE", False), \
         patch("gitopsgui.services.sops_service.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="ENCRYPTED_OUTPUT", returncode=0)
        result = _encrypt_with_management_key("PRIV", "age1mgmtkey")

    assert result == "ENCRYPTED_OUTPUT"
    call_args = mock_run.call_args[0][0]
    assert "age" in call_args
    assert "age1mgmtkey" in call_args


# ---------------------------------------------------------------------------
# _install_sops_secret
# ---------------------------------------------------------------------------

def test_install_sops_secret_skips_when_skip_k8s():
    with patch("gitopsgui.services.sops_service.SKIP_K8S", True), \
         patch("gitopsgui.services.sops_service.config") as mock_cfg, \
         patch("gitopsgui.services.sops_service.client") as mock_client:
        _install_sops_secret("ctx", "PRIV")

    mock_cfg.load_kube_config.assert_not_called()


def test_install_sops_secret_creates_secret():
    mock_v1 = MagicMock()
    with patch("gitopsgui.services.sops_service.SKIP_K8S", False), \
         patch("gitopsgui.services.sops_service.config"), \
         patch("gitopsgui.services.sops_service.client") as mock_client:
        mock_client.CoreV1Api.return_value = mock_v1
        mock_client.V1Secret = MagicMock(return_value=MagicMock())
        mock_client.V1ObjectMeta = MagicMock(return_value=MagicMock())
        _install_sops_secret("gitopsdev-admin@gitopsdev", "PRIV_KEY")

    mock_v1.create_namespaced_secret.assert_called_once()


def test_install_sops_secret_upserts_on_409():
    from kubernetes.client.exceptions import ApiException
    mock_v1 = MagicMock()
    mock_v1.create_namespaced_secret.side_effect = ApiException(status=409)
    with patch("gitopsgui.services.sops_service.SKIP_K8S", False), \
         patch("gitopsgui.services.sops_service.config"), \
         patch("gitopsgui.services.sops_service.client") as mock_client:
        mock_client.CoreV1Api.return_value = mock_v1
        mock_client.V1Secret = MagicMock(return_value=MagicMock())
        mock_client.V1ObjectMeta = MagicMock(return_value=MagicMock())
        _install_sops_secret("ctx", "PRIV_KEY")

    mock_v1.replace_namespaced_secret.assert_called_once()


# ---------------------------------------------------------------------------
# SOPSService.sops_bootstrap
# ---------------------------------------------------------------------------

async def test_sops_bootstrap_raises_without_management_key():
    svc = SOPSService()
    with patch("gitopsgui.services.sops_service.MANAGEMENT_SOPS_PUBLIC_KEY", ""), \
         patch("gitopsgui.services.sops_service.SKIP_AGE", False):
        with pytest.raises(ValueError, match="MANAGEMENT_SOPS_PUBLIC_KEY"):
            await svc.sops_bootstrap("gitopsdev", SOPSBootstrapRequest())


async def test_sops_bootstrap_returns_response():
    fake_key = _SOPSKeyPair(private_key="AGE-SECRET-KEY-1FAKE", public_key="age1fakepub")

    mock_mgmt_git = MagicMock()
    mock_mgmt_git.create_branch = AsyncMock()
    mock_mgmt_git.write_file = AsyncMock()
    mock_mgmt_git.commit = AsyncMock(return_value="abc123")
    mock_mgmt_git.push = AsyncMock()
    mock_mgmt_git.checkout_main = AsyncMock()

    mock_cluster_git = MagicMock()
    mock_cluster_git.create_branch = AsyncMock()
    mock_cluster_git.write_file = AsyncMock()
    mock_cluster_git.commit = AsyncMock(return_value="def456")
    mock_cluster_git.push = AsyncMock()
    mock_cluster_git.checkout_main = AsyncMock()

    svc = SOPSService()
    svc._mgmt_git = mock_mgmt_git
    svc._cluster_infra_git = mock_cluster_git

    with patch("gitopsgui.services.sops_service._generate_sops_key", return_value=fake_key), \
         patch("gitopsgui.services.sops_service._encrypt_with_management_key", return_value="ENCRYPTED"), \
         patch("gitopsgui.services.sops_service._install_sops_secret"):

        result = await svc.sops_bootstrap(
            "gitopsdev",
            SOPSBootstrapRequest(management_sops_public_key="age1mgmtkey"),
        )

    assert isinstance(result, SOPSBootstrapResponse)
    assert result.cluster_name == "gitopsdev"
    assert result.sops_public_key == "age1fakepub"
    assert result.encrypted_key_path == "sops-keys/gitopsdev.agekey.enc"
    assert result.sops_yaml_committed is True


async def test_sops_bootstrap_writes_encrypted_key_to_mgmt_infra():
    fake_key = _SOPSKeyPair(private_key="AGE-SECRET-KEY-1FAKE", public_key="age1fakepub")

    mock_mgmt_git = MagicMock()
    mock_mgmt_git.create_branch = AsyncMock()
    mock_mgmt_git.write_file = AsyncMock()
    mock_mgmt_git.commit = AsyncMock(return_value="abc123")
    mock_mgmt_git.push = AsyncMock()
    mock_mgmt_git.checkout_main = AsyncMock()

    mock_cluster_git = MagicMock()
    mock_cluster_git.create_branch = AsyncMock()
    mock_cluster_git.write_file = AsyncMock()
    mock_cluster_git.commit = AsyncMock(return_value="def456")
    mock_cluster_git.push = AsyncMock()
    mock_cluster_git.checkout_main = AsyncMock()

    svc = SOPSService()
    svc._mgmt_git = mock_mgmt_git
    svc._cluster_infra_git = mock_cluster_git

    with patch("gitopsgui.services.sops_service._generate_sops_key", return_value=fake_key), \
         patch("gitopsgui.services.sops_service._encrypt_with_management_key", return_value="ENCRYPTED"), \
         patch("gitopsgui.services.sops_service._install_sops_secret"):

        await svc.sops_bootstrap(
            "gitopsdev",
            SOPSBootstrapRequest(management_sops_public_key="age1mgmtkey"),
        )

    # encrypted key written to management-infra with correct path
    mock_mgmt_git.write_file.assert_called_once_with(
        "sops-keys/gitopsdev.agekey.enc", "ENCRYPTED"
    )
    # .sops.yaml written to cluster-infra
    written_path, written_content = mock_cluster_git.write_file.call_args[0]
    assert written_path == ".sops.yaml"
    assert "age1fakepub" in written_content


async def test_sops_bootstrap_env_key_used_when_no_override():
    fake_key = _SOPSKeyPair(private_key="PRIV", public_key="age1pub")

    mock_git = MagicMock()
    for attr in ("create_branch", "write_file", "commit", "push", "checkout_main"):
        setattr(mock_git, attr, AsyncMock(return_value="sha"))

    svc = SOPSService()
    svc._mgmt_git = mock_git
    svc._cluster_infra_git = mock_git

    captured = {}

    def capture_encrypt(private_key, mgmt_key):
        captured["mgmt_key"] = mgmt_key
        return "ENC"

    with patch("gitopsgui.services.sops_service.MANAGEMENT_SOPS_PUBLIC_KEY", "age1envkey"), \
         patch("gitopsgui.services.sops_service._generate_sops_key", return_value=fake_key), \
         patch("gitopsgui.services.sops_service._encrypt_with_management_key", side_effect=capture_encrypt), \
         patch("gitopsgui.services.sops_service._install_sops_secret"):

        await svc.sops_bootstrap("gitopsdev", SOPSBootstrapRequest())

    assert captured["mgmt_key"] == "age1envkey"
