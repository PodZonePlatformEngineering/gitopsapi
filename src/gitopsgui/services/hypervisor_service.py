import json
import os
from typing import List, Optional

from kubernetes import client as k8s_client  # type: ignore
from kubernetes import config as k8s_config  # type: ignore
from kubernetes.client.exceptions import ApiException  # type: ignore

from ..models.hypervisor import (
    HypervisorSpec, HypervisorResponse, HypervisorListResponse, HypervisorAuditData,
    BootstrapConfig, BootstrapStatus,
)

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

    async def run_audit(self, name: str) -> HypervisorResponse:
        """Run Egg audit script on hypervisor, parse result, persist to HypervisorSpec.

        Returns updated HypervisorResponse with populated audit field.
        Raises FileNotFoundError if hypervisor not registered.
        Raises ValueError if hypervisor has no ssh_credentials_ref.
        """
        from .egg_script_service import EggScriptService

        hyp = await self.get(name)
        if hyp is None:
            raise FileNotFoundError(f"Hypervisor {name!r} not found")
        if not hyp.ssh_credentials_ref:
            raise ValueError(
                f"Hypervisor {name!r} has no ssh_credentials_ref — cannot run audit"
            )

        raw = await EggScriptService().audit(name)

        audit = HypervisorAuditData(
            bridges=raw.get("bridges", []),
            storage_pools=raw.get("storage_pools", []),
            template_vms=raw.get("template_vms", []),
            proxmox_nodes=raw.get("proxmox_nodes", []),
            last_audited=raw.get("last_audited"),
        )

        updated_spec = hyp.model_copy(update={"audit": audit})
        return await self.update(name, updated_spec)

    async def bootstrap(self, name: str, config: BootstrapConfig) -> BootstrapStatus:
        """Orchestrate full Chicken bootstrap sequence on a registered hypervisor.

        Steps: audit → template (optional) → provision → platform_install
        Returns BootstrapStatus with completed steps and kubeconfig path.
        Raises FileNotFoundError if hypervisor not registered.
        Raises ValueError if hypervisor has no ssh_credentials_ref.
        """
        from .egg_script_service import EggScriptService, EggScriptError

        hyp = await self.get(name)
        if hyp is None:
            raise FileNotFoundError(f"Hypervisor {name!r} not found")
        if not hyp.ssh_credentials_ref:
            raise ValueError(
                f"Hypervisor {name!r} has no ssh_credentials_ref — cannot bootstrap"
            )

        egg = EggScriptService()
        steps_completed: list[str] = []

        # Step 1: Audit
        await self.run_audit(name)
        steps_completed.append("audit")

        # Step 2: Template VM (skippable)
        if not config.skip_template:
            await egg.create_template(name, {
                "TALOS_VERSION": config.talos_version,
                "TALOS_SCHEMA_ID": config.talos_schema_id,
                "VMID": str(config.template_vmid),
                "STORAGE": hyp.default_storage_pool,
                "BRIDGE": hyp.bridge,
            })
            steps_completed.append("template")

        # Step 3: Provision cluster
        provision_result = await egg.provision_cluster(name, {
            "CLUSTER_NAME": config.cluster_name,
            "VIP": config.vip,
            "TEMPLATE_VMID": str(config.template_vmid),
            "NEW_VMID": str(config.new_vmid),
            "STORAGE": hyp.default_storage_pool,
            "BRIDGE": hyp.bridge,
            "CPU": str(config.cpu),
            "MEMORY_MB": str(config.memory_mb),
            "DISK_GB": str(config.disk_gb),
            "TALOS_VERSION": config.talos_version,
            "TALOS_SCHEMA_ID": config.talos_schema_id,
            "K8S_VERSION": config.kubernetes_version,
            "INSTALL_DISK": config.install_disk,
        })
        steps_completed.append("provision")

        # Step 4: Download kubeconfig
        await egg.download_kubeconfig(name, provision_result["kubeconfig_path"])
        steps_completed.append("download_kubeconfig")

        # Step 5: Platform install
        await egg.platform_install(name, {
            "KUBECONFIG_PATH": provision_result["kubeconfig_path"],
            "CLUSTER_CHART_REPO_URL": config.cluster_chart_repo_url,
            "CLUSTER_CHART_VERSION": config.cluster_chart_version,
        })
        steps_completed.append("platform_install")

        return BootstrapStatus(
            hypervisor=name,
            cluster_name=config.cluster_name,
            status="complete",
            steps_completed=steps_completed,
            kubeconfig_secret_name=f"{config.cluster_name}-kubeconfig",
        )

    async def get_ssh_context(self, name: str) -> dict:
        """Return {'host_ip': ..., 'ssh_credentials_ref': ...} for a named hypervisor.

        Raises FileNotFoundError if hypervisor not registered.
        Raises ValueError if hypervisor has no ssh_credentials_ref set.
        """
        hyp = await self.get(name)
        if hyp is None:
            raise FileNotFoundError(f"Hypervisor {name!r} not found")
        if not hyp.ssh_credentials_ref:
            raise ValueError(f"Hypervisor {name!r} has no ssh_credentials_ref set")
        return {"host_ip": hyp.host_ip, "ssh_credentials_ref": hyp.ssh_credentials_ref}
