"""
TR-SOPS-002 — SOPS age key lifecycle automation.

Automates cluster SOPS key provisioning:
1. Generate SOPS age key pair (age-keygen)
2. Encrypt private key with management cluster's SOPS public key (age)
3. Commit encrypted key to management-infra/sops-keys/{cluster}.agekey.enc
4. Open PR on management-infra for the encrypted key commit
5. Install private key as sops-age Secret in target cluster flux-system namespace
   (uses kubeconfig extracted from CAPI management cluster — no local kubeconfig required)
6. Write .sops.yaml to {cluster}-infra repo referencing cluster's public key

Environment variables:
  MANAGEMENT_SOPS_PUBLIC_KEY   — management cluster SOPS age public key (required)
  GITHUB_ORG                   — GitHub org owning the repos (required; no default)
  GITOPS_SKIP_AGE=1            — skip age subprocess calls; use stub keys (dev/test)
  GITOPS_SKIP_K8S=1            — skip K8s Secret creation and kubeconfig extraction (dev/test)
  GITOPS_SKIP_PUSH=1           — skip git push (dev/test)
"""

import asyncio
import os
import subprocess
import uuid
from typing import Optional

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from ..models.sops import SOPSBootstrapRequest, SOPSBootstrapResponse
from .git_service import GitService
from .github_service import GitHubService, GITHUB_ORG
from . import repo_router

MANAGEMENT_SOPS_PUBLIC_KEY = os.environ.get("MANAGEMENT_SOPS_PUBLIC_KEY", "")
SKIP_AGE = os.environ.get("GITOPS_SKIP_AGE", "") == "1"
SKIP_K8S = os.environ.get("GITOPS_SKIP_K8S", "") == "1"

_SOPS_YAML_TEMPLATE = """\
creation_rules:
  - path_regex: .*\\.yaml
    encrypted_regex: ^(data|stringData)$
    age: >-
      {public_key}
"""

_MGMT_INFRA_REPO = "management-infra"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _SOPSKeyPair:
    def __init__(self, private_key: str, public_key: str):
        self.private_key = private_key
        self.public_key = public_key


def _generate_sops_key(cluster_name: str) -> _SOPSKeyPair:
    """Run age-keygen and parse public key from output."""
    if SKIP_AGE:
        stub_id = cluster_name[:8]
        return _SOPSKeyPair(
            private_key=f"AGE-SECRET-KEY-1FAKESTUB{stub_id.upper()}",
            public_key=f"age1fakestub{stub_id}publickey",
        )
    result = subprocess.run(
        ["age-keygen"],
        check=True,
        capture_output=True,
        text=True,
    )
    private_key = ""
    public_key = ""
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("AGE-SECRET-KEY"):
            private_key = stripped
        elif "public key:" in line.lower():
            public_key = line.split(":")[-1].strip()
    if not private_key or not public_key:
        raise RuntimeError(f"Failed to parse age-keygen output: {result.stdout!r}")
    return _SOPSKeyPair(private_key=private_key, public_key=public_key)


