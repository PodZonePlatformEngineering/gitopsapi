"""
GITGUI-005 — Cluster object reader/writer.

Gitops repo layout:
  clusters/<name>/                          — Flux kustomization wiring
  gitops/cluster-charts/<name>/<name>-values.yaml  — cluster-chart Helm values

Writes go via feature branch + PR labelled 'cluster' + stage label.
"""

import os
import textwrap
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import yaml

from ..models.cluster import (
    ClusterSpec, ClusterResponse, ClusterStatus,
    ClusterSuspendResponse, ClusterDecommissionResponse,
    IngressConnectorSpec, IngressConnectorResponse,
    PlatformSpec,
)
from ..models.deploy_key import ClusterBootstrapRequest, ClusterBootstrapResponse
from .git_service import GitService
from .github_service import GitHubService
from . import repo_router

CLUSTER_CHART_REPO_URL = os.environ.get("GITOPS_CLUSTER_CHART_REPO_URL", "")

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
    if spec.ingress_connector:
        ic = spec.ingress_connector
        data["ingress_connector"] = {
            "enabled": ic.enabled,
            "type": ic.type,
            "tunnel_id": ic.tunnel_id,
            "replicas": ic.replicas,
            "namespace": ic.namespace,
            "token_secret_ref": {
                "name": ic.token_secret_ref.name,
                "key": ic.token_secret_ref.key,
            },
        }
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
          url: {CLUSTER_CHART_REPO_URL}
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

_CLOUDFLARED_APPS_PATH = "gitops/gitops-apps/cloudflared"


def _render_cloudflared_yaml(connector: IngressConnectorSpec) -> str:
    """Render cloudflared HelmRelease + HelmRepository + Namespace for the cluster apps repo."""
    return textwrap.dedent(f"""\
        ---
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {connector.namespace}
        ---
        apiVersion: source.toolkit.fluxcd.io/v1
        kind: HelmRepository
        metadata:
          name: cloudflare
          namespace: flux-system
        spec:
          interval: 24h
          url: https://cloudflare.github.io/helm-charts
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: cloudflared
          namespace: {connector.namespace}
        spec:
          interval: 30m
          chart:
            spec:
              chart: cloudflare-tunnel-remote
              version: "0.1.2"
              sourceRef:
                kind: HelmRepository
                name: cloudflare
                namespace: flux-system
          valuesFrom:
            - kind: Secret
              name: {connector.token_secret_ref.name}
              valuesKey: {connector.token_secret_ref.key}
              targetPath: cloudflare.tunnel_token
    """)


def _render_cloudflared_apps_kustomization() -> str:
    """Render kustomization.yaml for the cloudflared app directory."""
    return textwrap.dedent("""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        resources:
          - cloudflared.yaml
    """)


