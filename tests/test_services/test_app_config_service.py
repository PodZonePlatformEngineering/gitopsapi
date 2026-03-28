"""
Unit tests for AppConfigService — mocks GitService and GitHubService.
"""

import pytest
import textwrap
from unittest.mock import AsyncMock

from gitopsgui.models.application_config import (
    ApplicationDeployment,
    PatchApplicationDeployment,
    HTTPRouteSpec,
    SecretRef,
    ConfigMapRef,
)
from gitopsgui.services.app_config_service import (
    AppConfigService,
    _config_id,
    _cluster_apps_path,
    _values_override_path,
    _render_kustomization_entry,
    _render_httproute,
    _httproute_path,
    _find_kustomization_block,
    _remove_kustomization_block,
    _comment_kustomization_block,
)


_SPEC = ApplicationDeployment(
    app_id="keycloak",
    cluster_id="security",
    chart_version_override=None,
    values_override="replicaCount: 1\n",
    enabled=True,
    pipeline_stage=None,
    gitops_source_ref=None,
)

_APPS_YAML = textwrap.dedent("""\
    ---
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: existing-app
      namespace: flux-system
    spec:
      interval: 1h
      sourceRef:
        kind: GitRepository
        name: security-apps
      path: ./gitops/gitops-apps/existing-app
      prune: true
    ---
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: keycloak
      namespace: flux-system
    spec:
      interval: 1h
      sourceRef:
        kind: GitRepository
        name: security-apps
      path: ./gitops/gitops-apps/keycloak
      prune: true
""")


def _svc() -> AppConfigService:
    svc = AppConfigService()
    svc._git = AsyncMock()
    svc._gh = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------

def test_config_id():
    assert _config_id("keycloak", "security") == "keycloak-security"


def test_cluster_apps_path():
    assert _cluster_apps_path("security") == "clusters/security/security-apps.yaml"


def test_values_override_path():
    assert _values_override_path("keycloak", "security") == "gitops/gitops-apps/keycloak/keycloak-values-security.yaml"


# ---------------------------------------------------------------------------
# render helpers
# ---------------------------------------------------------------------------

def test_render_kustomization_entry_default_source_ref():
    rendered = _render_kustomization_entry(_SPEC)
    assert "name: security-apps" in rendered
    assert "name: keycloak" in rendered
    assert "path: ./gitops/gitops-apps/keycloak" in rendered


def test_render_kustomization_entry_external_source_ref():
    spec = _SPEC.model_copy(update={"gitops_source_ref": "bitnami-charts"})
    rendered = _render_kustomization_entry(spec)
    assert "name: bitnami-charts" in rendered


def test_render_kustomization_entry_external_hosts_annotation():
    spec = _SPEC.model_copy(update={"external_hosts": ["login.podzone.cloud"]})
    rendered = _render_kustomization_entry(spec)
    assert "gitopsapi.io/external-hosts" in rendered
    assert "login.podzone.cloud" in rendered


def test_render_kustomization_entry_multiple_external_hosts():
    spec = _SPEC.model_copy(update={"external_hosts": ["login.podzone.cloud", "sso.podzone.cloud"]})
    rendered = _render_kustomization_entry(spec)
    assert "login.podzone.cloud,sso.podzone.cloud" in rendered


def test_render_kustomization_entry_no_annotation_when_empty():
    rendered = _render_kustomization_entry(_SPEC)
    assert "gitopsapi.io/external-hosts" not in rendered
    assert "annotations" not in rendered


# ---------------------------------------------------------------------------
# block manipulation
# ---------------------------------------------------------------------------

def test_find_kustomization_block_found():
    block = _find_kustomization_block(_APPS_YAML, "keycloak")
    assert block is not None
    assert "keycloak" in block


def test_find_kustomization_block_not_found():
    block = _find_kustomization_block(_APPS_YAML, "missing-app")
    assert block is None


def test_remove_kustomization_block_found():
    updated, found = _remove_kustomization_block(_APPS_YAML, "keycloak")
    assert found
    assert "keycloak" not in updated
    assert "existing-app" in updated


