"""
GITGUI-008 — Flux status queries and interrogation service.
Uses the kubernetes Python client with per-cluster kubeconfigs.
"""

import asyncio
from typing import List, Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from ..models.status import (
    AggregateStatus,
    BlockingResource,
    ClusterFluxStatus,
    LogResponse,
    ResourceDetail,
    ResourceSummary,
    UndeployStatus,
)


def _load_kubeconfig(cluster: str) -> client.CoreV1Api:
    """Load kubeconfig for the named cluster and return a CoreV1Api client."""
    config.load_kube_config(context=cluster)
    return client.CoreV1Api()


class K8sService:
    async def list_all_flux_status(self) -> AggregateStatus:
        raise NotImplementedError

    async def get_cluster_flux_status(self, cluster: str) -> ClusterFluxStatus:
        raise NotImplementedError

    async def list_resources(
        self,
        cluster: str,
        kind: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> List[ResourceSummary]:
        raise NotImplementedError

    async def describe_resource(
        self, cluster: str, kind: str, namespace: str, name: str
    ) -> ResourceDetail:
        raise NotImplementedError

    async def get_logs(
        self,
        cluster: str,
        namespace: str,
        pod_name: str,
        container: Optional[str] = None,
        tail_lines: int = 100,
    ) -> LogResponse:
        raise NotImplementedError

    async def get_undeploy_status(self, name: str, cluster: str) -> UndeployStatus:
        """GITGUI-027 — Check whether the application namespace has been removed.

        Returns namespace_phase: gone | active | terminating | unknown.
        When terminating, lists namespace finalizers and any in-namespace
        resources that still carry finalizers.
        """
        def _run() -> UndeployStatus:
            try:
                v1 = _load_kubeconfig(cluster)
            except Exception:
                return UndeployStatus(name=name, cluster=cluster, namespace_phase="unknown")

            # Check namespace existence and phase
            try:
                ns = v1.read_namespace(name)
            except ApiException as e:
                if e.status == 404:
                    return UndeployStatus(name=name, cluster=cluster, namespace_phase="gone")
                return UndeployStatus(name=name, cluster=cluster, namespace_phase="unknown")

            phase = (ns.status.phase or "").lower()
            ns_finalizers: List[str] = ns.metadata.finalizers or []

            if phase != "terminating":
                return UndeployStatus(
                    name=name,
                    cluster=cluster,
                    namespace_phase=phase or "unknown",
                    finalizers=ns_finalizers,
                )

            # Namespace is terminating — find resources with finalizers inside it
            blocking: List[BlockingResource] = []
            try:
                for pod in (v1.list_namespaced_pod(name).items or []):
                    f = pod.metadata.finalizers or []
                    if f:
                        blocking.append(BlockingResource(kind="Pod", name=pod.metadata.name, finalizers=f))
                for svc in (v1.list_namespaced_service(name).items or []):
                    f = svc.metadata.finalizers or []
                    if f:
                        blocking.append(BlockingResource(kind="Service", name=svc.metadata.name, finalizers=f))
                for pvc in (v1.list_namespaced_persistent_volume_claim(name).items or []):
                    f = pvc.metadata.finalizers or []
                    if f:
                        blocking.append(BlockingResource(kind="PersistentVolumeClaim", name=pvc.metadata.name, finalizers=f))
            except ApiException:
                pass  # best-effort; return what we have

            return UndeployStatus(
                name=name,
                cluster=cluster,
                namespace_phase="terminating",
                finalizers=ns_finalizers,
                blocking_resources=blocking,
            )

        return await asyncio.to_thread(_run)
