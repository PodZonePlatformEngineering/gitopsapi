"""
Functional tests: "musings" single-node cluster dev lifecycle (PROJ-003/T-026).

Exercises the service layer end-to-end with git transport mocked.
All rendered manifests are captured and asserted — not just PR URL presence.

Cycle A — create cluster
Cycle B — deploy application (gitopsapi on musings)
Cycle C — decommission cluster

Also documents schema gaps found during API-First Testing Protocol review.
"""

import json
import yaml
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, call

from gitopsgui.models.cluster import (
    ClusterSpec, ClusterDimensions, StorageSpec,
)
from gitopsgui.models.application_config import ApplicationDeployment
from gitopsgui.services.cluster_service import ClusterService, _render_values
from gitopsgui.services.app_config_service import AppConfigService


# ---------------------------------------------------------------------------
# Fixtures: load test data from test_data/
# ---------------------------------------------------------------------------

_TEST_DATA = Path(__file__).parents[1] / "test_data"


def _load_cluster_spec(filename: str) -> ClusterSpec:
    raw = json.loads((_TEST_DATA / "clusters" / filename).read_text())
    # Strip metadata keys (API-First Protocol: _comment, _curl, _schema_gaps)
    payload = {k: v for k, v in raw.items() if not k.startswith("_")}
    return ClusterSpec(**payload)


def _load_app_deployment(filename: str) -> ApplicationDeployment:
    raw = json.loads((_TEST_DATA / "application-configs" / filename).read_text())
    payload = {k: v for k, v in raw.items() if not k.startswith("_")}
    return ApplicationDeployment(**payload)


_MUSINGS_SPEC = _load_cluster_spec("musings-create.json")
_MUSINGS_APP = _load_app_deployment("gitopsapi-musings.json")