def test_remove_kustomization_block_not_found():
    updated, found = _remove_kustomization_block(_APPS_YAML, "nonexistent")
    assert not found
    assert "existing-app" in updated


def test_comment_kustomization_block():
    updated, found = _comment_kustomization_block(_APPS_YAML, "keycloak")
    assert found
    assert "existing-app" in updated
    # The keycloak block lines should now be commented
    for line in updated.splitlines():
        if "keycloak" in line and "existing" not in line and "---" not in line:
            assert line.lstrip().startswith("#"), f"Expected commented line: {line!r}"


# ---------------------------------------------------------------------------
# list_by_cluster — external_hosts round-trip
# ---------------------------------------------------------------------------

_APPS_YAML_WITH_HOSTS = textwrap.dedent("""\
    ---
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: forgejo
      namespace: flux-system
      annotations:
        gitopsapi.io/external-hosts: "git.podzone.cloud"
    spec:
      interval: 1h
      sourceRef:
        kind: GitRepository
        name: platform-services-apps
      path: ./gitops/gitops-apps/forgejo
      prune: true
""")


@pytest.mark.asyncio
async def test_list_by_cluster_reads_external_hosts():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value=_APPS_YAML_WITH_HOSTS)
    results = await svc.list_by_cluster("platform-services")
    assert len(results) == 1
    assert results[0].app_id == "forgejo"
    assert results[0].external_hosts == ["git.podzone.cloud"]


@pytest.mark.asyncio
async def test_list_by_cluster_empty_hosts_when_no_annotation():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value=_APPS_YAML)
    results = await svc.list_by_cluster("security")
    for r in results:
        assert r.external_hosts == []


# ---------------------------------------------------------------------------
# service: create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_writes_apps_yaml_and_values():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value="")
    svc._git.list_dir = AsyncMock(return_value=[])
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/10")

    result = await svc.create(_SPEC)

    assert result.id == "keycloak-security"
    assert result.pr_url == "https://github.com/test/repo/pull/10"
    # write_file called at least twice: apps.yaml + values override
    assert svc._git.write_file.call_count >= 2


@pytest.mark.asyncio
async def test_create_no_values_override_skips_values_file():
    spec = _SPEC.model_copy(update={"values_override": ""})
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value="")
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/11")

    await svc.create(spec)

    # Only the apps.yaml write; no values file
    assert svc._git.write_file.call_count == 1


# ---------------------------------------------------------------------------
# service: delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_raises_409_when_already_assigned():
    from fastapi import HTTPException

    spec = _SPEC.model_copy(update={"values_override": ""})
    svc = _svc()
    # keycloak is already present in _APPS_YAML
    svc._git.read_file = AsyncMock(return_value=_APPS_YAML)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create(spec)

    assert exc_info.value.status_code == 409
    assert "already assigned" in exc_info.value.detail


# ---------------------------------------------------------------------------
# service: delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_removes_block():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value=_APPS_YAML)
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/12")

    result = await svc.delete("keycloak-security")

    assert result.id == "keycloak-security"
    written = svc._git.write_file.call_args[0][1]
    assert "keycloak" not in written
    assert "existing-app" in written


# ---------------------------------------------------------------------------
# T-021: HTTPRouteSpec, SecretRef, ConfigMapRef
# ---------------------------------------------------------------------------

_ROUTE = HTTPRouteSpec(gateway_name="podzone-gateway", gateway_namespace="kube-system", port=8080)


def test_render_httproute_basic():
    rendered = _render_httproute("keycloak", "security", ["login.podzone.cloud"], _ROUTE)
    assert "kind: HTTPRoute" in rendered
    assert "login.podzone.cloud" in rendered
    assert "gateway_name" not in rendered  # field name must not appear verbatim
    assert "name: podzone-gateway" in rendered
    assert "namespace: kube-system" in rendered
    assert "port: 8080" in rendered
    assert "name: keycloak" in rendered
    assert "name: keycloak-security" in rendered


