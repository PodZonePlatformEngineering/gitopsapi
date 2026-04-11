"""
CC-187 — PROJ-012/S1-GAP-K: Instance SOPS bootstrap.

Generates an age key-pair for the gitopsapi instance and stores the private
key as K8s Secret `gitopsapi-sops-age` in the gitopsapi namespace.
Returns the public key for the caller to commit to .sops.yaml in the
instance repo.

Environment variables:
  GITOPSAPI_NAMESPACE  — namespace for the Secret (default: gitopsapi)
  GITOPS_SKIP_K8S=1    — skip K8s Secret creation (dev/test)
"""

import asyncio
import os

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from .sops_service import _generate_sops_key

GITOPSAPI_NAMESPACE = os.environ.get("GITOPSAPI_NAMESPACE", "gitopsapi")
SKIP_K8S = os.environ.get("GITOPS_SKIP_K8S", "") == "1"

_SECRET_NAME = "gitopsapi-sops-age"


def _store_instance_sops_secret(namespace: str, private_key: str) -> None:
    """Upsert `gitopsapi-sops-age` Secret in the given namespace."""
    if SKIP_K8S:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    core_api = client.CoreV1Api()
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=_SECRET_NAME,
            namespace=namespace,
            labels={"app.kubernetes.io/managed-by": "gitopsapi"},
        ),
        type="Opaque",
        string_data={"age.agekey": private_key},
    )
    try:
        core_api.create_namespaced_secret(namespace, secret)
    except ApiException as e:
        if e.status == 409:  # already exists — overwrite (rotation)
            core_api.replace_namespaced_secret(_SECRET_NAME, namespace, secret)
        else:
            raise


class InstanceSopsService:
    """CC-187 — Generate instance SOPS age key and store private key as K8s Secret."""

    def __init__(self):
        self.namespace = GITOPSAPI_NAMESPACE

    async def bootstrap(self) -> str:
        """Generate age key-pair, store private key as K8s Secret, return public key."""
        key_pair = await asyncio.to_thread(_generate_sops_key, "gitopsapi-instance")
        await asyncio.to_thread(_store_instance_sops_secret, self.namespace, key_pair.private_key)
        return key_pair.public_key
