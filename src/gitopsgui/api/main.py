import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from .routers import clusters, applications, application_configs, pipelines, prs, repositories, status, forges, repos, sops_keys

# Set to True once startup tasks (git init, kubeconfig) complete successfully.
_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready

    # CC-010: Write management cluster kubeconfig to disk if injected via env var.
    mgmt_kubeconfig = os.environ.get("MGMT_KUBECONFIG_SECRET", "").strip()
    if mgmt_kubeconfig:
        kubeconfig_path = Path("/tmp/mgmt-kubeconfig")
        kubeconfig_path.write_text(mgmt_kubeconfig)
        kubeconfig_path.chmod(0o600)

    # Initialise git repo clone on startup — only when a writable repo is configured.
    from ..services.git_service import GitService, REPO_URL
    if REPO_URL:
        git = GitService()
        await git.init()

    _ready = True
    yield
    _ready = False


app = FastAPI(
    title="GitOpsAPI",
    version="0.2.0",
    description="GitOps management API — clusters, applications, change pipelines, PR governance.",
    lifespan=lifespan,
)

app.include_router(clusters.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
app.include_router(application_configs.router, prefix="/api/v1")
app.include_router(pipelines.router, prefix="/api/v1")
app.include_router(prs.router, prefix="/api/v1")
app.include_router(repositories.router, prefix="/api/v1")
app.include_router(status.router, prefix="/api/v1")
app.include_router(forges.router, prefix="/api/v1")
app.include_router(repos.router, prefix="/api/v1")
app.include_router(sops_keys.router, prefix="/api/v1")


# CC-009: Lightweight health and readiness probes (no auth, no git I/O).
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def ready():
    if _ready:
        return {"status": "ready"}
    return Response(content='{"status":"starting"}', status_code=503, media_type="application/json")


# Serve compiled React frontend — only present in container after multi-stage build.
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
