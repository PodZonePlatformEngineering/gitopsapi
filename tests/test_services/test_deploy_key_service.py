"""
Unit tests for DeployKeyService (TR-GIT-001).
All subprocess calls and K8s API calls are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.models.deploy_key import GitAccessRequest, GitAccessResponse
from gitopsgui.services.deploy_key_service import (
    DeployKeyService,
    _DeployKeyPair,
    _generate_key_pair,
    _create_deploy_key_secret,
    _create_flux_gitrepository,
    SKIP_K8S,
)


# ---------------------------------------------------------------------------
# _generate_key_pair
# ---------------------------------------------------------------------------

def test_generate_key_pair_calls_ssh_keygen(tmp_path):
    with patch("gitopsgui.services.deploy_key_service.subprocess.run") as mock_run, \
         patch("gitopsgui.services.deploy_key_service.tempfile.TemporaryDirectory") as mock_tmpdir, \
         patch("builtins.open", create=True) as mock_open:

        mock_tmpdir.return_value.__enter__.return_value = str(tmp_path)
        mock_run.return_value = MagicMock(returncode=0)
        mock_open.return_value.__enter__.return_value.read.side_effect = [
            "PRIVATE_KEY_CONTENT",
            "PUBLIC_KEY_CONTENT",
        ]

        result = _generate_key_pair("my-repo")

    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "ssh-keygen" in call_args
    assert "-t" in call_args
    assert "ed25519" in call_args
    assert "flux-my-repo" in call_args


# ---------------------------------------------------------------------------
# _create_deploy_key_secret — SKIP_K8S=True path
# ---------------------------------------------------------------------------

def test_create_deploy_key_secret_skips_when_skip_k8s():
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", True), \
         patch("gitopsgui.services.deploy_key_service._get_known_hosts") as mock_kh, \
         patch("gitopsgui.services.deploy_key_service._load_k8s") as mock_k8s:
        _create_deploy_key_secret("ctx", "repo", "private-key")

    mock_kh.assert_not_called()
    mock_k8s.assert_not_called()


def test_create_deploy_key_secret_creates_secret():
    mock_v1 = MagicMock()
    mock_custom = MagicMock()
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.deploy_key_service._get_known_hosts", return_value="known"), \
         patch("gitopsgui.services.deploy_key_service._load_k8s", return_value=(mock_v1, mock_custom)):
        _create_deploy_key_secret("ctx", "my-repo", "PRIVATE_KEY")

    mock_v1.create_namespaced_secret.assert_called_once()
    call_args = mock_v1.create_namespaced_secret.call_args
    assert call_args[0][0] == "flux-system"


def test_create_deploy_key_secret_upserts_on_409():
    from kubernetes.client.exceptions import ApiException
    mock_v1 = MagicMock()
    mock_v1.create_namespaced_secret.side_effect = ApiException(status=409)
    mock_custom = MagicMock()
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.deploy_key_service._get_known_hosts", return_value="known"), \
         patch("gitopsgui.services.deploy_key_service._load_k8s", return_value=(mock_v1, mock_custom)):
        _create_deploy_key_secret("ctx", "my-repo", "PRIVATE_KEY")

    mock_v1.replace_namespaced_secret.assert_called_once()


# ---------------------------------------------------------------------------
# _create_flux_gitrepository — SKIP_K8S=True path
# ---------------------------------------------------------------------------

def test_create_flux_gitrepository_skips_when_skip_k8s():
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", True), \
         patch("gitopsgui.services.deploy_key_service._load_k8s") as mock_k8s:
        _create_flux_gitrepository("ctx", "repo", "git@github.com:x/y.git", "secret")

    mock_k8s.assert_not_called()


def test_create_flux_gitrepository_creates_cr():
    mock_v1 = MagicMock()
    mock_custom = MagicMock()
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.deploy_key_service._load_k8s", return_value=(mock_v1, mock_custom)):
        _create_flux_gitrepository("ctx", "my-repo", "git@github.com:org/my-repo.git", "my-secret")

    mock_custom.create_namespaced_custom_object.assert_called_once()
    body = mock_custom.create_namespaced_custom_object.call_args.kwargs["body"]
    assert body["kind"] == "GitRepository"
    assert body["spec"]["url"] == "git@github.com:org/my-repo.git"
    assert body["spec"]["secretRef"]["name"] == "my-secret"


def test_create_flux_gitrepository_upserts_on_409():
    from kubernetes.client.exceptions import ApiException
    mock_v1 = MagicMock()
    mock_custom = MagicMock()
    mock_custom.create_namespaced_custom_object.side_effect = ApiException(status=409)
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.deploy_key_service._load_k8s", return_value=(mock_v1, mock_custom)):
        _create_flux_gitrepository("ctx", "my-repo", "git@github.com:org/my-repo.git", "sec")

    mock_custom.replace_namespaced_custom_object.assert_called_once()


# ---------------------------------------------------------------------------
# DeployKeyService.configure_repository_access
# ---------------------------------------------------------------------------

async def test_configure_repository_access_returns_response():
    svc = DeployKeyService()
    svc._gh = MagicMock()
    svc._gh.add_deploy_key = AsyncMock(return_value=12345)

    fake_key = _DeployKeyPair(private_key="PRIV", public_key="PUB")

    with patch("gitopsgui.services.deploy_key_service._generate_key_pair", return_value=fake_key), \
         patch("gitopsgui.services.deploy_key_service._create_deploy_key_secret"), \
         patch("gitopsgui.services.deploy_key_service._create_flux_gitrepository"):

        result = await svc.configure_repository_access(
            "gitopsdev-infra",
            GitAccessRequest(cluster="gitopsdev", git_url="git@github.com:MoTTTT/gitopsdev-infra.git"),
        )

    assert isinstance(result, GitAccessResponse)
    assert result.repo_name == "gitopsdev-infra"
    assert result.github_key_id == 12345
    assert result.secret_name == "flux-gitopsdev-infra-key"
    assert result.error is None


async def test_configure_repository_access_uses_correct_cluster_context():
    svc = DeployKeyService()
    svc._gh = MagicMock()
    svc._gh.add_deploy_key = AsyncMock(return_value=99)

    fake_key = _DeployKeyPair(private_key="PRIV", public_key="PUB")
    captured = {}

    def capture_secret(cluster_context, repo_name, private_key):
        captured["cluster_context"] = cluster_context

    with patch("gitopsgui.services.deploy_key_service._generate_key_pair", return_value=fake_key), \
         patch("gitopsgui.services.deploy_key_service._create_deploy_key_secret", side_effect=capture_secret), \
         patch("gitopsgui.services.deploy_key_service._create_flux_gitrepository"):

        await svc.configure_repository_access(
            "openclaw-apps",
            GitAccessRequest(cluster="openclaw", git_url="git@github.com:MoTTTT/openclaw-apps.git"),
        )

    assert captured["cluster_context"] == "openclaw-admin@openclaw"