def test_render_httproute_multiple_hosts():
    rendered = _render_httproute("keycloak", "security",
                                 ["login.podzone.cloud", "sso.podzone.cloud"], _ROUTE)
    assert '"login.podzone.cloud"' in rendered
    assert '"sso.podzone.cloud"' in rendered


def test_render_httproute_path_prefix():
    route = HTTPRouteSpec(gateway_name="gw", gateway_namespace="ns", port=80, path_prefix="/auth")
    rendered = _render_httproute("keycloak", "security", ["login.podzone.cloud"], route)
    assert "value: /auth" in rendered


def test_httproute_path():
    assert _httproute_path("keycloak", "security") == \
        "gitops/gitops-apps/keycloak/keycloak-httproute-security.yaml"


def test_render_kustomization_secret_ref_annotation():
    spec = _SPEC.model_copy(update={"secret_refs": [SecretRef(name="keycloak-db-secret")]})
    rendered = _render_kustomization_entry(spec)
    assert "gitopsapi.io/secret-refs" in rendered
    assert "keycloak-db-secret" in rendered


def test_render_kustomization_secret_ref_with_namespace():
    spec = _SPEC.model_copy(update={
        "secret_refs": [SecretRef(name="keycloak-db-secret", namespace="keycloak")]
    })
    rendered = _render_kustomization_entry(spec)
    assert "keycloak/keycloak-db-secret" in rendered


def test_render_kustomization_configmap_ref_annotation():
    spec = _SPEC.model_copy(update={"config_map_refs": [ConfigMapRef(name="keycloak-config")]})
    rendered = _render_kustomization_entry(spec)
    assert "gitopsapi.io/configmap-refs" in rendered
    assert "keycloak-config" in rendered


def test_render_kustomization_no_secret_annotation_when_empty():
    rendered = _render_kustomization_entry(_SPEC)
    assert "secret-refs" not in rendered
    assert "configmap-refs" not in rendered


@pytest.mark.asyncio
async def test_create_writes_httproute_when_hosts_and_route_set():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value="")
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/10")

    spec = ApplicationDeployment(
        app_id="keycloak",
        cluster_id="security",
        external_hosts=["login.podzone.cloud"],
        http_route=_ROUTE,
    )
    result = await svc.create(spec)

    # write_file called twice: infra apps.yaml + httproute
    write_calls = svc._git.write_file.call_args_list
    paths = [c[0][0] for c in write_calls]
    assert any("httproute" in p for p in paths), f"No httproute write found in {paths}"
    httproute_content = next(c[0][1] for c in write_calls if "httproute" in c[0][0])
    assert "kind: HTTPRoute" in httproute_content
    assert "login.podzone.cloud" in httproute_content
    assert result.http_route is not None
    assert result.external_hosts == ["login.podzone.cloud"]


@pytest.mark.asyncio
async def test_create_no_httproute_when_no_route_spec():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value="")
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/10")

    spec = ApplicationDeployment(
        app_id="keycloak",
        cluster_id="security",
        external_hosts=["login.podzone.cloud"],
        # http_route not set
    )
    await svc.create(spec)

    write_calls = svc._git.write_file.call_args_list
    paths = [c[0][0] for c in write_calls]
    assert not any("httproute" in p for p in paths)


@pytest.mark.asyncio
async def test_create_includes_secret_refs_in_response():
    svc = _svc()
    svc._git.read_file = AsyncMock(return_value="")
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/10")

    spec = ApplicationDeployment(
        app_id="keycloak",
        cluster_id="security",
        secret_refs=[SecretRef(name="keycloak-db-secret")],
        config_map_refs=[ConfigMapRef(name="keycloak-config")],
    )
    result = await svc.create(spec)

    assert len(result.secret_refs) == 1
    assert result.secret_refs[0].name == "keycloak-db-secret"
    assert len(result.config_map_refs) == 1
    assert result.config_map_refs[0].name == "keycloak-config"
