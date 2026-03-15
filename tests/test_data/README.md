# GitOpsAPI Test Data

Sample request payloads for CC-002 API testing against the live openclaw deployment.

## Access

```bash
BASE=http://freyr:8081/api/v1
HOST="Host: gitopsgui.podzone.cloud"
AUTH="GITOPSGUI_DEV_ROLE=cluster_operator"   # set on pod; callers use auth headers
```

All requests require the Host header:

```bash
curl -s -H "Host: gitopsgui.podzone.cloud" $BASE/...
```

## Emerging Business Rules

Issues found during test data creation — gaps between Pydantic models and actual cluster values:

### BR-001: ip_range format ambiguity

- **ClusterSpec.ip_range** is typed `str` with no format validation
- Actual cluster-chart values use hyphenated node range: `192.168.4.121-192.168.4.127`
- Schema doc describes CIDR notation: `192.168.4.120/28`
- VIP (`controlplane.endpoint_ip`) is a separate field in cluster-chart but **not in ClusterSpec**
- **Decision needed**: Does `ip_range` mean the node range, the full /28 block, or both? Where does the VIP go?

### BR-002: VIP not in ClusterSpec

- The CAPI cluster-chart needs `controlplane.endpoint_ip` (VIP) separately from the node range
- ClusterSpec has no `vip` or `endpoint_ip` field
- `_render_values()` in `cluster_service.py` only writes `network.ip_ranges` — VIP is lost
- **Decision needed**: Add `vip: str` to ClusterSpec, or derive from ip_range convention (first IP of range)?

### BR-003: platform field not in cluster-chart values

- `ClusterSpec.platform` exists in the Pydantic model (defaults to `"proxmox"` on read)
- Not written to cluster-chart values by `_render_values()` — lost on roundtrip
- **Decision needed**: Add platform to cluster-chart values schema, or treat as implicit (always proxmox for now)?

### BR-004: gitops_repo_url and sops_secret_ref not in cluster-chart values

- Both fields exist in ClusterSpec but `_render_values()` does not write them
- On GET, both default to empty string (not persisted)
- **Decision needed**: Write to cluster-chart values, or source from global config (env var)?

### BR-005: application.cluster is the target namespace, not a cluster reference

- `ApplicationSpec.cluster` is written as `targetNamespace` in the HelmRelease
- On GET, it reads back from `targetNamespace` — so it is the namespace name, not a cluster name
- For multi-cluster apps this is insufficient; needs cluster + namespace separately
- **Decision needed**: Rename to `namespace`? Or keep as cluster identifier and derive namespace from it?

### BR-006: No auth headers in curl test — dev role bypass needed

- Live pod uses OAuth2 proxy headers; local curl tests need `X-Forwarded-User` and `X-Auth-Request-Groups`
- **Workaround for testing**: ensure `GITOPSGUI_DEV_ROLE` is set on pod, or inject headers directly

---

## Files

| File | Endpoint | Method | Role |
| --- | --- | --- | --- |
| `clusters/gitopsdev-create.json` | `/api/v1/clusters` | POST | cluster-operators |
| `clusters/gitopsete-create.json` | `/api/v1/clusters` | POST | cluster-operators |
| `clusters/gitopsprod-create.json` | `/api/v1/clusters` | POST | cluster-operators |
| `applications/gitopsapi-create.json` | `/api/v1/applications` | POST | build-managers |
| `applications/gitopsapi-disable.json` | `/api/v1/applications/{name}/disable` | POST | cluster-operators |
| `applications/gitopsapi-enable.json` | `/api/v1/applications/{name}/enable` | POST | cluster-operators |
| `pipelines/gitopsapi-v0.1.0-create.json` | `/api/v1/pipelines` | POST | build-managers |
| `pipelines/gitopsapi-v0.1.0-change.json` | `/api/v1/pipelines/{name}/changes` | POST | build-managers |
| `pipelines/gitopsapi-v0.1.0-promote-ete.json` | `/api/v1/pipelines/{name}/promote` | POST | build-managers |
| `pipelines/gitopsapi-v0.1.0-promote-prod.json` | `/api/v1/pipelines/{name}/promote` | POST | build-managers |

**Note**: PR approve/merge (steps 19-21 in run-tests.sh) use dynamic PR numbers captured from the list response — no static JSON payload needed.