def _encrypt_with_management_key(private_key: str, mgmt_public_key: str) -> str:
    """Encrypt private_key string using age, recipient = mgmt_public_key. Returns armored ciphertext."""
    if SKIP_AGE:
        return "-----BEGIN AGE ENCRYPTED FILE-----\nFAKESTUBENCRYPTED\n-----END AGE ENCRYPTED FILE-----\n"
    result = subprocess.run(
        ["age", "--recipient", mgmt_public_key, "--armor"],
        input=private_key,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _install_sops_secret(kubeconfig_dict: dict, private_key: str) -> None:
    """Upsert sops-age Secret in flux-system namespace of the target cluster.

    Uses an in-memory kubeconfig dict (extracted from CAPI management cluster secret)
    so this works when gitopsapi is running in-cluster with no local ~/.kube/config.
    Pass an empty dict when SKIP_K8S=1 (the function returns immediately).
    """
    if SKIP_K8S:
        return
    config.load_kube_config_from_dict(kubeconfig_dict)
    v1 = client.CoreV1Api()
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name="sops-age", namespace="flux-system"),
        string_data={"age.agekey": private_key},
    )
    try:
        v1.create_namespaced_secret("flux-system", secret)
    except ApiException as exc:
        if exc.status == 409:
            v1.replace_namespaced_secret("sops-age", "flux-system", secret)
        else:
            raise


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SOPSService:
    """TR-SOPS-002 — Automates SOPS age key lifecycle for cluster provisioning."""

    def __init__(self):
        self._mgmt_git: Optional[GitService] = None
        self._cluster_infra_git: Optional[GitService] = None
        self._gh_mgmt: Optional[GitHubService] = None  # injectable for tests

    def _get_mgmt_git(self) -> GitService:
        """Return GitService targeting management-infra repo (HTTPS)."""
        if self._mgmt_git:
            return self._mgmt_git
        return GitService(
            repo_url=f"https://github.com/{GITHUB_ORG}/{_MGMT_INFRA_REPO}.git",
            local_path=None,
        )

    def _get_cluster_infra_git(self, cluster_name: str) -> GitService:
        """Return GitService targeting {cluster}-infra repo (HTTPS via repo_router)."""
        if self._cluster_infra_git:
            return self._cluster_infra_git
        return GitService(
            repo_url=repo_router.infra_repo_url(cluster_name),
            local_path=None,
        )

    def _get_gh_mgmt(self) -> GitHubService:
        """Return GitHubService targeting management-infra."""
        if self._gh_mgmt:
            return self._gh_mgmt
        return GitHubService(repo_name=f"{GITHUB_ORG}/{_MGMT_INFRA_REPO}")

    async def sops_bootstrap(
        self,
        cluster_name: str,
        request: SOPSBootstrapRequest,
    ) -> SOPSBootstrapResponse:
        """Orchestrate SOPS key generation → encryption → management-infra PR → cluster install → .sops.yaml.

        Args:
            cluster_name: Name of the cluster (e.g. "gitopsdev").
            request:       SOPSBootstrapRequest; optionally overrides management SOPS public key.

        Returns:
            SOPSBootstrapResponse with SOPS public key, operation status, and management-infra PR URL.

        Raises:
            ValueError:   MANAGEMENT_SOPS_PUBLIC_KEY not configured and no override provided.
            RuntimeError: age-keygen or age encryption subprocess failed.
        """
        mgmt_key = request.management_sops_public_key or MANAGEMENT_SOPS_PUBLIC_KEY
        if not mgmt_key and not SKIP_AGE:
            raise ValueError(
                "MANAGEMENT_SOPS_PUBLIC_KEY not configured. "
                "Set the env var or provide management_sops_public_key in the request."
            )

        branch = f"sops-bootstrap-{cluster_name}-{uuid.uuid4().hex[:8]}"
        encrypted_path = f"sops-keys/{cluster_name}.agekey.enc"

        # 1. Generate SOPS age key pair
        sops_key = await asyncio.to_thread(_generate_sops_key, cluster_name)

        # 2. Encrypt private key with management cluster's public key
        encrypted = await asyncio.to_thread(
            _encrypt_with_management_key,
            sops_key.private_key,
            mgmt_key or "age1stub",
        )

        # 3. Commit encrypted key to management-infra/sops-keys/
        mgmt_git = self._get_mgmt_git()
        await mgmt_git.create_branch(branch)
        await mgmt_git.write_file(encrypted_path, encrypted)
        await mgmt_git.commit(f"Add SOPS key for {cluster_name} (TR-SOPS-002)")
        await mgmt_git.push()
        await mgmt_git.checkout_main()

        # 4. Open PR on management-infra for the encrypted key commit
        gh_mgmt = self._get_gh_mgmt()
        mgmt_pr_url = await gh_mgmt.create_pr(
            branch=branch,
            title=f"Add SOPS key for {cluster_name} (TR-SOPS-002)",
            body=(
                f"Adds encrypted SOPS age key for `{cluster_name}` at "
                f"`{encrypted_path}`.\n\n"
                f"**Effect on merge**: Flux can decrypt SOPS-encrypted secrets "
                f"in `{cluster_name}-infra` once the `sops-age` Secret is also "
                f"present in the cluster's `flux-system` namespace (applied by "
                f"this bootstrap operation).\n\n"
                f"TR-SOPS-002"
            ),
            labels=["cluster", "stage:production"],
            reviewers=[],
        )

        # 5. Install SOPS private key as sops-age Secret in target cluster.
        #    Kubeconfig is fetched in-memory from the CAPI management cluster secret
        #    (no local ~/.kube/config required — works when running in-cluster).
        if not SKIP_K8S:
            from .kubeconfig_service import KubeconfigService, rewrite_kubeconfig_server
            from .cluster_service import ClusterService
            kubeconfig_yaml = await KubeconfigService().extract_kubeconfig(cluster_name)
            cluster_info = await ClusterService().get_cluster(cluster_name)
            if cluster_info and cluster_info.spec.bastion:
                b = cluster_info.spec.bastion
                kubeconfig_yaml = rewrite_kubeconfig_server(
                    kubeconfig_yaml, b.ip, b.api_port
                )
            kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
            await asyncio.to_thread(_install_sops_secret, kubeconfig_dict, sops_key.private_key)

        # 6. Write .sops.yaml to {cluster}-infra repo
        sops_yaml = _SOPS_YAML_TEMPLATE.format(public_key=sops_key.public_key)
        cluster_git = self._get_cluster_infra_git(cluster_name)
        await cluster_git.create_branch(branch)
        await cluster_git.write_file(".sops.yaml", sops_yaml)
        await cluster_git.commit(f"Add .sops.yaml for {cluster_name} (TR-SOPS-002)")
        await cluster_git.push()
        await cluster_git.checkout_main()

        return SOPSBootstrapResponse(
            cluster_name=cluster_name,
            sops_public_key=sops_key.public_key,
            encrypted_key_path=encrypted_path,
            secret_created=not SKIP_K8S,
            sops_yaml_committed=True,
            mgmt_pr_url=mgmt_pr_url,
        )
