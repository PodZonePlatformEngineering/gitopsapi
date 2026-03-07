"""
GITGUI-005 — Cluster object reader/writer.

Gitops repo layout:
  clusters/<name>/                          — Flux kustomization wiring
  gitops/cluster-charts/<name>/<name>-values.yaml  — cluster-chart Helm values

Writes go via feature branch + PR labelled 'cluster' + stage label.
"""

import textwrap
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import yaml

from ..models.cluster import ClusterSpec, ClusterResponse, ClusterStatus
from .git_service import GitService
from .github_service import GitHubService

_CLUSTER_CHARTS_BASE = "gitops/cluster-charts"
_CLUSTERS_BASE = "clusters"

# Reviewers determined by target (cluster changes always need cluster_operator approval)
_CLUSTER_REVIEWERS: List[str] = []  # populated from env/config at runtime


def _cluster_values_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/{name}-values.yaml"


def _cluster_yaml_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/{name}.yaml"


def _kustomization_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/kustomization.yaml"


def _kustomizeconfig_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/kustomizeconfig.yaml"


def _render_values(spec: ClusterSpec) -> str:
    data = {
        "cluster": {"name": spec.name},
        "network": {"ip_ranges": [spec.ip_range]},
        "controlplane": {"machine_count": spec.dimensions.control_plane_count},
        "worker": {"machine_count": spec.dimensions.worker_count},
    }
    return yaml.dump(data, default_flow_style=False)


def _render_cluster_yaml(name: str) -> str:
    return textwrap.dedent(f"""\
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {name}
        ---
        apiVersion: source.toolkit.fluxcd.io/v1beta2
        kind: HelmRepository
        metadata:
          name: podzone-charts
          namespace: flux-system
        spec:
          interval: 10m0s
          url: https://motttt.github.io/cluster09/
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2beta2
        kind: HelmRelease
        metadata:
          name: {name}
          namespace: flux-system
        spec:
          targetNamespace: {name}
          chart:
            spec:
              chart: cluster-chart
              sourceRef:
                kind: HelmRepository
                name: podzone-charts
              version: 0.1.19
          valuesFrom:
            - kind: ConfigMap
              name: {name}-values
          interval: 10m0s
    """)


def _render_kustomization(name: str) -> str:
    return textwrap.dedent(f"""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        resources:
          - {name}.yaml
        configMapGenerator:
          - name: {name}-values
            namespace: flux-system
            files:
              - values.yaml={name}-values.yaml
        configurations:
          - kustomizeconfig.yaml
    """)


_KUSTOMIZECONFIG = textwrap.dedent("""\
    nameReference:
    - kind: ConfigMap
      version: v1
      fieldSpecs:
      - path: spec/valuesFrom/name
        kind: HelmRelease
""")


class ClusterService:
    def __init__(self):
        self._git = GitService()
        self._gh = GitHubService()

    async def list_clusters(self) -> List[ClusterResponse]:
        names = await self._git.list_dir(_CLUSTER_CHARTS_BASE)
        results = []
        for name in names:
            cluster = await self.get_cluster(name)
            if cluster:
                results.append(cluster)
        return results

    async def get_cluster(self, name: str) -> Optional[ClusterResponse]:
        try:
            raw = await self._git.read_file(_cluster_values_path(name))
            data = yaml.safe_load(raw)
        except FileNotFoundError:
            return None

        spec = ClusterSpec(
            name=name,
            platform=data.get("platform", "proxmox"),
            ip_range=data.get("network", {}).get("ip_ranges", [""])[0],
            dimensions=data.get("dimensions", {}),
            gitops_repo_url=data.get("gitops_repo_url", ""),
            sops_secret_ref=data.get("sops_secret_ref", ""),
        )
        return ClusterResponse(name=name, spec=spec)

    async def create_cluster(self, spec: ClusterSpec) -> ClusterResponse:
        branch = f"cluster/provision-{spec.name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        await self._git.write_file(_cluster_values_path(spec.name), _render_values(spec))
        await self._git.write_file(_cluster_yaml_path(spec.name), _render_cluster_yaml(spec.name))
        await self._git.write_file(_kustomization_path(spec.name), _render_kustomization(spec.name))
        await self._git.write_file(_kustomizeconfig_path(spec.name), _KUSTOMIZECONFIG)

        await self._git.commit(f"chore: provision cluster {spec.name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Provision cluster: {spec.name}",
            body=f"Automated cluster provisioning for `{spec.name}`.\n\nIP range: {spec.ip_range}",
            labels=["cluster", "stage:production"],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterResponse(name=spec.name, spec=spec, pr_url=pr_url)

    async def update_cluster(self, name: str, spec: ClusterSpec) -> ClusterResponse:
        branch = f"cluster/update-{name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        await self._git.write_file(_cluster_values_path(name), _render_values(spec))
        await self._git.commit(f"chore: update cluster {name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Update cluster: {name}",
            body=f"Cluster spec update for `{name}`.",
            labels=["cluster", "stage:production"],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterResponse(name=name, spec=spec, pr_url=pr_url)
