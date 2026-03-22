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
# _create_deploy_key_secret — now takes kubeconfig_dict, not cluster_context
# ---------------------------------------------------------------------------

def test_create_deploy_key_secret_skips_when_skip_k8s():
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", True), \
         patch("gitopsgui.services.deploy_key_service._get_known_hosts") as mock_kh, \
         patch("gitopsgui.services.deploy_key_service._load_k8s") as mock_k8s:
        _create_deploy_key_secret({}, "repo", "private-key")

    mock_kh.assert_not_called()
    mock_k8s.assert_not_called()


def test_create_deploy_key_secret_creates_secret():
    mock_v1 = MagicMock()
    mock_custom = MagicMock()
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.deploy_key_service._get_known_hosts", return_value="known"), \
         patch("gitopsgui.services.deploy_key_service._load_k8s", return_value=(mock_v1, mock_custom)):
        _create_deploy_key_secret({"clusters": []}, "my-repo", "PRIVATE_KEY")

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
        _create_deploy_key_secret({"clusters": []}, "my-repo", "PRIVATE_KEY")

    mock_v1.replace_namespaced_secret.assert_called_once()


# ---------------------------------------------------------------------------
# _load_k8s — must use load_kube_config_from_dict, not load_kube_config
# ---------------------------------------------------------------------------

def test_load_k8s_uses_from_dict():
    """_load_k8s must use load_kube_config_from_dict — no local context lookup."""
    kubeconfig = {"clusters": [{"name": "test"}]}
    with patch("gitopsgui.services.deploy_key_service.config") as mock_cfg, \
         patch("gitopsgui.services.deploy_key_service.client") as mock_client:
        mock_client.CoreV1Api.return_value = MagicMock()
        mock_client.CustomObjectsApi.return_value = MagicMock()
        from gitopsgui.services.deploy_key_service import _load_k8s
        _load_k8s(kubeconfig)

    mock_cfg.load_kube_config_from_dict.assert_called_once_with(kubeconfig)
    mock_cfg.load_kube_config.assert_not_called()


# ---------------------------------------------------------------------------
# _create_flux_gitrepository — SKIP_K8S=True path
# ---------------------------------------------------------------------------

def test_create_flux_gitrepository_skips_when_skip_k8s():
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", True), \
         patch("gitopsgui.services.deploy_key_service._load_k8s") as mock_k8s:
        _create_flux_gitrepository({}, "repo", "git@github.com:x/y.git", "secret")

    mock_k8s.assert_not_called()


def test_create_flux_gitrepository_creates_cr():
    mock_v1 = MagicMock()
    mock_custom = MagicMock()
    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.deploy_key_service._load_k8s", return_value=(mock_v1, mock_custom)):
        _create_flux_gitrepository({"clusters": []}, "my-repo", "git@github.com:org/my-repo.git", "my-secret")

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
        _create_flux_gitrepository({"clusters": []}, "my-repo", "git@github.com:org/my-repo.git", "sec")

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
            GitAccessRequest(cluster="gitopsdev", git_url="git@github.com:your-org/gitopsdev-infra.git"),
            kubeconfig_dict={},
        )

    assert isinstance(result, GitAccessResponse)
    assert result.repo_name == "gitopsdev-infra"
    assert result.github_key_id == 12345
    assert result.secret_name == "flux-gitopsdev-infra-key"
    assert result.error is None


async def test_configure_repository_access_passes_kubeconfig_dict_to_helpers():
    """Injected kubeconfig_dict must be passed through to the K8s helpers."""
    svc = DeployKeyService()
    svc._gh = MagicMock()
    svc._gh.add_deploy_key = AsyncMock(return_value=99)

    fake_key = _DeployKeyPair(private_key="PRIV", public_key="PUB")
    captured = {}

    def capture_secret(kubeconfig_dict, repo_name, private_key):
        captured["kubeconfig_dict"] = kubeconfig_dict

    injected_kube = {"clusters": [{"name": "mycluster"}]}

    with patch("gitopsgui.services.deploy_key_service._generate_key_pair", return_value=fake_key), \
         patch("gitopsgui.services.deploy_key_service._create_deploy_key_secret", side_effect=capture_secret), \
         patch("gitopsgui.services.deploy_key_service._create_flux_gitrepository"):

        await svc.configure_repository_access(
            "openclaw-apps",
            GitAccessRequest(cluster="openclaw", git_url="git@github.com:your-org/openclaw-apps.git"),
            kubeconfig_dict=injected_kube,
        )

    assert captured["kubeconfig_dict"] is injected_kube


async def test_configure_repository_access_skips_k8s_fetch_when_dict_provided():
    """When kubeconfig_dict is injected, KubeconfigService must not be called."""
    svc = DeployKeyService()
    svc._gh = MagicMock()
    svc._gh.add_deploy_key = AsyncMock(return_value=1)

    fake_key = _DeployKeyPair(private_key="P", public_key="Q")

    with patch("gitopsgui.services.deploy_key_service._generate_key_pair", return_value=fake_key), \
         patch("gitopsgui.services.deploy_key_service._create_deploy_key_secret"), \
         patch("gitopsgui.services.deploy_key_service._create_flux_gitrepository"), \
         patch("gitopsgui.services.deploy_key_service.SKIP_K8S", False), \
         patch("gitopsgui.services.kubeconfig_service.KubeconfigService") as mock_kube_svc:

        await svc.configure_repository_access(
            "foo-infra",
            GitAccessRequest(cluster="foo", git_url="git@github.com:org/foo-infra.git"),
            kubeconfig_dict={"clusters": []},
        )

    # KubeconfigService is imported inline in the else branch; when kubeconfig_dict is
    # provided the else branch is never entered so the mock is never instantiated.
    mock_kube_svc.assert_not_called()
