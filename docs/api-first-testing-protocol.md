# API-First Testing Protocol

## Purpose and Motivation

When provisioning clusters or deploying applications via the GitOpsAPI, schema mismatches and
missing fields are detected late — typically as roundtrip failures in Flux reconciliation or CAPI
provisioning. By the time those failures surface, the Git PR has already been merged and the
cluster or application object may be in a partially provisioned state.

The API-first testing protocol moves schema review earlier in the process. Before any POST or PUT
call is made against a write endpoint, the caller documents and reviews every attribute of the
target object: field names, types, default values, and constraints. This practice caught several
schema gaps during the platform-services provisioning session (2026-03-18), including missing
`vip`, `platform`, and `external_hosts` fields, before any Flux reconciliation was triggered.

---

## Pre-Call Attribute Review Checklist

Complete all four steps before making any API call to a write endpoint:

1. **List all fields** — enumerate every field in the target object (and any nested objects).
   Reference the Pydantic model in `src/gitopsgui/models/` as the authoritative source.

2. **Review types and constraints** — for each field, confirm:
   - Data type (`str`, `int`, `bool`, `List[str]`, nested model, `Optional[...]`)
   - Default value (if any)
   - Whether the field is required or optional
   - Any format constraints (e.g. IP address format, URL, port range)
   - Business rules documented in `tests/test_data/README.md` (BR-001 through BR-006)

3. **Verify the test data file** — open (or create) the corresponding file in
   `tests/test_data/{endpoint}/` and confirm:
   - All required fields are present
   - Field values conform to types and constraints from step 2
   - The `_comment` and `_curl` metadata keys are present and accurate (see Convention below)
   - No fields are included that the schema does not define

4. **Make the API call** — only after steps 1–3 are complete and any gaps are resolved.
   Any schema gap found in step 2 or 3 must either be fixed in the model before proceeding,
   or logged as a business rule in `tests/test_data/README.md` with a decision pending.

---

## Test Data File Convention

All test data files are JSON stored under `tests/test_data/`:

```
tests/test_data/
  clusters/               POST /api/v1/clusters, PUT /api/v1/clusters/{name}
  applications/           POST /api/v1/applications, PUT /api/v1/applications/{name}
  application-configs/    POST /api/v1/application-configs, PATCH /api/v1/application-configs/{id}
  pipelines/              POST /api/v1/pipelines and sub-resources
```

### Metadata fields (`_comment` and `_curl`)

Every test data file includes two metadata keys at the top level:

| Key | Purpose |
| --- | --- |
| `_comment` | Human-readable description: method, endpoint, and object being created |
| `_curl` | Complete `curl` command to replay the call against the live API |

These keys are **not part of the API schema**. They are stripped by the JSON parser before the
payload reaches the Pydantic model. Never include them when constructing payloads in application
code. They exist only to make test data files self-documenting and replayable.

Example header:

```json
{
  "_comment": "POST /api/v1/clusters — provision platform-services cluster",
  "_curl": "curl -s -X POST -H 'Host: gitopsgui.podzone.cloud' -H 'Content-Type: application/json' -H 'X-Forwarded-User: martin' -H 'X-Auth-Request-Groups: cluster-operators' -d @clusters/platform-services-create.json http://freyr:8081/api/v1/clusters",
  ...
}
```

---

## Reference Objects

### ClusterSpec — platform-services

File: `tests/test_data/clusters/platform-services-create.json`

