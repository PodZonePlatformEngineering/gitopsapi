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

from ..models.cluster import (
    ClusterSpec, ClusterResponse, ClusterStatus,
    ClusterSuspendResponse, ClusterDecommissionResponse,
    PlatformSpec,
)
from .git_service import GitService
from .github_service import GitHubService

_CLUSTER_CHARTS_BASE = "gitops/cluster-charts"
_CLUSTERS_BASE = "clusters"
_MGMT_CLUSTERS_PATH = "clusters/management/clusters.yaml"

# Reviewers determined by target (cluster changes always need cluster_operator approval)
_CLUSTER_REVIEWERS: List[str] = []  # populated from env/config at runtime
_CLUSTER_STAGE_LABEL = "stage:production"


# ---------------------------------------------------------------------------
# clusters.yaml helpers
# ---------------------------------------------------------------------------

def _set_kustomization_suspended(content: str, cluster_name: str) -> str:
    """Insert suspend: true into the named Kustomization's spec block."""
    kust_name = f"{cluster_name}-cluster"
    parts = content.split("\n---")
    result = []
    for part in parts:
        if f"name: {kust_name}" in part and "kind: Kustomization" in part:
            part = part.replace("spec:\n", "spec:\n  suspend: true\n", 1)
        result.append(part)
    return "\n---".join(result)


def _remove_kustomization(content: str, cluster_name: str) -> str:
    """Remove the YAML document for cluster_name-cluster from a multi-doc file."""
    kust_name = f"{cluster_name}-cluster"
    parts = content.split("\n---")
    kept = [
        p for p in parts
        if not (f"name: {kust_name}" in p and "kind: Kustomization" in p)
    ]
    return "\n---".join(kept)


def _cluster_values_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/{name}-values.yaml"


def _cluster_yaml_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/{name}.yaml"


def _kustomization_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/kustomization.yaml"


def _kustomizeconfig_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/kustomizeconfig.yaml"


