"""
GITGUI-008 — Flux status queries and interrogation service.
Uses the kubernetes Python client with per-cluster kubeconfigs.
"""

from typing import List, Optional

from ..models.status import AggregateStatus, ClusterFluxStatus, ResourceSummary, ResourceDetail, LogResponse


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
