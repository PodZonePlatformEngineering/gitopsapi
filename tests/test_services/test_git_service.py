"""
Unit tests for GitService — mocks gitpython so no real repo is needed.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from gitopsgui.services.git_service import GitService, REPO_LOCAL_PATH


@pytest.fixture()
def svc():
    s = GitService()
    GitService._repo = MagicMock()  # inject a mock repo
    return s


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def test_read_file_returns_contents(tmp_path, svc):
    f = tmp_path / "test.yaml"
    f.write_text("key: value\n")
    with patch("gitopsgui.services.git_service.REPO_LOCAL_PATH", tmp_path):
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(svc.read_file("test.yaml"))
    assert result == "key: value\n"


def test_read_file_missing_raises(tmp_path, svc):
    import asyncio
    with patch("gitopsgui.services.git_service.REPO_LOCAL_PATH", tmp_path):
        with pytest.raises(FileNotFoundError):
            asyncio.get_event_loop().run_until_complete(svc.read_file("missing.yaml"))


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def test_list_dir_returns_subdirs(tmp_path, svc):
    (tmp_path / "dir-a").mkdir()
    (tmp_path / "dir-b").mkdir()
    (tmp_path / "file.yaml").write_text("")
    import asyncio
    with patch("gitopsgui.services.git_service.REPO_LOCAL_PATH", tmp_path):
        result = asyncio.get_event_loop().run_until_complete(svc.list_dir("."))
    assert set(result) == {"dir-a", "dir-b"}


def test_list_dir_nonexistent_returns_empty(tmp_path, svc):
    import asyncio
    with patch("gitopsgui.services.git_service.REPO_LOCAL_PATH", tmp_path):
        result = asyncio.get_event_loop().run_until_complete(svc.list_dir("nonexistent"))
    assert result == []


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def test_write_file_creates_file_and_stages(tmp_path, svc):
    import asyncio
    with patch("gitopsgui.services.git_service.REPO_LOCAL_PATH", tmp_path):
        asyncio.get_event_loop().run_until_complete(
            svc.write_file("subdir/test.yaml", "content: 42\n")
        )
    assert (tmp_path / "subdir" / "test.yaml").read_text() == "content: 42\n"
    svc._get_repo().index.add.assert_called_once()


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------

def test_commit_returns_sha(svc):
    import asyncio
    mock_commit = MagicMock()
    mock_commit.hexsha = "abc123def456"
    svc._get_repo().index.commit.return_value = mock_commit

    result = asyncio.get_event_loop().run_until_complete(svc.commit("test commit"))
    assert result == "abc123def456"


# ---------------------------------------------------------------------------
# _get_repo raises if not initialised
# ---------------------------------------------------------------------------

def test_get_repo_raises_if_not_initialised():
    GitService._repo = None
    svc = GitService()
    with pytest.raises(RuntimeError, match="not initialised"):
        svc._get_repo()