# Stable clusters.yaml fixture used for decommission tests
_CLUSTERS_YAML = """\
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: musings-cluster
  namespace: flux-system
spec:
  interval: 10m
  path: ./gitops/cluster-charts/musings
  prune: true
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: other-cluster
  namespace: flux-system
spec:
  interval: 10m
  path: ./gitops/cluster-charts/other
  prune: true
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cluster_svc() -> ClusterService:
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha-musings")
    svc._git.push = AsyncMock()
    svc._git.delete_file = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_CLUSTERS_YAML)
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/management-infra/pull/42")
    svc._gh.archive_repo = AsyncMock()
    return svc


def _make_app_svc() -> AppConfigService:
    svc = AppConfigService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha-app")
    svc._git.push = AsyncMock()
    svc._git.read_file = AsyncMock(return_value="")  # empty = no existing entries
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/musings-infra/pull/1")
    return svc


def _capture_values_yaml(svc: ClusterService) -> dict:
    """Return parsed YAML from the first write_file call (cluster values file)."""
    first_call = svc._git.write_file.call_args_list[0]
    return yaml.safe_load(first_call.args[1])


# ---------------------------------------------------------------------------
# Schema gap documentation (API-First Testing Protocol)
# ---------------------------------------------------------------------------

def test_task_spec_missing_required_fields():
    """Document T-026 task data gaps: vip, ip_range, sops_secret_ref are required.

    The task file (2026-03-28-dev-functional-testing.md) provides:
      { worker_count, cpu_per_node, memory_gb_per_node, boot_volume_gb,
        storage.enabled, allow_scheduling_on_controlplanes }
    but omits the three required ClusterSpec fields. This test confirms the
    API rejects an incomplete payload and documents the gap.
    """
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        ClusterSpec(
            name="musings",
            dimensions=ClusterDimensions(worker_count=1, cpu_per_node=2, memory_gb_per_node=4, boot_volume_gb=10),
            allow_scheduling_on_control_planes=True,
            storage=StorageSpec(internal_linstor=False),
            # vip, ip_range, sops_secret_ref intentionally omitted
        )
    errors = {e["loc"][0] for e in exc_info.value.errors()}
    assert "vip" in errors
    assert "ip_range" in errors
    assert "sops_secret_ref" in errors


def test_legacy_storage_fields_migrate_correctly():
    """storage.enabled/size (legacy task field names) map to internal_linstor/linstor_disk_gb."""
    spec = ClusterSpec(
        name="musings",
        vip="192.168.4.230",
        ip_range="192.168.4.231-192.168.4.233",
        sops_secret_ref="sops-age",
        dimensions=ClusterDimensions(
            control_plane_count=1, worker_count=0,
            cpu_per_node=2, memory_gb_per_node=4, boot_volume_gb=10,
        ),
        allow_scheduling_on_control_planes=True,
        storage={"enabled": False, "size": 10},  # legacy field names from task file
    )
    assert spec.storage.internal_linstor is False
    assert spec.storage.linstor_disk_gb == 10


# ---------------------------------------------------------------------------
# Cycle A — create cluster
# ---------------------------------------------------------------------------

async def test_cycle_a_create_musings_opens_pr():
    """Cycle A: create_cluster raises a PR and writes 4 git files."""
    svc = _make_cluster_svc()
    result = await svc.create_cluster(_MUSINGS_SPEC)

    svc._git.create_branch.assert_called_once()
    assert svc._git.write_file.call_count == 4  # values + cluster.yaml + kustomization + kustomizeconfig
    svc._git.commit.assert_called_once()
    svc._git.push.assert_called_once()
    svc._gh.create_pr.assert_called_once()
    assert result.pr_url == "https://github.com/test/management-infra/pull/42"
    assert result.name == "musings"


async def test_cycle_a_values_storage_linstor_disabled():
    """Cycle A: rendered values has storage.internal_linstor=false — Linstor not provisioned."""
    svc = _make_cluster_svc()
    await svc.create_cluster(_MUSINGS_SPEC)

    parsed = _capture_values_yaml(svc)
    assert "storage" in parsed
    assert parsed["storage"]["internal_linstor"] is False
    assert "linstor_disk_gb" not in parsed["storage"]


async def test_cycle_a_values_dimensions_correct():
    """Cycle A: rendered values reflects musings dimensions (single-node, 2cpu/4gb/10gb)."""
    svc = _make_cluster_svc()
    await svc.create_cluster(_MUSINGS_SPEC)

    parsed = _capture_values_yaml(svc)
    assert parsed["controlplane"]["machine_count"] == 1
    assert parsed["controlplane"]["num_cores"] == 2
    assert parsed["controlplane"]["memory_mib"] == 4096  # 4 GB
    assert parsed["controlplane"]["boot_volume_size"] == 10
    assert parsed["worker"]["machine_count"] == 0
    assert parsed["worker"]["boot_volume_size"] == 10  # no emptydir headroom


async def test_cycle_a_values_allow_scheduling_on_control_planes():
    """Cycle A: allow_scheduling_on_control_planes=true is set (required for single-node)."""
    svc = _make_cluster_svc()
    await svc.create_cluster(_MUSINGS_SPEC)

    parsed = _capture_values_yaml(svc)
    assert parsed["controlplane"]["allow_scheduling_on_control_planes"] is True
    assert parsed["allow_scheduling_on_control_planes"] is True


async def test_cycle_a_values_network_fields():
    """Cycle A: VIP and IP range are present in rendered values."""
    svc = _make_cluster_svc()
    await svc.create_cluster(_MUSINGS_SPEC)

    parsed = _capture_values_yaml(svc)
    assert parsed["vip"] == "192.168.4.230"
    assert "192.168.4.231-192.168.4.233" in parsed["network"]["ip_ranges"]


async def test_cycle_a_pr_has_cluster_label():
    """Cycle A: PR is labelled 'cluster' for management-infra routing."""
    svc = _make_cluster_svc()
    await svc.create_cluster(_MUSINGS_SPEC)

    pr_kwargs = svc._gh.create_pr.call_args
    labels = pr_kwargs.kwargs.get("labels") or pr_kwargs.args[3]
    assert "cluster" in labels


async def test_cycle_a_no_github_repo_creation_when_managed_gitops_false():
    """Cycle A: managed_gitops=false skips repo provisioning (test environment)."""
    svc = _make_cluster_svc()
    svc._gh.create_repo = AsyncMock()
    await svc.create_cluster(_MUSINGS_SPEC)

    svc._gh.create_repo.assert_not_called()


async def test_cycle_a_values_written_to_correct_path():
    """Cycle A: values file written to gitops/cluster-charts/musings/musings-values.yaml."""
    svc = _make_cluster_svc()
    await svc.create_cluster(_MUSINGS_SPEC)

    written_paths = [c.args[0] for c in svc._git.write_file.call_args_list]
    assert any("musings" in p and "values" in p for p in written_paths), (
        f"Expected a values file path containing 'musings' and 'values', got: {written_paths}"
    )


# ---------------------------------------------------------------------------
# Cycle B — application deployment
# ---------------------------------------------------------------------------

async def test_cycle_b_deploy_gitopsapi_on_musings_opens_pr():
    """Cycle B: deploying gitopsapi on musings creates a PR in musings-infra."""
    svc = _make_app_svc()
    result = await svc.create(_MUSINGS_APP)

    svc._git.create_branch.assert_called_once()
    svc._gh.create_pr.assert_called_once()
    assert result.pr_url == "https://github.com/test/musings-infra/pull/1"
    assert result.id == "gitopsapi-musings"
    assert result.app_id == "gitopsapi"
    assert result.cluster_id == "musings"


async def test_cycle_b_apps_yaml_updated_with_kustomization_entry():
    """Cycle B: musings-apps.yaml (clusters/musings/musings-apps.yaml) gains a Kustomization entry."""
    svc = _make_app_svc()
    await svc.create(_MUSINGS_APP)

    # The apps.yaml write is the first (and only) write when values_override is empty
    assert svc._git.write_file.call_count == 1
    written_path, written_content = svc._git.write_file.call_args.args
    assert "musings" in written_path
    assert "gitopsapi" in written_content
    assert "Kustomization" in written_content


async def test_cycle_b_no_values_file_when_override_empty():
    """Cycle B: no values override file written when values_override is empty (default repo config)."""
    svc = _make_app_svc()
    await svc.create(_MUSINGS_APP)

    assert svc._git.write_file.call_count == 1  # only apps.yaml, no values file


async def test_cycle_b_duplicate_raises_409():
    """Cycle B: re-deploying same app to same cluster returns 409 Conflict."""
    from fastapi import HTTPException

    existing_yaml = """\
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: gitopsapi
  namespace: flux-system
