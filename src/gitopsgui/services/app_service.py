"""
GITGUI-006 — Application object reader/writer.

Gitops repo layout:
  gitops/gitops-apps/<name>/<name>.yaml           — HelmRepository + HelmRelease
  gitops/gitops-apps/<name>/<name>-values.yaml    — chart values
  gitops/gitops-apps/<name>/kustomization.yaml
  gitops/gitops-apps/<name>/kustomizeconfig.yaml

Writes go via feature branch + PR labelled 'application'.
"""

import textwrap
import uuid
from typing import List, Optional

import yaml

from ..models.application import ApplicationSpec, ApplicationResponse
from .git_service import GitService
from .github_service import GitHubService

_APPS_BASE = "gitops/gitops-apps"


def _app_yaml_path(name: str) -> str:
    return f"{_APPS_BASE}/{name}/{name}.yaml"


def _app_values_path(name: str) -> str:
    return f"{_APPS_BASE}/{name}/{name}-values.yaml"


def _kustomization_path(name: str) -> str:
    return f"{_APPS_BASE}/{name}/kustomization.yaml"


def _kustomizeconfig_path(name: str) -> str:
    return f"{_APPS_BASE}/{name}/kustomizeconfig.yaml"


def _render_app_yaml(spec: ApplicationSpec) -> str:
    return textwrap.dedent(f"""\
        ---
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {spec.name}

        ---
        apiVersion: source.toolkit.fluxcd.io/v1
        kind: HelmRepository
        metadata:
          name: {spec.name}
          namespace: flux-system
        spec:
          interval: 24h
          url: {spec.helm_repo_url}

        ---
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: {spec.name}
          namespace: flux-system
        spec:
          targetNamespace: {spec.name}
          interval: 30m
          chart:
            spec:
              chart: {spec.chart_name}
              version: "{spec.chart_version}"
              sourceRef:
                kind: HelmRepository
                name: {spec.name}
                namespace: flux-system
              interval: 12h
          valuesFrom:
            - kind: ConfigMap
              name: {spec.name}-values
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


class AppService:
    def __init__(self):
        self._git = GitService()
        self._gh = GitHubService()

    async def list_applications(self) -> List[ApplicationResponse]:
        names = await self._git.list_dir(_APPS_BASE)
        results = []
        for name in names:
            app = await self.get_application(name)
            if app:
                results.append(app)
        return results

    async def get_application(self, name: str) -> Optional[ApplicationResponse]:
        try:
            raw = await self._git.read_file(_app_yaml_path(name))
        except FileNotFoundError:
            return None

        # Extract HelmRelease spec from multi-doc YAML
        docs = list(yaml.safe_load_all(raw))
        helm_release = next(
            (d for d in docs if d and d.get("kind") == "HelmRelease"), None
        )
        helm_repo = next(
            (d for d in docs if d and d.get("kind") == "HelmRepository"), None
        )
        if not helm_release:
            return None

        chart_spec = helm_release.get("spec", {}).get("chart", {}).get("spec", {})
        spec = ApplicationSpec(
            name=name,
            cluster=helm_release.get("spec", {}).get("targetNamespace", ""),
            helm_repo_url=helm_repo.get("spec", {}).get("url", "") if helm_repo else "",
            chart_name=chart_spec.get("chart", ""),
            chart_version=chart_spec.get("version", ""),
        )
        return ApplicationResponse(name=name, spec=spec)

    async def create_application(self, spec: ApplicationSpec) -> ApplicationResponse:
        branch = f"application/add-{spec.name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        await self._git.write_file(_app_yaml_path(spec.name), _render_app_yaml(spec))
        await self._git.write_file(_app_values_path(spec.name), spec.values_yaml or "")
        await self._git.write_file(_kustomization_path(spec.name), _render_kustomization(spec.name))
        await self._git.write_file(_kustomizeconfig_path(spec.name), _KUSTOMIZECONFIG)

        await self._git.commit(f"chore: add application {spec.name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Add application: {spec.name}",
            body=f"Add workload `{spec.name}` (chart: {spec.chart_name} {spec.chart_version}) to cluster `{spec.cluster}`.",
            labels=["application", f"stage:{spec.cluster}"],
            reviewers=[],
        )

        return ApplicationResponse(name=spec.name, spec=spec, pr_url=pr_url)
