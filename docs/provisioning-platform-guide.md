# Provisioning Platform Guide

This guide covers deploying GitOpsAPI with the full **cluster provisioning platform** — using CAPI to provision new Kubernetes clusters onto bare-metal or cloud infrastructure.

---

## Architecture

```text
GitOpsAPI
  ↓ writes cluster-chart values + Flux Kustomization
Management GitOps repo (PR-gated)
  ↓ Flux syncs
Management cluster (CAPI controllers)
  ↓ CAPI + infrastructure provider
New cluster on target hypervisor/cloud
```

GitOpsAPI writes manifests; it never calls infrastructure APIs directly. CAPI handles all provisioning after the PR is merged.

---

## Prerequisites

### CAPI management cluster

A Kubernetes cluster with CAPI controllers installed:

- `cluster-api` core
- Infrastructure provider (e.g. `cluster-api-provider-proxmox` for Proxmox)
- Bootstrap provider (e.g. `cluster-api-provider-talos`)
- Control plane provider (e.g. `cluster-api-provider-talos`)

The management cluster kubeconfig must be available as a Kubernetes secret for GitOpsAPI (see [deployment-prerequisites.md](deployment-prerequisites.md)).

### GitOps repositories

Two repositories per cluster are created by `POST /api/v1/repositories`:

| Repo | Purpose |
| --- | --- |
| `{cluster}-infra` | Flux Kustomization entries pointing at the apps repo |
| `{cluster}-apps` | HelmRelease + HelmRepository manifests |

The management repo holds cluster-chart values at `gitops/cluster-charts/{name}/{name}-values.yaml`.

### Cluster-chart Helm chart

The `cluster-chart` Helm chart generates CAPI manifests from the values written by GitOpsAPI. It must be published and accessible from the management cluster. Source: [podzone-infrastructure](https://github.com/PodZone/podzone-infrastructure).

---

## Workflow: Provisioning a new cluster

### 1. Register the platform (hypervisor)

Platforms are defined in `ClusterSpec.platform`. Each platform maps to a CAPI infrastructure provider.

Example for Proxmox:

```json
{
  "name": "<hypervisor-name>",
  "type": "proxmox",
  "endpoint": "https://<proxmox-host>:8006",
  "nodes": ["<proxmox-node-name>"],
  "template_vmid": 100,
  "credentials_ref": "capmox-manager-credentials",
  "bridge": "vmbr0"
}
```

| Field | Maps to | Notes |
| --- | --- | --- |
| `nodes` | `proxmox.allowedNodes` | VMs may be placed on any listed node |
| `template_vmid` | `proxmox.template.template_vmid` | VMID of the Talos VM template |
| `template_node` | `proxmox.template.sourcenode` | Defaults to `nodes[0]` |
| `credentials_ref` | `proxmox.credentials` | Kubernetes secret name on management cluster |
| `bridge` | `proxmox.vm.bridge` | Proxmox network bridge for cluster VMs |

### 2. Create the cluster repositories

```bash
POST /api/v1/repositories
{
  "name": "<cluster-name>",
  "github_org": "<your-org>"
}
```

This creates `<cluster-name>-infra` and `<cluster-name>-apps` on your GitHub organisation and registers deploy keys.

### 3. Provision the cluster

```bash
POST /api/v1/clusters
```

Full `ClusterSpec` example:

```json
{
  "name": "<cluster-name>",
  "platform": {
    "name": "<hypervisor-name>",
    "type": "proxmox",
    "endpoint": "https://<proxmox-host>:8006",
    "nodes": ["<proxmox-node>"],
    "template_vmid": 100
  },
  "vip": "<control-plane-vip>",
  "ip_range": "<worker-ip-start>-<worker-ip-end>",
  "dimensions": {
    "control_plane_count": 1,
    "worker_count": 2,
    "cpu_per_node": 4,
    "memory_gb_per_node": 16,
    "boot_volume_gb": 50
  },
  "managed_gitops": true,
  "sops_secret_ref": "gitopsapi-age-key",
  "extra_manifests": [],
  "bastion": {
    "hostname": "<bastion-hostname>",
    "ip": "<bastion-ip>",
    "api_port": 6443
  },
  "external_hosts": ["<hostname-for-this-cluster>"]
}
```

GitOpsAPI raises a PR to the management repo. Review and merge it (or use `POST /api/v1/prs/{id}/merge`). Flux syncs and CAPI begins provisioning.

### 4. Monitor progress

```bash
GET /api/v1/clusters/{name}
```

Returns cluster phase and condition from the CAPI `Cluster` object.

---

## Workflow: Assigning an application

### 1. Register the application

```bash
POST /api/v1/applications
{
  "name": "nexus",
  "chart": "nexus",
  "chart_version": "0.1.0",
  "repo_url": "https://motttt.github.io/gitopsapi-apps"
}
```

### 2. Assign to a cluster

```bash
POST /api/v1/application-configs
{
  "application_name": "nexus",
  "cluster_name": "<cluster-name>",
  "values": {}
}
```

GitOpsAPI writes a HelmRelease to `{cluster}-apps` and raises a PR.

---

## Promotion Pipeline

A promotion pipeline connects dev → ETE → production environments with PR-gated approvals.

```bash
POST /api/v1/pipelines
{
  "name": "my-app-pipeline",
  "application_name": "my-app",
  "environments": ["dev", "ete", "production"]
}
```

Stage labels (`stage:dev`, `stage:ete`, `stage:production`) on PRs drive required-approver rules. Build managers approve and merge via `POST /api/v1/prs/{id}/approve` and `POST /api/v1/prs/{id}/merge`.

---

## Talos VM Template Management

Each hypervisor needs a Talos VM template for CAPI to clone. Template attributes are hypervisor-level concerns tracked in `PlatformSpec`:

| Field | Purpose |
| --- | --- |
| `template_vmid` | VMID of the template on this hypervisor |
| `template_node` | Proxmox node where the template resides |

Template lifecycle (upload, version update) is a manual operation performed on the hypervisor. See [Talos documentation](https://www.talos.dev) for image download and import steps.

---

## Reference

- [Getting started](getting-started.md)
- [Deployment prerequisites](deployment-prerequisites.md)
- [Roles reference](roles-reference.md)
- [API-first testing protocol](api-first-testing-protocol.md)
- [Architecture](architecture/v0.1.0-architecture.md)