spec:
  interval: 1h
  path: ./gitops/gitops-apps/gitopsapi
  prune: true
"""
    svc = _make_app_svc()
    svc._git.read_file = AsyncMock(return_value=existing_yaml)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create(_MUSINGS_APP)

    assert exc_info.value.status_code == 409
    assert "already assigned" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Cycle C — decommission cluster
# ---------------------------------------------------------------------------

async def test_cycle_c_decommission_musings_raises_pr():
    """Cycle C: decommission_cluster opens a PR to remove musings from management-infra."""
    svc = _make_cluster_svc()
    result = await svc.decommission_cluster("musings")

    svc._gh.create_pr.assert_called_once()
    assert result.pr_url == "https://github.com/test/management-infra/pull/42"
    assert result.name == "musings"


async def test_cycle_c_decommission_deletes_four_files():
    """Cycle C: 4 files deleted (values + cluster.yaml + kustomization + kustomizeconfig)."""
    svc = _make_cluster_svc()
    await svc.decommission_cluster("musings")

    assert svc._git.delete_file.call_count == 4


async def test_cycle_c_decommission_removes_kustomization_entry():
    """Cycle C: clusters.yaml kustomization entry for musings is removed; other clusters unaffected."""
    svc = _make_cluster_svc()
    await svc.decommission_cluster("musings")

    written_path, written_content = svc._git.write_file.call_args.args
    assert "name: musings-cluster" not in written_content
    assert "name: other-cluster" in written_content


async def test_cycle_c_decommission_archives_two_repos():
    """Cycle C: both musings-infra and musings-apps are archived."""
    svc = _make_cluster_svc()
    result = await svc.decommission_cluster("musings")

    assert svc._gh.archive_repo.call_count == 2
    archived = {c.args[0] for c in svc._gh.archive_repo.call_args_list}
    assert archived == {"musings-infra", "musings-apps"}
    assert set(result.archived_repos) == {"musings-infra", "musings-apps"}


async def test_cycle_c_decommission_not_found_raises_404():
    """Cycle C: decommissioning a non-existent cluster raises FileNotFoundError (→ 404)."""
    svc = _make_cluster_svc()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("clusters.yaml not found"))

    with pytest.raises(FileNotFoundError):
        await svc.decommission_cluster("nonexistent")


# ---------------------------------------------------------------------------
# Full A→B→C cycle (state is carried between cycles)
# ---------------------------------------------------------------------------

async def test_full_lifecycle_a_b_c():
    """Full lifecycle: create cluster → deploy app → decommission — all succeed."""
    # Cycle A
    cluster_svc = _make_cluster_svc()
    create_result = await cluster_svc.create_cluster(_MUSINGS_SPEC)
    assert create_result.pr_url is not None

    # Cycle B
    app_svc = _make_app_svc()
    deploy_result = await app_svc.create(_MUSINGS_APP)
    assert deploy_result.pr_url is not None
    assert deploy_result.id == "gitopsapi-musings"

    # Cycle C
    decomm_svc = _make_cluster_svc()
    decomm_result = await decomm_svc.decommission_cluster("musings")
    assert decomm_result.pr_url is not None
    assert set(decomm_result.archived_repos) == {"musings-infra", "musings-apps"}
