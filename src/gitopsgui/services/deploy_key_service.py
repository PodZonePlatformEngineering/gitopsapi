"""
TR-GIT-001 — Deploy key generation and repository Git access configuration.

Automates:
1. SSH ed25519 deploy key pair generation (ssh-keygen)
2. Public key upload to GitHub repo (via GitHubService)
3. Private key storage as K8s Secret in flux-system namespace
4. Flux GitRepository CR creation referencing the Secret

Environment variables:
  GITOPS_SKIP_GITHUB=1  — skip real GitHub API calls (dev/test)
  GITOPS_SKIP_K8S=1     — skip K8s Secret creation and CR apply (dev/test)
"""

import asyncio
import os
import subprocess
import tempfile
from typing import Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from ..models.deploy_key import GitAccessRequest, GitAccessResponse
from .github_service import GitHubService, SKIP_GITHUB

SKIP_K8S = os.environ.get("GITOPS_SKIP_K8S", "") == "1"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _DeployKeyPair:
    def __init__(self, private_key: str, public_key: str):
        self.private_key = private_key
        self.public_key = public_key


def _generate_key_pair(repo_name: str) -> _DeployKeyPair:
    """Generate an SSH ed25519 key pair via ssh-keygen. Returns private + public key strings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = os.path.join(tmpdir, "deploy_key")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-C", f"flux-{repo_name}", "-f", key_path, "-N", ""],
            check=True,
            capture_output=True,
        )
        private_key = open(key_path).read()
        public_key = open(f"{key_path}.pub").read()
    return _DeployKeyPair(private_key=private_key, public_key=public_key)


def _get_known_hosts() -> str:
    """Fetch github.com SSH host keys via ssh-keyscan."""
    result = subprocess.run(
        ["ssh-keyscan", "github.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _load_k8s(cluster_context: str) -> tuple[client.CoreV1Api, client.CustomObjectsApi]:
    """Load kubeconfig for the named cluster context; return CoreV1Api and CustomObjectsApi."""
    config.load_kube_config(context=cluster_context)
    return client.CoreV1Api(), client.CustomObjectsApi()


def _create_deploy_key_secret(
    cluster_context: str,
    repo_name: str,
    private_key: str,
) -> None:
    """Upsert a flux-<repo>-key Secret in flux-system namespace."""
    if SKIP_K8S:
        return
    known_hosts = _get_known_hosts()
    v1, _ = _load_k8s(cluster_context)
    secret_name = f"flux-{repo_name}-key"
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=secret_name, namespace="flux-system"),
        string_data={"identity": private_key, "known_hosts": known_hosts},
    )
    try:
        v1.create_namespaced_secret("flux-system", secret)
    except ApiException as exc:
        if exc.status == 409:
            v1.replace_namespaced_secret(secret_name, "flux-system", secret)
        else:
            raise


def _create_flux_gitrepository(
    cluster_context: str,
    repo_name: str,
    git_url: str,
    secret_name: str,
) -> None:
    """Upsert a Flux GitRepository CR in flux-system namespace."""
    if SKIP_K8S:
        return
    _, custom = _load_k8s(cluster_context)
    cr = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "GitRepository",
        "metadata": {"name": repo_name, "namespace": "flux-system"},
        "spec": {
            "interval": "5m",
            "url": git_url,
            "ref": {"branch": "main"},
            "secretRef": {"name": secret_name},
        },
    }
    try:
        custom.create_namespaced_custom_object(
            group="source.toolkit.fluxcd.io",
            version="v1",
            namespace="flux-system",
            plural="gitrepositories",
            body=cr,
        )
    except ApiException as exc:
        if exc.status == 409:
            custom.replace_namespaced_custom_object(
                group="source.toolkit.fluxcd.io",
                version="v1",
                namespace="flux-system",
                plural="gitrepositories",
                name=repo_name,
                body=cr,
            )
        else:
            raise


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DeployKeyService:
    """TR-GIT-001 — Automates SSH deploy key lifecycle for GitOps repositories."""

    def __init__(self):
        self._gh: Optional[GitHubService] = None

    def _github(self) -> GitHubService:
        return self._gh or GitHubService()

    async def configure_repository_access(
        self,
        repo_name: str,
        request: GitAccessRequest,
    ) -> GitAccessResponse:
        """Orchestrate deploy key generation → GitHub upload → K8s Secret → Flux GitRepository.

        Args:
            repo_name: Repository name (without owner prefix).
            request:   GitAccessRequest with cluster context and SSH git URL.

        Returns:
            GitAccessResponse with key ID, secret name, and CR creation status.
        """
        cluster_context = f"{request.cluster}-admin@{request.cluster}"

        # 1. Generate key pair
        key_pair = await asyncio.to_thread(_generate_key_pair, repo_name)

        # 2. Upload public key to GitHub
        key_id = await self._github().add_deploy_key(
            repo_name=repo_name,
            title=f"flux-{request.cluster}",
            public_key=key_pair.public_key,
            read_only=False,
        )

        # 3. Create K8s Secret in flux-system
        secret_name = f"flux-{repo_name}-key"
        await asyncio.to_thread(
            _create_deploy_key_secret, cluster_context, repo_name, key_pair.private_key
        )

        # 4. Create Flux GitRepository CR
        await asyncio.to_thread(
            _create_flux_gitrepository, cluster_context, repo_name, request.git_url, secret_name
        )

        return GitAccessResponse(
            repo_name=repo_name,
            github_key_id=key_id,
            secret_name=secret_name,
            gitrepository_created=not SKIP_K8S,
        )
