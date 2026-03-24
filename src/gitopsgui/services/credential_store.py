"""
CC-083 — Credential store backed by Kubernetes ConfigMap + Secret.

Storage layout (all objects in GITOPSAPI_NAMESPACE, default: "gitopsapi"):

  ConfigMap  gitopsapi-forges           — forge metadata (id, forge_url, is_default)
  Secret     gitopsapi-forge-tokens     — forge git tokens, keyed by forge id
  ConfigMap  gitopsapi-repos            — repo metadata (id, forge_id, repo_name)
  Secret     gitopsapi-repo-tokens      — per-repo git token overrides, keyed by repo id
  ConfigMap  gitopsapi-sops-keys        — sops key public keys, keyed by sops key id
  Secret     gitopsapi-sops-privkeys    — sops age private keys, keyed by sops key id

K8s client loading:
  In-cluster → config.load_incluster_config()
  Dev fallback → config.load_kube_config()
  GITOPS_SKIP_K8S=1 → in-memory dicts (dev/test)

Injectable _v1 field allows test mock injection (same pattern as AppService._git).

Age key generation reuses the age-keygen subprocess pattern from sops_service.
GITOPS_SKIP_AGE=1 suppresses the subprocess and returns stub keys.
"""

import asyncio
import json
import os
import subprocess
from typing import Any, Dict, List, Optional

from kubernetes import client as k8s_client  # type: ignore
from kubernetes import config as k8s_config  # type: ignore
from kubernetes.client.exceptions import ApiException  # type: ignore

from ..models.credentials import (
    GitForgeCreate,
    GitForgeResponse,
    GitRepoCreate,
    GitRepoResponse,
    SopsKeyImport,
    SopsKeyResponse,
)

GITOPSAPI_NAMESPACE = os.environ.get("GITOPSAPI_NAMESPACE", "gitopsapi")


def _skip_k8s() -> bool:
    return os.environ.get("GITOPS_SKIP_K8S", "") == "1"


def _skip_age() -> bool:
    return os.environ.get("GITOPS_SKIP_AGE", "") == "1"

# In-memory fallback when SKIP_K8S=1 — module-level so state persists within a process
_local_forges: Dict[str, dict] = {}
_local_forge_tokens: Dict[str, str] = {}
_local_repos: Dict[str, dict] = {}
_local_repo_tokens: Dict[str, str] = {}
_local_sops_meta: Dict[str, str] = {}     # id → public_key
_local_sops_priv: Dict[str, str] = {}     # id → private_key


# ---------------------------------------------------------------------------
# Age key generation
# ---------------------------------------------------------------------------

class _AgeKeyPair:
    def __init__(self, private_key: str, public_key: str):
        self.private_key = private_key
        self.public_key = public_key


