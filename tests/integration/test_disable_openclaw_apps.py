"""
Integration test: disable ollama and qdrant on the openclaw cluster.

Reads and writes the REAL file at:
  cluster09/clusters/openclaw/openclaw-apps.yaml

Git branch/commit/push and GitHub PR operations are mocked so no actual
git or GitHub calls are made.  The file on disk IS modified by the test.

Run with:
  .venv/bin/pytest tests/integration/test_disable_openclaw_apps.py -v
"""

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gitopsgui.services.app_service import AppService
from gitopsgui.services.git_service import REPO_LOCAL_PATH

# Path to the real cluster09 workspace
_CLUSTER09 = Path(__file__).parents[3] / "cluster09"
_APPS_FILE = _CLUSTER09 / "clusters" / "openclaw" / "openclaw-apps.yaml"

pytestmark = pytest.mark.skipif(
    not _APPS_FILE.exists(),
    reason=f"cluster09 repo not present at {_APPS_FILE}",
)


def _make_svc() -> AppService:
    svc = AppService()
    # Mock only git plumbing that touches the remote; file reads/writes use real FS
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.commit = AsyncMock(return_value="integration-sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(
        return_value="https://github.com/MoTTTT/cluster09/pull/999"
    )
    # Wire read_file and write_file to the real filesystem via REPO_LOCAL_PATH patch
    async def _read_file(path: str) -> str:
        return (_CLUSTER09 / path).read_text()

    async def _write_file(path: str, content: str) -> None:
        target = _CLUSTER09 / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    svc._git.read_file = _read_file
    svc._git.write_file = _write_file
    return svc


@pytest.fixture(autouse=True)
def backup_apps_file():
    """Back up openclaw-apps.yaml before each test and restore after."""
    backup = _APPS_FILE.read_text()
    yield
    _APPS_FILE.write_text(backup)


async def test_disable_ollama_modifies_file_on_disk():
    svc = _make_svc()
    await svc.disable_application("ollama", "openclaw")

    result = _APPS_FILE.read_text()

    # ollama block is commented out
    for line in result.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert "ollama" not in line, f"Uncommented line still references ollama: {line!r}"

    # openclaw and qdrant blocks are untouched
    assert "path: ./gitops/gitops-apps/openclaw" in result
    assert "path: ./gitops/gitops-apps/qdrant" in result


async def test_disable_qdrant_modifies_file_on_disk():
    svc = _make_svc()
    await svc.disable_application("qdrant", "openclaw")

    result = _APPS_FILE.read_text()

    # qdrant block is commented out
    for line in result.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert "qdrant" not in line, f"Uncommented line still references qdrant: {line!r}"

    # openclaw and ollama blocks are untouched
    assert "path: ./gitops/gitops-apps/openclaw" in result
    assert "path: ./gitops/gitops-apps/ollama" in result


async def test_disable_both_ollama_and_qdrant():
    """Disable both apps sequentially; only openclaw block remains active."""
    svc = _make_svc()
    await svc.disable_application("ollama", "openclaw")
    # Re-read after first write so second call sees the updated file
    await svc.disable_application("qdrant", "openclaw")

    result = _APPS_FILE.read_text()
    active_lines = [
        l for l in result.splitlines()
        if l.strip() and not l.lstrip().startswith("#")
    ]
    # Only the openclaw kustomization and its --- separator should have active lines
    active_text = "\n".join(active_lines)
    assert "ollama" not in active_text
    assert "qdrant" not in active_text
    assert "openclaw" in active_text


async def test_disable_returns_pr_url():
    svc = _make_svc()
    result = await svc.disable_application("ollama", "openclaw")
    assert result.pr_url == "https://github.com/MoTTTT/cluster09/pull/999"


async def test_file_is_restored_after_test(backup_apps_file):
    """Sanity check: after test teardown the file is back to its original state."""
    original = _APPS_FILE.read_text()
    svc = _make_svc()
    await svc.disable_application("ollama", "openclaw")
    modified = _APPS_FILE.read_text()
    assert modified != original  # file was changed during the test
    # backup_apps_file fixture will restore it after this test returns
