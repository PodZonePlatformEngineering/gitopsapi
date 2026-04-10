import json
import os
from typing import List, Optional

from kubernetes import client as k8s_client  # type: ignore
from kubernetes import config as k8s_config  # type: ignore
from kubernetes.client.exceptions import ApiException  # type: ignore

from ..models.hypervisor import HypervisorSpec, HypervisorResponse, HypervisorListResponse

GITOPSAPI_NAMESPACE = os.environ.get("GITOPSAPI_NAMESPACE", "gitopsapi")
_CONFIGMAP_NAME = "gitopsapi-hypervisors"

_local_store: dict = {}   # in-memory fallback when GITOPS_SKIP_K8S=1


def _skip_k8s() -> bool:
    return os.environ.get("GITOPS_SKIP_K8S", "") == "1"


def _v1() -> k8s_client.CoreV1Api:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return k8s_client.CoreV1Api()


def _read_cm(v1: k8s_client.CoreV1Api) -> dict:
    try:
        cm = v1.read_namespaced_config_map(_CONFIGMAP_NAME, GITOPSAPI_NAMESPACE)
        return cm.data or {}
    except ApiException as e:
        if e.status == 404:
            return {}
        raise


def _write_cm(v1: k8s_client.CoreV1Api, data: dict) -> None:
    cm = k8s_client.V1ConfigMap(
        metadata=k8s_client.V1ObjectMeta(
            name=_CONFIGMAP_NAME,
            namespace=GITOPSAPI_NAMESPACE,
            labels={"app.kubernetes.io/managed-by": "gitopsapi"},
        ),
        data=data,
    )
    try:
        v1.replace_namespaced_config_map(_CONFIGMAP_NAME, GITOPSAPI_NAMESPACE, cm)
    except ApiException as e:
        if e.status == 404:
            v1.create_namespaced_config_map(GITOPSAPI_NAMESPACE, cm)
        else:
            raise


class HypervisorService:

    def _read(self) -> dict:
        if _skip_k8s():
            return dict(_local_store)
        return _read_cm(_v1())

    def _write(self, data: dict) -> None:
        if _skip_k8s():
            _local_store.clear()
            _local_store.update(data)
            return
        _write_cm(_v1(), data)

    async def create(self, spec: HypervisorSpec) -> HypervisorResponse:
        data = self._read()
        if spec.name in data:
            raise ValueError(f"Hypervisor {spec.name!r} already exists")
        data[spec.name] = spec.model_dump_json()
        self._write(data)
        return HypervisorResponse(**spec.model_dump())

    async def list(self) -> HypervisorListResponse:
        data = self._read()
        items = [HypervisorResponse(**json.loads(v)) for v in data.values()]
        return HypervisorListResponse(items=items)

    async def get(self, name: str) -> Optional[HypervisorResponse]:
        data = self._read()
        if name not in data:
            return None
        return HypervisorResponse(**json.loads(data[name]))

    async def update(self, name: str, spec: HypervisorSpec) -> HypervisorResponse:
        data = self._read()
        if name not in data:
            raise FileNotFoundError(f"Hypervisor {name!r} not found")
        if spec.name != name:
            raise ValueError(f"Cannot rename hypervisor via PATCH ({name!r} → {spec.name!r})")
        data[name] = spec.model_dump_json()
        self._write(data)
        return HypervisorResponse(**spec.model_dump())

    async def delete(self, name: str) -> None:
        data = self._read()
        if name not in data:
            raise FileNotFoundError(f"Hypervisor {name!r} not found")
        del data[name]
        self._write(data)