def _render_values(spec: ClusterSpec) -> str:
    """Render cluster-chart values YAML.

    Matches the schema used by actual cluster-chart values files:
      cluster.name, network.ip_ranges, controlplane.*, worker.machine_count
    Top-level GitOpsAPI metadata fields (platform, vip, etc.) are also stored
    here for roundtrip fidelity when the API reads back a cluster spec.
    """
    # cluster-chart consumed fields
    data: dict = {
        "cluster": {"name": spec.name},
        "network": {"ip_ranges": [spec.ip_range]},
        "controlplane": {
            "endpoint_ip": spec.vip,
            "machine_count": spec.dimensions.control_plane_count,
        },
        "worker": {"machine_count": spec.dimensions.worker_count},
    }
    if spec.extra_manifests:
        data["controlplane"]["extra_manifests"] = spec.extra_manifests
    if spec.allow_scheduling_on_control_planes:
        data["controlplane"]["allow_scheduling_on_control_planes"] = True

    # proxmox: section consumed by cluster-chart templates
    if spec.platform and spec.platform.type == "proxmox":
        template_node = spec.platform.talos_template.node or spec.platform.nodes[0]
        data["proxmox"] = {
            "allowed_nodes": spec.platform.nodes,
            "template": {
                "sourcenode": template_node,
                "template_vmid": spec.platform.talos_template.vmid,
            },
            "vm": {"bridge": spec.platform.bridge},
        }

    # GitOpsAPI metadata (roundtrip fields — not consumed by cluster-chart)
    if spec.platform:
        data["platform"] = {
            "name": spec.platform.name,
            "type": spec.platform.type,
            "endpoint": spec.platform.endpoint,
            "nodes": spec.platform.nodes,
            "talos_template": {
                "name": spec.platform.talos_template.name,
                "version": spec.platform.talos_template.version,
                "vmid": spec.platform.talos_template.vmid,
                "node": spec.platform.talos_template.node,
            },
            "credentials_ref": spec.platform.credentials_ref,
            "bridge": spec.platform.bridge,
        }
    data["vip"] = spec.vip
    if spec.gitops_repo_url:
        data["gitops_repo_url"] = spec.gitops_repo_url
    data["sops_secret_ref"] = spec.sops_secret_ref
    data["allow_scheduling_on_control_planes"] = spec.allow_scheduling_on_control_planes
    if spec.external_hosts:
        data["external_hosts"] = spec.external_hosts
    data["dimensions"] = {
        "control_plane_count": spec.dimensions.control_plane_count,
        "worker_count": spec.dimensions.worker_count,
        "cpu_per_node": spec.dimensions.cpu_per_node,
        "memory_gb_per_node": spec.dimensions.memory_gb_per_node,
        "boot_volume_gb": spec.dimensions.boot_volume_gb,
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
              version: 0.1.20
          valuesFrom:
            - kind: ConfigMap
              name: {name}-values
          interval: 10m0s
    """)


def _render_kustomization(name: str) -> str:
    return textwrap.dedent(f"""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        namespace: {name}
        resources:
          - {name}.yaml
          - proxmox-secret.yaml
        configMapGenerator:
          - name: {name}-values
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

        raw_platform = data.get("platform")
        platform = PlatformSpec(**raw_platform) if isinstance(raw_platform, dict) else None

        spec = ClusterSpec(
            name=name,
            platform=platform,
            vip=data.get("vip", ""),
            ip_range=data.get("network", {}).get("ip_ranges", [""])[0],
            dimensions=data.get("dimensions", {}),
            gitops_repo_url=data.get("gitops_repo_url", ""),
            sops_secret_ref=data.get("sops_secret_ref", ""),
            allow_scheduling_on_control_planes=data.get("allow_scheduling_on_control_planes", False),
            external_hosts=data.get("external_hosts", []),
        )
        return ClusterResponse(name=name, spec=spec)

    async def _provision_gitops_repos(self, spec: ClusterSpec) -> ClusterSpec:
        """TR-039: Create {cluster}-infra and {cluster}-apps as private repos on the git forge.

        Returns an updated spec with gitops_repo_url populated from the created infra repo.
        Raises RuntimeError if repo creation fails (cluster provisioning must not proceed).
        """
        infra_name = f"{spec.name}-infra"
        apps_name = f"{spec.name}-apps"

        infra_url = await self._gh.create_repo(
            name=infra_name,
            description=f"Flux infrastructure manifests for {spec.name} cluster",
            private=True,
        )
        await self._gh.create_repo(
            name=apps_name,
            description=f"Application workloads for {spec.name} cluster",
            private=True,
        )

        return spec.model_copy(update={"gitops_repo_url": infra_url})

    async def create_cluster(self, spec: ClusterSpec) -> ClusterResponse:
        if spec.managed_gitops:
            # TR-039: provision repos first — cluster creation fails if this fails
            spec = await self._provision_gitops_repos(spec)

        branch = f"cluster/provision-{spec.name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        await self._git.write_file(_cluster_values_path(spec.name), _render_values(spec))
        await self._git.write_file(_cluster_yaml_path(spec.name), _render_cluster_yaml(spec.name))
        await self._git.write_file(_kustomization_path(spec.name), _render_kustomization(spec.name))
        await self._git.write_file(_kustomizeconfig_path(spec.name), _KUSTOMIZECONFIG)

        await self._git.commit(f"chore: provision cluster {spec.name}")
        await self._git.push()

        pr_body = (
            f"Automated cluster provisioning for `{spec.name}`.\n\n"
            f"IP range: {spec.ip_range}\n\n"
        )
        if spec.managed_gitops:
            pr_body += (
                f"**GitOps repos provisioned (TR-039)**:\n"
                f"- `{spec.name}-infra`: {spec.gitops_repo_url}\n"
                f"- `{spec.name}-apps`: (companion workload repo)\n\n"
                f"**Next steps** (CC-053 — not yet automated):\n"
                f"- Generate and register deploy keys for both repos\n"
                f"- Generate per-cluster SOPS age key, encrypt with management key\n"
                f"- Bootstrap Flux on the new cluster\n"
            )

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Provision cluster: {spec.name}",
            body=pr_body,
            labels=["cluster", _CLUSTER_STAGE_LABEL],
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
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterResponse(name=name, spec=spec, pr_url=pr_url)

    async def suspend_cluster(self, name: str) -> ClusterSuspendResponse:
        """PR: add suspend: true to the cluster's Kustomization in ManagementCluster/clusters.yaml.

        When Flux reconciles the PR, it suspends the cluster's HelmRelease reconciliation
        without deleting any resources. The cluster continues to run unmanaged.
        """
        branch = f"cluster/suspend-{name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        content = await self._git.read_file(_MGMT_CLUSTERS_PATH)
        updated = _set_kustomization_suspended(content, name)
        await self._git.write_file(_MGMT_CLUSTERS_PATH, updated)
        await self._git.commit(f"chore: suspend cluster {name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Suspend cluster: {name}",
            body=(
                f"Sets `spec.suspend: true` on the `{name}-cluster` Kustomization.\n\n"
                f"The cluster continues to run but Flux stops reconciling its HelmRelease.\n"
                f"Reverse with a follow-up PR removing the suspend flag, or proceed to "
                f"`DELETE /clusters/{name}` to decommission."
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterSuspendResponse(name=name, pr_url=pr_url)

    async def decommission_cluster(self, name: str) -> ClusterDecommissionResponse:
        """PR: remove cluster-chart files + Kustomization entry. Archives infra/apps repos.

        When the PR is merged, Flux prunes the {name}-cluster Kustomization which cascades to
        deleting the HelmRelease → CAPI deprovisions all cluster machines.
        Repos {name}-infra and {name}-apps are archived (read-only) before the PR is opened.
        """
        branch = f"cluster/decommission-{name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        # Remove cluster-chart files
        chart_files = [
            f"{_CLUSTER_CHARTS_BASE}/{name}/{name}-values.yaml",
            f"{_CLUSTER_CHARTS_BASE}/{name}/{name}.yaml",
            f"{_CLUSTER_CHARTS_BASE}/{name}/kustomization.yaml",
            f"{_CLUSTER_CHARTS_BASE}/{name}/kustomizeconfig.yaml",
        ]
        for path in chart_files:
            try:
                await self._git.delete_file(path)
            except FileNotFoundError:
                pass  # already absent — idempotent

        # Remove the Kustomization entry from ManagementCluster/clusters.yaml
        content = await self._git.read_file(_MGMT_CLUSTERS_PATH)
        updated = _remove_kustomization(content, name)
        await self._git.write_file(_MGMT_CLUSTERS_PATH, updated)

        await self._git.commit(f"chore: decommission cluster {name}")
        await self._git.push()

        # Archive repos (read-only, history preserved) before PR so the intent is clear
        archived: List[str] = []
        for repo_suffix in ("infra", "apps"):
            repo_name = f"{name}-{repo_suffix}"
            await self._gh.archive_repo(repo_name)
            archived.append(repo_name)

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Decommission cluster: {name}",
            body=(
                f"Removes `{name}` cluster-chart files and its Kustomization entry from "
                f"`clusters/ManagementCluster/clusters.yaml`.\n\n"
                f"**Effect on merge**: Flux prunes the `{name}-cluster` Kustomization → "
                f"HelmRelease deleted → CAPI deprovisions all `{name}` machines.\n\n"
                f"**Repos archived** (read-only):\n"
                + "".join(f"- `{r}`\n" for r in archived)
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterDecommissionResponse(name=name, pr_url=pr_url, archived_repos=archived)