def _generate_age_key(key_id: str) -> _AgeKeyPair:
    if _skip_age():
        stub = key_id[:8]
        return _AgeKeyPair(
            private_key=f"AGE-SECRET-KEY-1FAKESTUB{stub.upper()}",
            public_key=f"age1fakestub{stub}publickey",
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
    return _AgeKeyPair(private_key=private_key, public_key=public_key)


# ---------------------------------------------------------------------------
# K8s helpers
# ---------------------------------------------------------------------------

def _load_v1(injectable: Optional[Any]) -> Any:
    """Return a CoreV1Api — injected mock, or freshly loaded client."""
    if injectable is not None:
        return injectable
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return k8s_client.CoreV1Api()


def _ensure_configmap(v1: Any, name: str, namespace: str) -> None:
    """Create the named ConfigMap if it does not already exist."""
    try:
        v1.read_namespaced_config_map(name=name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            v1.create_namespaced_config_map(
                namespace=namespace,
                body=k8s_client.V1ConfigMap(
                    metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
                    data={},
                ),
            )
        else:
            raise


def _ensure_secret(v1: Any, name: str, namespace: str) -> None:
    """Create the named Secret if it does not already exist."""
    try:
        v1.read_namespaced_secret(name=name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            v1.create_namespaced_secret(
                namespace=namespace,
                body=k8s_client.V1Secret(
                    metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
                    string_data={},
                ),
            )
        else:
            raise


def _patch_configmap_key(v1: Any, name: str, namespace: str, key: str, value: str) -> None:
    _ensure_configmap(v1, name, namespace)
    cm = v1.read_namespaced_config_map(name=name, namespace=namespace)
    data = cm.data or {}
    data[key] = value
    cm.data = data
    v1.replace_namespaced_config_map(name=name, namespace=namespace, body=cm)


def _delete_configmap_key(v1: Any, name: str, namespace: str, key: str) -> bool:
    try:
        cm = v1.read_namespaced_config_map(name=name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise
    data = cm.data or {}
    if key not in data:
        return False
    del data[key]
    cm.data = data
    v1.replace_namespaced_config_map(name=name, namespace=namespace, body=cm)
    return True


def _patch_secret_key(v1: Any, name: str, namespace: str, key: str, value: str) -> None:
    _ensure_secret(v1, name, namespace)
    secret = v1.read_namespaced_secret(name=name, namespace=namespace)
    # string_data is write-only; read from data (base64) and rebuild as string_data patch
    v1.patch_namespaced_secret(
        name=name,
        namespace=namespace,
        body={"stringData": {key: value}},
    )


def _delete_secret_key(v1: Any, name: str, namespace: str, key: str) -> bool:
    import base64
    try:
        secret = v1.read_namespaced_secret(name=name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise
    data = dict(secret.data or {})
    if key not in data:
        return False
    del data[key]
    v1.replace_namespaced_secret(
        name=name,
        namespace=namespace,
        body=k8s_client.V1Secret(
            metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
            data=data,
        ),
    )
    return True


def _read_configmap_all(v1: Any, name: str, namespace: str) -> Dict[str, str]:
    try:
        cm = v1.read_namespaced_config_map(name=name, namespace=namespace)
        return cm.data or {}
    except ApiException as exc:
        if exc.status == 404:
            return {}
        raise


def _read_secret_key(v1: Any, name: str, namespace: str, key: str) -> Optional[str]:
    import base64
    try:
        secret = v1.read_namespaced_secret(name=name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise
    raw = (secret.data or {}).get(key)
    if raw is None:
        return None
    return base64.b64decode(raw).decode()


# ---------------------------------------------------------------------------
# CredentialStore
# ---------------------------------------------------------------------------

class CredentialStore:
    """CRUD store for credential-bearing objects backed by K8s ConfigMap + Secret.

    Inject _v1 (a MagicMock CoreV1Api) in tests to avoid real K8s calls.
    When GITOPS_SKIP_K8S=1, all operations use in-memory module-level dicts.
    """

    _v1: Optional[Any] = None  # injectable for tests

    # -----------------------------------------------------------------------
    # GitForge
    # -----------------------------------------------------------------------

    async def create_forge(self, spec: GitForgeCreate) -> GitForgeResponse:
        def _run():
            if _skip_k8s():
                _local_forges[spec.id] = {"id": spec.id, "forge_url": spec.forge_url, "is_default": spec.is_default}
                _local_forge_tokens[spec.id] = spec.git_token
                return
            v1 = _load_v1(self._v1)
            _patch_configmap_key(
                v1, "gitopsapi-forges", GITOPSAPI_NAMESPACE, spec.id,
                json.dumps({"id": spec.id, "forge_url": spec.forge_url, "is_default": spec.is_default}),
            )
            _patch_secret_key(v1, "gitopsapi-forge-tokens", GITOPSAPI_NAMESPACE, spec.id, spec.git_token)

        await asyncio.to_thread(_run)
        return GitForgeResponse(id=spec.id, forge_url=spec.forge_url, is_default=spec.is_default)

    async def list_forges(self) -> List[GitForgeResponse]:
        def _run() -> List[dict]:
            if _skip_k8s():
                return list(_local_forges.values())
            v1 = _load_v1(self._v1)
            data = _read_configmap_all(v1, "gitopsapi-forges", GITOPSAPI_NAMESPACE)
            return [json.loads(v) for v in data.values()]

        entries = await asyncio.to_thread(_run)
        return [GitForgeResponse(**e) for e in entries]

    async def get_forge(self, forge_id: str) -> Optional[GitForgeResponse]:
        def _run() -> Optional[dict]:
            if _skip_k8s():
                return _local_forges.get(forge_id)
            v1 = _load_v1(self._v1)
            data = _read_configmap_all(v1, "gitopsapi-forges", GITOPSAPI_NAMESPACE)
            raw = data.get(forge_id)
            return json.loads(raw) if raw else None

        entry = await asyncio.to_thread(_run)
        return GitForgeResponse(**entry) if entry else None

    async def delete_forge(self, forge_id: str) -> bool:
        def _run() -> bool:
            if _skip_k8s():
                existed = forge_id in _local_forges
                _local_forges.pop(forge_id, None)
                _local_forge_tokens.pop(forge_id, None)
                return existed
            v1 = _load_v1(self._v1)
            existed = _delete_configmap_key(v1, "gitopsapi-forges", GITOPSAPI_NAMESPACE, forge_id)
            _delete_secret_key(v1, "gitopsapi-forge-tokens", GITOPSAPI_NAMESPACE, forge_id)
            return existed

        return await asyncio.to_thread(_run)

    async def get_forge_token(self, forge_id: str) -> Optional[str]:
        """Return the git token for forge_id — for internal service use only."""
        def _run() -> Optional[str]:
            if _skip_k8s():
                return _local_forge_tokens.get(forge_id)
            v1 = _load_v1(self._v1)
            return _read_secret_key(v1, "gitopsapi-forge-tokens", GITOPSAPI_NAMESPACE, forge_id)

        return await asyncio.to_thread(_run)

    # -----------------------------------------------------------------------
    # GitRepo
    # -----------------------------------------------------------------------

    async def create_repo(self, spec: GitRepoCreate) -> GitRepoResponse:
        def _run():
            if _skip_k8s():
                _local_repos[spec.id] = {"id": spec.id, "forge_id": spec.forge_id, "repo_name": spec.repo_name}
                if spec.git_token:
                    _local_repo_tokens[spec.id] = spec.git_token
                return
            v1 = _load_v1(self._v1)
            _patch_configmap_key(
                v1, "gitopsapi-repos", GITOPSAPI_NAMESPACE, spec.id,
                json.dumps({"id": spec.id, "forge_id": spec.forge_id, "repo_name": spec.repo_name}),
            )
            if spec.git_token:
                _patch_secret_key(v1, "gitopsapi-repo-tokens", GITOPSAPI_NAMESPACE, spec.id, spec.git_token)

        await asyncio.to_thread(_run)
        return GitRepoResponse(id=spec.id, forge_id=spec.forge_id, repo_name=spec.repo_name)

    async def list_repos(self) -> List[GitRepoResponse]:
        def _run() -> List[dict]:
            if _skip_k8s():
                return list(_local_repos.values())
            v1 = _load_v1(self._v1)
            data = _read_configmap_all(v1, "gitopsapi-repos", GITOPSAPI_NAMESPACE)
            return [json.loads(v) for v in data.values()]

        entries = await asyncio.to_thread(_run)
        return [GitRepoResponse(**e) for e in entries]

    async def get_repo(self, repo_id: str) -> Optional[GitRepoResponse]:
        def _run() -> Optional[dict]:
            if _skip_k8s():
                return _local_repos.get(repo_id)
            v1 = _load_v1(self._v1)
            data = _read_configmap_all(v1, "gitopsapi-repos", GITOPSAPI_NAMESPACE)
            raw = data.get(repo_id)
            return json.loads(raw) if raw else None

        entry = await asyncio.to_thread(_run)
        return GitRepoResponse(**entry) if entry else None

    async def delete_repo(self, repo_id: str) -> bool:
        def _run() -> bool:
            if _skip_k8s():
                existed = repo_id in _local_repos
                _local_repos.pop(repo_id, None)
                _local_repo_tokens.pop(repo_id, None)
                return existed
            v1 = _load_v1(self._v1)
            existed = _delete_configmap_key(v1, "gitopsapi-repos", GITOPSAPI_NAMESPACE, repo_id)
            _delete_secret_key(v1, "gitopsapi-repo-tokens", GITOPSAPI_NAMESPACE, repo_id)
            return existed

        return await asyncio.to_thread(_run)

    async def get_repo_token(self, repo_id: str) -> Optional[str]:
        """Return per-repo token override — for internal service use only."""
        def _run() -> Optional[str]:
            if _skip_k8s():
                return _local_repo_tokens.get(repo_id)
            v1 = _load_v1(self._v1)
            return _read_secret_key(v1, "gitopsapi-repo-tokens", GITOPSAPI_NAMESPACE, repo_id)

        return await asyncio.to_thread(_run)

    # -----------------------------------------------------------------------
    # SopsKey
    # -----------------------------------------------------------------------

    async def generate_sops_key(self, key_id: str) -> SopsKeyResponse:
        """Generate a new age key pair and store it. Returns public key only."""
        pair = await asyncio.to_thread(_generate_age_key, key_id)
        await self._store_sops_key(key_id, pair.public_key, pair.private_key)
        return SopsKeyResponse(id=key_id, public_key=pair.public_key)

    async def import_sops_key(self, spec: SopsKeyImport) -> SopsKeyResponse:
        """Import an existing age key pair."""
        await self._store_sops_key(spec.id, spec.public_key, spec.private_key)
        return SopsKeyResponse(id=spec.id, public_key=spec.public_key)

    async def _store_sops_key(self, key_id: str, public_key: str, private_key: str) -> None:
        def _run():
            if _skip_k8s():
                _local_sops_meta[key_id] = public_key
                _local_sops_priv[key_id] = private_key
                return
            v1 = _load_v1(self._v1)
            _patch_configmap_key(v1, "gitopsapi-sops-keys", GITOPSAPI_NAMESPACE, key_id, public_key)
            _patch_secret_key(v1, "gitopsapi-sops-privkeys", GITOPSAPI_NAMESPACE, key_id, private_key)

        await asyncio.to_thread(_run)

    async def list_sops_keys(self) -> List[SopsKeyResponse]:
        def _run() -> Dict[str, str]:
            if _skip_k8s():
                return dict(_local_sops_meta)
            v1 = _load_v1(self._v1)
            return _read_configmap_all(v1, "gitopsapi-sops-keys", GITOPSAPI_NAMESPACE)

        data = await asyncio.to_thread(_run)
        return [SopsKeyResponse(id=k, public_key=v) for k, v in data.items()]

    async def get_sops_key(self, key_id: str) -> Optional[SopsKeyResponse]:
        def _run() -> Optional[str]:
            if _skip_k8s():
                return _local_sops_meta.get(key_id)
            v1 = _load_v1(self._v1)
            data = _read_configmap_all(v1, "gitopsapi-sops-keys", GITOPSAPI_NAMESPACE)
            return data.get(key_id)

        public_key = await asyncio.to_thread(_run)
        return SopsKeyResponse(id=key_id, public_key=public_key) if public_key else None

    async def delete_sops_key(self, key_id: str) -> bool:
        def _run() -> bool:
            if _skip_k8s():
                existed = key_id in _local_sops_meta
                _local_sops_meta.pop(key_id, None)
                _local_sops_priv.pop(key_id, None)
                return existed
            v1 = _load_v1(self._v1)
            existed = _delete_configmap_key(v1, "gitopsapi-sops-keys", GITOPSAPI_NAMESPACE, key_id)
            _delete_secret_key(v1, "gitopsapi-sops-privkeys", GITOPSAPI_NAMESPACE, key_id)
            return existed

        return await asyncio.to_thread(_run)

    async def get_sops_private_key(self, key_id: str) -> Optional[str]:
        """Return the private key — for internal bootstrap operations only; not exposed via API."""
        def _run() -> Optional[str]:
            if _skip_k8s():
                return _local_sops_priv.get(key_id)
            v1 = _load_v1(self._v1)
            return _read_secret_key(v1, "gitopsapi-sops-privkeys", GITOPSAPI_NAMESPACE, key_id)

        return await asyncio.to_thread(_run)