| Field | Type | Default | Required | Constraint / Notes |
| --- | --- | --- | --- | --- |
| `name` | `str` | — | yes | Unique cluster identifier; used as repo prefix (`{name}-infra`, `{name}-apps`) |
| `platform` | `PlatformSpec \| null` | `null` | no | `null` for externally-managed clusters (`managed_gitops=False`) |
| `platform.name` | `str` | — | if platform set | Human identifier for the hypervisor node, e.g. `"venus"` |
| `platform.type` | `str` | `"proxmox"` | no | Provisioning platform type; currently only `"proxmox"` supported |
| `platform.endpoint` | `str` | — | if platform set | Proxmox management API URL, e.g. `"https://192.168.4.50:8006"` |
| `platform.nodes` | `List[str]` | — | if platform set | Proxmox node names available for provisioning, e.g. `["venus"]` |
| `vip` | `str` | — | yes | Virtual IP for the Kubernetes API server load balancer (kube-vip); must be outside `ip_range` |
| `ip_range` | `str` | — | yes | Node IP allocation range in hyphenated format: `"192.168.4.181-192.168.4.187"` (BR-001) |
| `dimensions` | `ClusterDimensions` | see below | no | Node sizing; all sub-fields have defaults |
| `dimensions.control_plane_count` | `int` | `3` | no | Number of control plane nodes |
| `dimensions.worker_count` | `int` | `3` | no | Number of worker nodes; set to `0` only if `allow_scheduling_on_control_planes=True` |
| `dimensions.cpu_per_node` | `int` | `4` | no | vCPUs per node |
| `dimensions.memory_gb_per_node` | `int` | `16` | no | RAM in GiB per node |
| `dimensions.boot_volume_gb` | `int` | `50` | no | Boot disk size in GiB per node |
| `managed_gitops` | `bool` | `True` | no | If `True`, platform creates and manages `{cluster}-infra` and `{cluster}-apps` repos (TR-039) |
| `gitops_repo_url` | `str \| null` | `null` | if `managed_gitops=False` | Required for externally-managed clusters; derived automatically when `managed_gitops=True` |
| `sops_secret_ref` | `str` | — | yes | Name of the Kubernetes Secret holding the SOPS/age decryption key |
| `extra_manifests` | `List[str]` | `[]` | no | URLs applied as Talos `extraManifests` at boot time (CNI, Flux, Gateway API, etc.) |
| `bastion` | `BastionSpec \| null` | `null` | no | If set, kubeconfig server URL is rewritten to the bastion host |
| `bastion.hostname` | `str` | — | if bastion set | Bastion DNS name, e.g. `"freyr"` |
| `bastion.ip` | `str` | — | if bastion set | Bastion IP address |
| `bastion.api_port` | `int` | `6443` | no | Port on bastion that forwards to the cluster API server; formula: `6430 + block_number` |
| `allow_scheduling_on_control_planes` | `bool` | `False` | no | Enables Talos `allowSchedulingOnControlPlanes`; required when `worker_count=0` |
| `external_hosts` | `List[str]` | `[]` | no | FQDNs served externally via this cluster's Gateway; drives cert-manager certs and Gateway listeners at provisioning time |

Expected values for platform-services:

```json
{
  "name": "platform-services",
  "platform": {
    "name": "venus",
    "type": "proxmox",
    "endpoint": "https://192.168.4.50:8006",
    "nodes": ["venus"]
  },
  "vip": "192.168.4.180",
  "ip_range": "192.168.4.181-192.168.4.187",
  "dimensions": {
    "control_plane_count": 1,
    "worker_count": 2,
    "cpu_per_node": 4,
    "memory_gb_per_node": 16,
    "boot_volume_gb": 50
  },
  "managed_gitops": true,
  "sops_secret_ref": "gitopsapi-age-key",
  "extra_manifests": [
    "http://192.168.4.1/cilium.yaml",
    "http://192.168.4.1/flux.yaml",
    "http://192.168.4.1/flux-instance-platform-services.yaml",
    "http://192.168.4.1/flux-secret.yaml",
    "http://192.168.4.1/gateway-api.yaml"
  ],
  "bastion": {
    "hostname": "freyr",
    "ip": "192.168.1.80",
    "api_port": 6448
  },
  "allow_scheduling_on_control_planes": false,
  "external_hosts": [
    "login.podzone.cloud",
    "artefacts.podzone.cloud",
    "git.podzone.cloud"
  ]
}
```

---

### ApplicationSpec — cloudnative-pg

File: `tests/test_data/applications/cloudnative-pg.json`

