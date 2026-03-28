"""
GITGUI — Application-Cluster-Configuration service.

Controls which applications are deployed to which clusters, with per-cluster overrides.

Multi-repo layout:
  {cluster}-infra repo:
    clusters/<cluster>/<cluster>-apps.yaml       — Kustomization entry for each app

  {cluster}-apps repo:
    gitops/gitops-apps/<app>/<app>-values-<cluster>.yaml  — per-cluster values override

Two use cases:
  1. Deployment link: for external/3rd-party apps, POST is the deployment action
  2. Per-cluster config holder: values_override carries cluster-specific settings

Writes go via feature branch + PR labelled 'application-config'.
PR for Kustomization entry → {cluster}-infra repo.
PR for values override → {cluster}-apps repo.
"""

import re
import textwrap
import uuid
from typing import List, Optional, Tuple

import yaml

from ..models.application_config import (
    ApplicationDeployment,
    ApplicationDeploymentResponse,
    PatchApplicationDeployment,
    HTTPRouteSpec,
)
from .repo_router import git_for_apps, git_for_infra, github_for_apps, github_for_infra

_APPS_BASE = "gitops/gitops-apps"


def _config_id(app_id: str, cluster_id: str) -> str:
    return f"{app_id}-{cluster_id}"


def _cluster_apps_path(cluster: str) -> str:
    return f"clusters/{cluster}/{cluster}-apps.yaml"


def _values_override_path(app_id: str, cluster_id: str) -> str:
    return f"{_APPS_BASE}/{app_id}/{app_id}-values-{cluster_id}.yaml"


def _render_kustomization_entry(spec: ApplicationDeployment) -> str:
    """Render a Kustomization YAML document for one app→cluster assignment."""
    source_ref_name = spec.gitops_source_ref or f"{spec.cluster_id}-apps"
    annotations: dict = {}
    if spec.external_hosts:
        annotations["gitopsapi.io/external-hosts"] = ",".join(spec.external_hosts)
    if spec.secret_refs:
        annotations["gitopsapi.io/secret-refs"] = ",".join(
            f"{r.namespace}/{r.name}" if r.namespace else r.name for r in spec.secret_refs
        )
    if spec.config_map_refs:
        annotations["gitopsapi.io/configmap-refs"] = ",".join(
            f"{r.namespace}/{r.name}" if r.namespace else r.name for r in spec.config_map_refs
        )
    annotations_block = ""
    if annotations:
        lines = "\n".join(f'    {k}: "{v}"' for k, v in annotations.items())
        annotations_block = f"  annotations:\n{lines}\n"
    return (
        f"---\n"
        f"apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
        f"kind: Kustomization\n"
        f"metadata:\n"
        f"  name: {spec.app_id}\n"
        f"  namespace: flux-system\n"
        f"{annotations_block}"
        f"spec:\n"
        f"  interval: 1h\n"
        f"  retryInterval: 1m\n"
        f"  timeout: 5m\n"
        f"  sourceRef:\n"
        f"    kind: GitRepository\n"
        f"    name: {source_ref_name}\n"
        f"  path: ./{_APPS_BASE}/{spec.app_id}\n"
        f"  prune: true\n"
    )


def _render_httproute(app_id: str, cluster_id: str, hosts: List[str], route: HTTPRouteSpec) -> str:
    """Render a Gateway API HTTPRoute manifest for an application deployment."""
    hostnames_block = "\n".join(f'    - "{h}"' for h in hosts)
    return (
        f"---\n"
        f"apiVersion: gateway.networking.k8s.io/v1\n"
        f"kind: HTTPRoute\n"
        f"metadata:\n"
        f"  name: {app_id}-{cluster_id}\n"
        f"  namespace: {app_id}\n"
        f"  labels:\n"
        f"    app: {app_id}\n"
        f"    gitopsapi.io/cluster: {cluster_id}\n"
        f"spec:\n"
        f"  parentRefs:\n"
        f"    - name: {route.gateway_name}\n"
        f"      namespace: {route.gateway_namespace}\n"
        f"      kind: Gateway\n"
        f"  hostnames:\n"
        f"{hostnames_block}\n"
        f"  rules:\n"
        f"    - matches:\n"
        f"        - path:\n"
        f"            type: PathPrefix\n"
        f"            value: {route.path_prefix}\n"
        f"      backendRefs:\n"
        f"        - name: {app_id}\n"
        f"          port: {route.port}\n"
    )


def _httproute_path(app_id: str, cluster_id: str) -> str:
    return f"{_APPS_BASE}/{app_id}/{app_id}-httproute-{cluster_id}.yaml"


