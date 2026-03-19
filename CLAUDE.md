# gitopsapi — Claude Code session context

## Role
FastAPI backend (`src/gitopsgui/`) + React frontend (future). Git-as-object-store: all writes
via feature branch + PR, no direct commits to main. Read task assignments filtered
`🧠 Claude Code` from `/Users/martincolley/workspace/podzoneAgentTeam/planning/team-tasklist.md`.
Write blockers/questions to `QUESTIONS.md` in this repo (monitored by Team Lead).
Escalate to Team Lead via `podzoneAgentTeam/agents/team-lead/incoming/YYYY-MM-DD-{task}.md`.

## Commands
```
.venv/bin/pytest -q --tb=short      # run all tests (177 passing as of v0.1.5)
.venv/bin/pytest -q --tb=short tests/test_services/test_app_service.py   # single module
```

## Auth pattern — critical gotcha
`require_role(*roles)` uses a callable class (`_RoleChecker`), not a closure:
```python
caller: CallerInfo = require_role("build_manager")          # correct
caller: CallerInfo = Depends(require_role("build_manager"))  # WRONG — double-wraps
```
FastAPI resolves nested `Depends` correctly only with the callable class pattern.
Groups → roles mapping is in `src/gitopsgui/api/auth.py` (`_GROUP_TO_ROLE`).
Dev fallback: `GITOPSGUI_DEV_ROLE=cluster_operator` env var.

## Multi-repo layout (per cluster)
Each cluster has two repos: `{cluster}-infra` and `{cluster}-apps`.

| What | Repo | Path |
|---|---|---|
| Flux Kustomization entries | `{cluster}-infra` | `clusters/{cluster}/{cluster}-apps.yaml` |
| HelmRelease + HelmRepository | `{cluster}-apps` | `gitops/gitops-apps/{name}/{name}.yaml` |
| Per-cluster values override | `{cluster}-apps` | `gitops/gitops-apps/{name}/{name}-values-{cluster}.yaml` |

Routing helpers live in `src/gitopsgui/services/repo_router.py`.
Services use `self._git = None` / `self._gh = None` (None = use cluster routing).
Tests inject `svc._git = AsyncMock()` to bypass routing.

## GitService — instance-based (not singleton)
Constructor: `GitService(repo_url=None, local_path=None)`.
`repo_url=None` → uses `GITOPS_REPO_URL`; `repo_url=""` → raises on `_get_repo()`.
Repos clone lazily on first use; `SKIP_INIT=1` skips clone.

## Local dev env vars
```
GITOPS_SKIP_INIT=1      # skip git clone on startup
GITOPS_SKIP_PUSH=1      # skip git push
GITOPS_SKIP_GITHUB=1    # use LocalPRStore instead of GitHub API
GITOPSGUI_DEV_ROLE=cluster_operator   # bypass OAuth2 proxy auth
GITHUB_ORG=MoTTTT       # org for per-cluster repo URLs
```

## Known gaps (document, don't implement)
- `list_applications()` / `get_application(name)`: no cluster registry → empty in multi-repo prod
- `list_by_application(app_id)` in AppConfigService: same gap
- Deploy key + SOPS key automation (TR-039a/b): manual steps, not yet automated (CC-053)
- Gap label: **CC-055** (cluster registry)

## API-First Testing Protocol

Before calling any GitOpsAPI write endpoint in testing, document and review all object attributes
(fields, types, defaults, constraints) first. This practice catches schema gaps before roundtrip
failures in Flux or CAPI.

**Steps** (required before every POST/PUT in a test session):

1. List all fields for the target object (ClusterSpec, ApplicationSpec, ApplicationClusterConfig).
2. Review each field's type, default value, and any constraints (e.g. `ip_range` format, enum values,
   required vs. optional) against the Pydantic model in `src/gitopsgui/models/`.
3. Verify the test data JSON file matches the schema — confirm `_comment`/`_curl` metadata keys are
   present (they are stripped before the payload is sent; never include them in production code).
4. Make the API call only after steps 1–3 are complete and any schema gaps are resolved.

**Test data directory**: `tests/test_data/`

| Subdirectory | Endpoint |
| --- | --- |
| `clusters/` | `POST /api/v1/clusters`, `PUT /api/v1/clusters/{name}` |
| `applications/` | `POST /api/v1/applications`, `PUT /api/v1/applications/{name}` |
| `application-configs/` | `POST /api/v1/application-configs`, `PATCH /api/v1/application-configs/{id}` |

Every write endpoint must have a corresponding test data file with all fields documented.
See `docs/api-first-testing-protocol.md` for the full test plan.

## Image build (on erectus 192.168.1.201)
```bash
rsync -az --delete src/ colleymj@192.168.1.201:~/gitopsapi-build/src/
# Then on erectus:
docker build -t ghcr.io/motttt/gitopsapi:vX.Y.Z -f Dockerfile.api-only .
docker push ghcr.io/motttt/gitopsapi:vX.Y.Z
```
Current deployed image: `v0.1.4` (gitopsdev cluster, HelmRelease `gitopsapi/gitopsapi-gitopsapi`).
Next: `v0.1.5` (multi-repo routing, app-config CRUD).

## kubectl access (when Cloudflare WARP is down)
```bash
kubectl --context gitopsdev-admin@gitopsdev --server=https://192.168.1.80:6442 ...
kubectl --context openclaw-admin@openclaw   --server=https://192.168.1.80:6447 ...
```

## Key files outside this repo

- Tasks: `/Users/martincolley/workspace/podzoneAgentTeam/planning/team-tasklist.md`
- Specs: `podzoneAgentTeam/specifications/gitopsgui-requirements.md` (v0.4 current)
- API schema: `podzoneAgentTeam/specifications/gitopsapi-schema.md`
- Team Lead inbox (escalations): `podzoneAgentTeam/agents/team-lead/incoming/`