| Field | Type | Default | Required | Constraint / Notes |
| --- | --- | --- | --- | --- |
| `name` | `str` | — | yes | Unique application identifier; used as HelmRelease name and as `app_id` in application-configs |
| `cluster` | `str` | — | yes | Target cluster name; written as `targetNamespace` in the HelmRelease (BR-005: this is the namespace, not a cluster reference) |
| `helm_repo_url` | `str` | — | yes | Helm chart repository URL |
| `chart_name` | `str` | — | yes | Helm chart name within the repository |
| `chart_version` | `str` | — | yes | Pinned chart version string (semver, e.g. `"0.27.1"`) |
| `values_yaml` | `str` | `""` | no | Inline Helm values as a YAML string; empty string means use chart defaults |
| `app_repo_url` | `str \| null` | `null` | no | Source repository URL for hosted applications |

Expected values for cloudnative-pg:

```json
{
  "name": "cloudnative-pg",
  "cluster": "platform-services",
  "helm_repo_url": "https://cloudnative-pg.io/charts/",
  "chart_name": "cloudnative-pg",
  "chart_version": "0.27.1",
  "values_yaml": ""
}
```

---

### ApplicationClusterConfig — cloudnative-pg on platform-services

File: `tests/test_data/application-configs/cloudnative-pg-platform-services.json`

| Field | Type | Default | Required | Constraint / Notes |
| --- | --- | --- | --- | --- |
| `app_id` | `str` | — | yes | Must match the `name` of an existing `ApplicationSpec` |
| `cluster_id` | `str` | — | yes | Must match the `name` of an existing `ClusterSpec` |
| `chart_version_override` | `str \| null` | `null` | no | Overrides the chart version from `ApplicationSpec` for this cluster; `null` means use application default |
| `values_override` | `str` | `""` | no | Per-cluster Helm values as a YAML string; merged over `ApplicationSpec.values_yaml` |
| `enabled` | `bool` | `True` | no | If `False`, the Flux Kustomization for this app on this cluster is suspended |
| `pipeline_stage` | `str \| null` | `null` | no | Pipeline stage tag: `"dev"`, `"ete"`, `"production"`, or `null` |
| `gitops_source_ref` | `str \| null` | `null` | no | External GitRepository CR name (FR-046a); `null` means use the cluster-apps repo default source |
| `external_hosts` | `List[str]` | `[]` | no | Subset of `cluster.external_hosts` routed to this application; drives HTTPRoute creation |

Expected values for cloudnative-pg on platform-services:

```json
{
  "app_id": "cloudnative-pg",
  "cluster_id": "platform-services",
  "enabled": true,
  "values_override": "",
  "external_hosts": []
}
```

---

## Coverage Requirements

Every write endpoint must have at least one corresponding test data file with all fields
documented. The table below tracks current coverage:

| Endpoint | Method | Test data file(s) | Status |
| --- | --- | --- | --- |
| `/api/v1/clusters` | POST | `clusters/gitopsdev-create.json`, `clusters/gitopsete-create.json`, `clusters/gitopsprod-create.json`, `clusters/platform-services-create.json` | Covered |
| `/api/v1/clusters/{name}` | PUT | — | Not yet covered |
| `/api/v1/applications` | POST | `applications/cloudnative-pg.json`, `applications/nexus.json`, `applications/forgejo.json`, `applications/keycloak.json`, `applications/cloudflared.json`, `applications/gitopsapi-create.json` | Covered |
| `/api/v1/applications/{name}` | PUT | — | Not yet covered |
| `/api/v1/application-configs` | POST | `application-configs/cloudnative-pg-platform-services.json`, `application-configs/nexus-platform-services.json`, `application-configs/forgejo-platform-services.json`, `application-configs/keycloak-platform-services.json`, `application-configs/cloudflared-platform-services.json` | Covered |
| `/api/v1/application-configs/{id}` | PATCH | — | Not yet covered |

PUT and PATCH test data files should be added before those endpoints are exercised in any
testing session. Apply the pre-call checklist above before creating those files.

---

## Related Files

- Pydantic models: `src/gitopsgui/models/cluster.py`, `application.py`, `application_config.py`
- Business rules: `tests/test_data/README.md`
- API schema reference: `podzoneAgentTeam/specifications/gitopsapi-schema.md`
- Working practice note: `CLAUDE.md` — `## API-First Testing Protocol` section