def _render_cloudflared_flux_kustomization(cluster_name: str) -> str:
    """Render the Flux Kustomization entry to append to {cluster}-infra/{name}-apps.yaml."""
    return textwrap.dedent(f"""\
        ---
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: cloudflared
          namespace: flux-system
        spec:
          interval: 1h
          retryInterval: 1m
          timeout: 5m
          sourceRef:
            kind: GitRepository
            name: {cluster_name}-apps
          path: ./{_CLOUDFLARED_APPS_PATH}
          prune: true
          decryption:
            provider: sops
            secretRef:
              name: sops-age
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

        raw_ic = data.get("ingress_connector")
        ingress_connector = IngressConnectorSpec(**raw_ic) if isinstance(raw_ic, dict) else None

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
            ingress_connector=ingress_connector,
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

    async def wire_ingress_connector(self, cluster_name: str) -> IngressConnectorResponse:
        """CC-068 Phase 1: render cloudflared manifests into {cluster}-apps and {cluster}-infra.

        Writes to two repos:
        - {cluster}-apps: gitops/gitops-apps/cloudflared/ (HelmRelease + kustomization)
        - {cluster}-infra: appends cloudflared Flux Kustomization to clusters/{name}/{name}-apps.yaml

        Raises ValueError if the cluster has no ingress_connector configured or it is disabled.
        """
        cluster = await self.get_cluster(cluster_name)
        if not cluster:
            raise FileNotFoundError(f"Cluster {cluster_name!r} not found")

        connector = cluster.spec.ingress_connector
        if not connector or not connector.enabled:
            raise ValueError(
                f"Cluster {cluster_name!r} has no ingress_connector configured or it is disabled. "
                "Set ingress_connector.enabled=true in the cluster spec first."
            )

        branch = f"cluster/ingress-connector-{cluster_name}-{uuid.uuid4().hex[:8]}"

        # --- apps repo: write cloudflared manifests ---
        git_apps = repo_router.git_for_apps(cluster_name)
        gh_apps = repo_router.github_for_apps(cluster_name)

        await git_apps.create_branch(branch)
        await git_apps.write_file(
            f"{_CLOUDFLARED_APPS_PATH}/cloudflared.yaml",
            _render_cloudflared_yaml(connector),
        )
        await git_apps.write_file(
            f"{_CLOUDFLARED_APPS_PATH}/kustomization.yaml",
            _render_cloudflared_apps_kustomization(),
        )
        await git_apps.commit(f"feat: add cloudflared ingress connector for {cluster_name}")
        await git_apps.push()

        apps_pr_url = await gh_apps.create_pr(
            branch=branch,
            title=f"feat: wire cloudflared ingress connector — {cluster_name}",
            body=(
                f"Adds cloudflared HelmRelease to `{cluster_name}-apps`.\n\n"
                f"**Tunnel token secret**: `{connector.token_secret_ref.name}` "
                f"(key: `{connector.token_secret_ref.key}`) must exist in namespace "
                f"`{connector.namespace}` before this can reconcile.\n\n"
                f"**Companion PR** in `{cluster_name}-infra` adds the Flux Kustomization entry.\n\n"
                f"CC-068 Phase 1"
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        # --- infra repo: append Flux Kustomization entry to {name}-apps.yaml ---
        git_infra = repo_router.git_for_infra(cluster_name)
        gh_infra = repo_router.github_for_infra(cluster_name)

        await git_infra.create_branch(branch)
        apps_yaml_path = f"clusters/{cluster_name}/{cluster_name}-apps.yaml"
        existing = await git_infra.read_file(apps_yaml_path)
        updated = existing.rstrip("\n") + "\n" + _render_cloudflared_flux_kustomization(cluster_name)
        await git_infra.write_file(apps_yaml_path, updated)
        await git_infra.commit(f"feat: add cloudflared Kustomization for {cluster_name}")
        await git_infra.push()

        infra_pr_url = await gh_infra.create_pr(
            branch=branch,
            title=f"feat: wire cloudflared Flux Kustomization — {cluster_name}",
            body=(
                f"Adds `cloudflared` Flux Kustomization to `clusters/{cluster_name}/{cluster_name}-apps.yaml`.\n\n"
                f"**Merge after** the companion `{cluster_name}-apps` PR so the HelmRelease exists "
                f"before Flux reconciles this Kustomization.\n\n"
                f"CC-068 Phase 1"
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return IngressConnectorResponse(
            name=cluster_name,
            apps_pr_url=apps_pr_url,
            infra_pr_url=infra_pr_url,
        )

    async def bootstrap_cluster(
        self,
        cluster_name: str,
        request: ClusterBootstrapRequest,
        _sops_svc=None,       # injectable for tests
        _deploy_key_svc=None,  # injectable for tests
    ) -> ClusterBootstrapResponse:
        """CC-053b — Install SOPS key + SSH deploy keys on a newly provisioned cluster.

        Requires the cluster to be running and reachable via the CAPI management cluster.
        Kubeconfig is extracted from the CAPI management secret — no local kubeconfig needed.

        Orchestrates:
        1. SOPS age key generation, encryption, management-infra PR, cluster Secret install
        2. SSH deploy key for {cluster}-infra → GitHub + cluster flux-system Secret + GitRepository CR
        3. SSH deploy key for {cluster}-apps  → GitHub + cluster flux-system Secret + GitRepository CR

        After this call, Flux can be bootstrapped on the cluster pointing at {cluster}-infra
        and it will have credentials to pull from both repos.
        """
        import yaml as _yaml
        from .kubeconfig_service import KubeconfigService, rewrite_kubeconfig_server
        from .sops_service import SOPSService
        from .deploy_key_service import DeployKeyService, SKIP_K8S
        from .github_service import GITHUB_ORG
        from ..models.sops import SOPSBootstrapRequest
        from ..models.deploy_key import GitAccessRequest

        cluster = await self.get_cluster(cluster_name)
        if not cluster:
            raise FileNotFoundError(f"Cluster {cluster_name!r} not found in registry")

        # Extract kubeconfig from CAPI (raises HTTPException 503/404 if not available)
        kubeconfig_dict: dict = {}
        if not SKIP_K8S:
            kubeconfig_yaml = await KubeconfigService().extract_kubeconfig(cluster_name)
            if cluster.spec.bastion:
                b = cluster.spec.bastion
                kubeconfig_yaml = rewrite_kubeconfig_server(
                    kubeconfig_yaml, b.ip, b.api_port
                )
            kubeconfig_dict = _yaml.safe_load(kubeconfig_yaml)

        # 1. SOPS bootstrap: generate key, encrypt, PR on management-infra, install Secret
        sops_svc = _sops_svc if _sops_svc is not None else SOPSService()
        sops_result = await sops_svc.sops_bootstrap(
            cluster_name,
            SOPSBootstrapRequest(
                management_sops_public_key=request.management_sops_public_key
            ),
        )

        # 2 & 3. SSH deploy keys for infra and apps repos
        dks = _deploy_key_svc if _deploy_key_svc is not None else DeployKeyService()
        org = GITHUB_ORG

        infra_name = repo_router.infra_repo_name(cluster_name)
        apps_name = repo_router.apps_repo_name(cluster_name)

        infra_result = await dks.configure_repository_access(
            infra_name,
            GitAccessRequest(
                cluster=cluster_name,
                git_url=f"git@github.com:{org}/{infra_name}.git",
            ),
            kubeconfig_dict=kubeconfig_dict or None,
        )
        apps_result = await dks.configure_repository_access(
            apps_name,
            GitAccessRequest(
                cluster=cluster_name,
                git_url=f"git@github.com:{org}/{apps_name}.git",
            ),
            kubeconfig_dict=kubeconfig_dict or None,
        )

        return ClusterBootstrapResponse(
            cluster_name=cluster_name,
            sops_public_key=sops_result.sops_public_key,
            sops_mgmt_pr_url=sops_result.mgmt_pr_url,
            infra_key_id=infra_result.github_key_id,
            apps_key_id=apps_result.github_key_id,
            secrets_created=not SKIP_K8S,
        )
