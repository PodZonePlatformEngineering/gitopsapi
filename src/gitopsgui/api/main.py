import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import clusters, applications, pipelines, prs, status


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise git repo clone on startup
    from ..services.git_service import GitService
    git = GitService()
    await git.init()
    yield


app = FastAPI(
    title="GitOpsAPI",
    version="0.1.0",
    description="GitOps management API — clusters, applications, change pipelines, PR governance.",
    lifespan=lifespan,
)

app.include_router(clusters.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
app.include_router(pipelines.router, prefix="/api/v1")
app.include_router(prs.router, prefix="/api/v1")
app.include_router(status.router, prefix="/api/v1")

# Serve compiled React frontend — only present in container after multi-stage build
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