def _find_kustomization_block(content: str, app_id: str) -> Optional[str]:
    """Return the raw Kustomization block for app_id, or None if not found."""
    raw_blocks = re.split(r"(?m)^---\s*$", content)
    for block in raw_blocks:
        if (
            re.search(rf"^\s+name:\s+{re.escape(app_id)}\s*$", block, re.MULTILINE)
            and "kind: Kustomization" in block
        ):
            return block
    return None


def _remove_kustomization_block(content: str, app_id: str) -> Tuple[str, bool]:
    """Remove the Kustomization block for app_id from a multi-doc YAML string."""
    raw_blocks = re.split(r"(?m)^---\s*$", content)
    found = False
    result_blocks: List[str] = []
    for block in raw_blocks:
        is_target = (
            re.search(rf"^\s+name:\s+{re.escape(app_id)}\s*$", block, re.MULTILINE)
            and "kind: Kustomization" in block
        )
        if is_target:
            found = True
        else:
            result_blocks.append(block)

    updated = result_blocks[0] + "".join(
        "---\n" + blk.lstrip("\n") for blk in result_blocks[1:]
    )
    return updated, found


def _comment_kustomization_block(content: str, app_id: str) -> Tuple[str, bool]:
    """Comment out the Kustomization block for app_id."""
    raw_blocks = re.split(r"(?m)^---\s*$", content)
    found = False
    result_blocks: List[str] = []
    for block in raw_blocks:
        is_target = (
            re.search(rf"^\s+name:\s+{re.escape(app_id)}\s*$", block, re.MULTILINE)
            and "kind: Kustomization" in block
        )
        if is_target:
            found = True
            commented = [
                f"# {line}" if (line.strip() and not line.lstrip().startswith("#")) else line
                for line in block.splitlines()
            ]
            result_blocks.append("\n".join(commented))
        else:
            result_blocks.append(block)

    updated = result_blocks[0] + "".join(
        "---\n" + blk.lstrip("\n") for blk in result_blocks[1:]
    )
    return updated, found


