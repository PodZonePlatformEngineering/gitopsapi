"""
TR-SOPS-002 — SOPS age key lifecycle automation.

Automates cluster SOPS key provisioning:
1. Generate SOPS age key pair (age-keygen)
2. Encrypt private key with management cluster's SOPS public key (age)
3. Commit encrypted key to management-infra/sops-keys/{cluster}.agekey.enc
4. Install private key as sops-age Secret in target cluster flux-system namespace
5. Write .sops.yaml to {cluster}-infra repo referencing cluster's public key

Environment variables:
  MANAGEMENT_SOPS_PUBLIC_KEY   — management cluster SOPS age public key (required)
  GITHUB_ORG                   — GitHub org owning the repos (required; no default)
  GITOPS_SKIP_AGE=1            — skip age subprocess calls; use stub keys (dev/test)
  GITOPS_SKIP_K8S=1            — skip K8s Secret creation (dev/test)
  GITOPS_SKIP_PUSH=1           — skip git push (dev/test)
"""

import asyncio
import os
import subprocess
import uuid
from typing import Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from ..models.sops import SOPSBootstrapRequest, SOPSBootstrapResponse
from .git_service import GitService

MANAGEMENT_SOPS_PUBLIC_KEY = os.environ.get("MANAGEMENT_SOPS_PUBLIC_KEY", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")  # required; set GITHUB_ORG in deployment
SKIP_AGE = os.environ.get("GITOPS_SKIP_AGE", "") == "1"
SKIP_K8S = os.environ.get("GITOPS_SKIP_K8S", "") == "1"


_SOPS_YAML_TEMPLATE = """\
creation_rules:
  - path_regex: .*\\.yaml
    encrypted_regex: ^(data|stringData)$
    age: >-
      {public_key}
"""


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


def _install_sops_secret(cluster_context: str, private_key: str) -> None:
    """Upsert sops-age Secret in flux-system namespace of the target cluster."""
    if SKIP_K8S:
        return
    config.load_kube_config(context=cluster_context)
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

    def _get_mgmt_git(self) -> GitService:
        """Return GitService targeting management-infra repo."""
        if self._mgmt_git:
            return self._mgmt_git
        return GitService(
            repo_url=f"git@github.com:{GITHUB_ORG}/management-infra.git",
            local_path=None,
        )

    def _get_cluster_infra_git(self, cluster_name: str) -> GitService:
        """Return GitService targeting {cluster}-infra repo."""
        if self._cluster_infra_git:
            return self._cluster_infra_git
        return GitService(
            repo_url=f"git@github.com:{GITHUB_ORG}/{cluster_name}-infra.git",
            local_path=None,
        )

    async def sops_bootstrap(
        self,
        cluster_name: str,
        request: SOPSBootstrapRequest,
    ) -> SOPSBootstrapResponse:
        """Orchestrate SOPS key generation → encryption → storage → cluster install → .sops.yaml.

        Args:
            cluster_name: Name of the cluster (e.g. "gitopsdev").
            request:       SOPSBootstrapRequest; optionally overrides management SOPS public key.

        Returns:
            SOPSBootstrapResponse with SOPS public key and operation status.

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

        cluster_context = f"{cluster_name}-admin@{cluster_name}"
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

        # 4. Install SOPS private key as sops-age Secret in target cluster
        await asyncio.to_thread(_install_sops_secret, cluster_context, sops_key.private_key)

        # 5. Write .sops.yaml to {cluster}-infra repo
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
        )