class AppConfigService:
    def __init__(self):
        # None = use cluster routing; set to AsyncMock in tests to override
        self._git = None
        self._gh = None

    def _infra_git(self, cluster: str):
        """GitService for {cluster}-infra (Kustomization entries)."""
        return self._git or git_for_infra(cluster)

    def _infra_gh(self, cluster: str):
        """GitHubService for {cluster}-infra PRs."""
        return self._gh or github_for_infra(cluster)

    def _apps_git(self, cluster: str):
        """GitService for {cluster}-apps (values overrides)."""
        return self._git or git_for_apps(cluster)

    def _apps_gh(self, cluster: str):
        """GitHubService for {cluster}-apps PRs."""
        return self._gh or github_for_apps(cluster)

    async def list_by_cluster(self, cluster_id: str) -> List[ApplicationDeploymentResponse]:
        """List all app-cluster configs for a given cluster by parsing its apps.yaml.

        Reads from {cluster_id}-infra repo.
        """
        git = self._infra_git(cluster_id)
        apps_path = _cluster_apps_path(cluster_id)
        try:
            content = await git.read_file(apps_path)
        except FileNotFoundError:
            return []

        results = []
        for doc in yaml.safe_load_all(content):
            if not doc or doc.get("kind") != "Kustomization":
                continue
            meta = doc.get("metadata", {})
            spec = doc.get("spec", {})
            app_id = meta.get("name", "")
            if not app_id:
                continue
            source_ref = spec.get("sourceRef", {}).get("name", "")
            external_ref = source_ref if source_ref != f"{cluster_id}-apps" else None
            hosts_csv = meta.get("annotations", {}).get("gitopsapi.io/external-hosts", "")
            external_hosts = [h.strip() for h in hosts_csv.split(",") if h.strip()]
            results.append(ApplicationDeploymentResponse(
                id=_config_id(app_id, cluster_id),
                app_id=app_id,
                cluster_id=cluster_id,
                gitops_source_ref=external_ref,
                external_hosts=external_hosts,
            ))
        return results

    async def list_by_application(self, app_id: str) -> List[ApplicationDeploymentResponse]:
        """List all clusters this application is assigned to.

        GAP: In the multi-repo model there is no single repo with a 'clusters/' directory.
        Each cluster has its own {cluster}-infra repo. Without a cluster registry, we cannot
        enumerate repos to scan. This method works only when self._git is injected (test mode).
        """
        if self._git is None:
            # GAP: no cluster registry — cannot list across multiple infra repos
            return []

        try:
            cluster_dirs = await self._git.list_dir("clusters")
        except FileNotFoundError:
            return []

        results = []
        for cluster_id in cluster_dirs:
            apps_path = _cluster_apps_path(cluster_id)
            try:
                content = await self._git.read_file(apps_path)
            except FileNotFoundError:
                continue
            block = _find_kustomization_block(content, app_id)
            if block:
                docs = list(yaml.safe_load_all(f"---{block}"))
                doc = next((d for d in docs if d and d.get("kind") == "Kustomization"), None)
                source_ref = (doc or {}).get("spec", {}).get("sourceRef", {}).get("name", "")
                external_ref = source_ref if source_ref != f"{cluster_id}-apps" else None
                results.append(ApplicationDeploymentResponse(
                    id=_config_id(app_id, cluster_id),
                    app_id=app_id,
                    cluster_id=cluster_id,
                    gitops_source_ref=external_ref,
                ))
        return results

    async def create(self, spec: ApplicationDeployment) -> ApplicationDeploymentResponse:
        from fastapi import HTTPException

        infra_git = self._infra_git(spec.cluster_id)
        infra_gh = self._infra_gh(spec.cluster_id)

        apps_path = _cluster_apps_path(spec.cluster_id)
        try:
            existing = await infra_git.read_file(apps_path)
        except FileNotFoundError:
            existing = ""

        if existing and _find_kustomization_block(existing, spec.app_id):
            raise HTTPException(
                status_code=409,
                detail=f"{spec.app_id!r} is already assigned to {spec.cluster_id!r}. Use PATCH to update.",
            )

        branch = f"app-config/assign-{spec.app_id}-{spec.cluster_id}-{uuid.uuid4().hex[:8]}"
        await infra_git.create_branch(branch)

        new_entry = _render_kustomization_entry(spec)
        updated = existing.rstrip("\n") + "\n" + new_entry if existing.strip() else new_entry
        await infra_git.write_file(apps_path, updated)

        await infra_git.commit(f"chore: assign {spec.app_id} to {spec.cluster_id}")
        await infra_git.push()

        pr_body = (
            f"Adds `{spec.app_id}` Kustomization to `{apps_path}`.\n\n"
            + (f"Chart version override: `{spec.chart_version_override}`\n" if spec.chart_version_override else "")
            + (f"External GitRepository source: `{spec.gitops_source_ref}`\n" if spec.gitops_source_ref else "")
        )
        pr_url = await infra_gh.create_pr(
            branch=branch,
            title=f"Assign {spec.app_id} to {spec.cluster_id}",
            body=pr_body,
            labels=["application-config", f"cluster:{spec.cluster_id}"],
            reviewers=[],
        )

        # Values override and HTTPRoute go to {cluster}-apps repo as a separate PR
        values_pr_url = None
        needs_apps_pr = spec.values_override or (spec.external_hosts and spec.http_route)
        if needs_apps_pr:
            apps_git = self._apps_git(spec.cluster_id)
            apps_gh = self._apps_gh(spec.cluster_id)
            values_branch = f"app-config/values-{spec.app_id}-{spec.cluster_id}-{uuid.uuid4().hex[:8]}"
            await apps_git.create_branch(values_branch)

            if spec.values_override:
                override_path = _values_override_path(spec.app_id, spec.cluster_id)
                await apps_git.write_file(override_path, spec.values_override)

            if spec.external_hosts and spec.http_route:
                httproute_yaml = _render_httproute(
                    spec.app_id, spec.cluster_id, spec.external_hosts, spec.http_route
                )
                await apps_git.write_file(
                    _httproute_path(spec.app_id, spec.cluster_id), httproute_yaml
                )

            pr_body_parts = [
                f"Adds per-cluster manifests for `{spec.app_id}` on `{spec.cluster_id}`.\n"
            ]
            if spec.values_override:
                pr_body_parts.append("- Values override")
            if spec.external_hosts and spec.http_route:
                pr_body_parts.append(
                    f"- HTTPRoute: {', '.join(spec.external_hosts)} → {spec.app_id}:{spec.http_route.port}"
                )
            if spec.secret_refs:
                names = ", ".join(r.name for r in spec.secret_refs)
                pr_body_parts.append(f"- Required secrets: {names}")
            if spec.config_map_refs:
                names = ", ".join(r.name for r in spec.config_map_refs)
                pr_body_parts.append(f"- Required configmaps: {names}")

            await apps_git.commit(f"chore: app manifests {spec.app_id} on {spec.cluster_id}")
            await apps_git.push()
            values_pr_url = await apps_gh.create_pr(
                branch=values_branch,
                title=f"App manifests: {spec.app_id} on {spec.cluster_id}",
                body="\n".join(pr_body_parts),
                labels=["application-config", f"cluster:{spec.cluster_id}"],
                reviewers=[],
            )

        return ApplicationDeploymentResponse(
            id=_config_id(spec.app_id, spec.cluster_id),
            app_id=spec.app_id,
            cluster_id=spec.cluster_id,
            chart_version_override=spec.chart_version_override,
            values_override=spec.values_override,
            enabled=spec.enabled,
            pipeline_stage=spec.pipeline_stage,
            gitops_source_ref=spec.gitops_source_ref,
            external_hosts=spec.external_hosts,
            http_route=spec.http_route,
            secret_refs=spec.secret_refs,
            config_map_refs=spec.config_map_refs,
            pr_url=values_pr_url or pr_url,
        )

    async def patch(
        self, config_id: str, patch: PatchApplicationDeployment
    ) -> ApplicationDeploymentResponse:
        parts = config_id.split("-", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid config id: {config_id!r}")
        app_id, cluster_id = parts[0], parts[1]

        pr_url = None

        # Values override patch → {cluster}-apps repo
        if patch.values_override is not None:
            apps_git = self._apps_git(cluster_id)
            apps_gh = self._apps_gh(cluster_id)
            branch = f"app-config/patch-values-{config_id}-{uuid.uuid4().hex[:8]}"
            await apps_git.create_branch(branch)
            override_path = _values_override_path(app_id, cluster_id)
            await apps_git.write_file(override_path, patch.values_override)
            await apps_git.commit(f"chore: patch values {config_id}")
            await apps_git.push()
            pr_url = await apps_gh.create_pr(
                branch=branch,
                title=f"Update values override: {config_id}",
                body=f"Updates per-cluster values for `{app_id}` on `{cluster_id}`.",
                labels=["application-config", f"cluster:{cluster_id}"],
                reviewers=[],
            )

        # enabled=False → comment Kustomization block in {cluster}-infra repo
        if patch.enabled is False:
            infra_git = self._infra_git(cluster_id)
            infra_gh = self._infra_gh(cluster_id)
            branch = f"app-config/disable-{config_id}-{uuid.uuid4().hex[:8]}"
            await infra_git.create_branch(branch)
            apps_path = _cluster_apps_path(cluster_id)
            content = await infra_git.read_file(apps_path)
            updated, _ = _comment_kustomization_block(content, app_id)
            await infra_git.write_file(apps_path, updated)
            await infra_git.commit(f"chore: disable {config_id}")
            await infra_git.push()
            pr_url = await infra_gh.create_pr(
                branch=branch,
                title=f"Disable app-config: {config_id}",
                body=f"Comments out `{app_id}` Kustomization on `{cluster_id}`.",
                labels=["application-config", f"cluster:{cluster_id}"],
                reviewers=[],
            )

        return ApplicationDeploymentResponse(
            id=config_id,
            app_id=app_id,
            cluster_id=cluster_id,
            chart_version_override=patch.chart_version_override,
            values_override=patch.values_override or "",
            enabled=patch.enabled if patch.enabled is not None else True,
            http_route=patch.http_route,
            secret_refs=patch.secret_refs or [],
            config_map_refs=patch.config_map_refs or [],
            pr_url=pr_url,
        )

    async def delete(self, config_id: str) -> ApplicationDeploymentResponse:
        parts = config_id.split("-", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid config id: {config_id!r}")
        app_id, cluster_id = parts[0], parts[1]

        infra_git = self._infra_git(cluster_id)
        infra_gh = self._infra_gh(cluster_id)
        apps_path = _cluster_apps_path(cluster_id)
        branch = f"app-config/remove-{config_id}-{uuid.uuid4().hex[:8]}"
        await infra_git.create_branch(branch)

        content = await infra_git.read_file(apps_path)
        updated, found = _remove_kustomization_block(content, app_id)
        if not found:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"Kustomization for {app_id!r} not found in {apps_path}",
            )

        await infra_git.write_file(apps_path, updated)
        await infra_git.commit(f"chore: remove {app_id} from {cluster_id}")
        await infra_git.push()

        pr_url = await infra_gh.create_pr(
            branch=branch,
            title=f"Remove {app_id} from {cluster_id}",
            body=(
                f"Removes `{app_id}` Kustomization from `{apps_path}`.\n\n"
                f"App definition in `gitops/gitops-apps/{app_id}/` is preserved."
            ),
            labels=["application-config", f"cluster:{cluster_id}"],
            reviewers=[],
        )

        return ApplicationDeploymentResponse(
            id=config_id,
            app_id=app_id,
            cluster_id=cluster_id,
            pr_url=pr_url,
        )
