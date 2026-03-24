"""
Unit tests for CredentialStore — uses GITOPS_SKIP_K8S=1 in-memory path.

All tests run without a real K8s cluster.  The in-memory dicts are module-level,
so each test that writes must clean up or use unique IDs to avoid cross-test leakage.
"""

import os
import pytest

import gitopsgui.services.credential_store as cs_module
from gitopsgui.models.credentials import (
    GitForgeCreate,
    GitRepoCreate,
    SopsKeyImport,
)
from gitopsgui.services.credential_store import CredentialStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_local_state():
    cs_module._local_forges.clear()
    cs_module._local_forge_tokens.clear()
    cs_module._local_repos.clear()
    cs_module._local_repo_tokens.clear()
    cs_module._local_sops_meta.clear()
    cs_module._local_sops_priv.clear()


@pytest.fixture(autouse=True)
def skip_k8s_and_age(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_K8S", "1")
    monkeypatch.setenv("GITOPS_SKIP_AGE", "1")
    _clear_local_state()
    yield
    _clear_local_state()


def _svc() -> CredentialStore:
    return CredentialStore()


# ---------------------------------------------------------------------------
# GitForge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_forge():
    svc = _svc()
    spec = GitForgeCreate(id="gh-test", forge_url="https://github.com/TestOrg", git_token="ghp_test", is_default=True)
    created = await svc.create_forge(spec)
    assert created.id == "gh-test"
    assert created.forge_url == "https://github.com/TestOrg"
    assert created.is_default is True

    fetched = await svc.get_forge("gh-test")
    assert fetched is not None
    assert fetched.forge_url == "https://github.com/TestOrg"


@pytest.mark.asyncio
async def test_create_forge_stores_token():
    svc = _svc()
    spec = GitForgeCreate(id="gh-token", forge_url="https://github.com/Org", git_token="secret-token")
    await svc.create_forge(spec)
    token = await svc.get_forge_token("gh-token")
    assert token == "secret-token"


@pytest.mark.asyncio
async def test_list_forges():
    svc = _svc()
    await svc.create_forge(GitForgeCreate(id="forge-a", forge_url="https://github.com/A", git_token="tok-a"))
    await svc.create_forge(GitForgeCreate(id="forge-b", forge_url="https://github.com/B", git_token="tok-b"))
    forges = await svc.list_forges()
    ids = {f.id for f in forges}
    assert {"forge-a", "forge-b"} == ids


@pytest.mark.asyncio
async def test_get_forge_missing_returns_none():
    svc = _svc()
    result = await svc.get_forge("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_delete_forge():
    svc = _svc()
    await svc.create_forge(GitForgeCreate(id="del-forge", forge_url="https://github.com/X", git_token="tok"))
    deleted = await svc.delete_forge("del-forge")
    assert deleted is True
    assert await svc.get_forge("del-forge") is None


@pytest.mark.asyncio
async def test_delete_forge_not_found():
    svc = _svc()
    deleted = await svc.delete_forge("nonexistent")
    assert deleted is False


# ---------------------------------------------------------------------------
# GitRepo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_repo():
    svc = _svc()
    await svc.create_forge(GitForgeCreate(id="forge-r", forge_url="https://github.com/Org", git_token="t"))
    spec = GitRepoCreate(id="myrepo-infra", forge_id="forge-r", repo_name="myrepo-infra")
    created = await svc.create_repo(spec)
    assert created.id == "myrepo-infra"
    assert created.forge_id == "forge-r"
    assert created.repo_name == "myrepo-infra"

    fetched = await svc.get_repo("myrepo-infra")
    assert fetched is not None
    assert fetched.repo_name == "myrepo-infra"


@pytest.mark.asyncio
async def test_create_repo_with_token():
    svc = _svc()
    await svc.create_forge(GitForgeCreate(id="forge-t", forge_url="https://github.com/Org", git_token="forge-tok"))
    spec = GitRepoCreate(id="repo-with-tok", forge_id="forge-t", repo_name="my-repo", git_token="repo-specific-token")
    await svc.create_repo(spec)
    token = await svc.get_repo_token("repo-with-tok")
    assert token == "repo-specific-token"


@pytest.mark.asyncio
async def test_list_repos():
    svc = _svc()
    await svc.create_forge(GitForgeCreate(id="forge-l", forge_url="https://github.com/Org", git_token="t"))
    await svc.create_repo(GitRepoCreate(id="repo-1", forge_id="forge-l", repo_name="repo-1"))
    await svc.create_repo(GitRepoCreate(id="repo-2", forge_id="forge-l", repo_name="repo-2"))
    repos = await svc.list_repos()
    ids = {r.id for r in repos}
    assert {"repo-1", "repo-2"} == ids


@pytest.mark.asyncio
async def test_delete_repo():
    svc = _svc()
    await svc.create_forge(GitForgeCreate(id="forge-d", forge_url="https://github.com/Org", git_token="t"))
    await svc.create_repo(GitRepoCreate(id="del-repo", forge_id="forge-d", repo_name="del-repo"))
    deleted = await svc.delete_repo("del-repo")
    assert deleted is True
    assert await svc.get_repo("del-repo") is None


# ---------------------------------------------------------------------------
# SopsKey
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_sops_key_returns_public_key_only():
    svc = _svc()
    result = await svc.generate_sops_key("test-cluster-sops")
    assert result.id == "test-cluster-sops"
    assert result.public_key.startswith("age1fakestub")
    # private key must NOT be in the response model
    assert not hasattr(result, "private_key") or result.__class__.__fields__.get("private_key") is None


@pytest.mark.asyncio
async def test_generate_sops_key_private_key_stored():
    svc = _svc()
    await svc.generate_sops_key("stored-sops")
    priv = await svc.get_sops_private_key("stored-sops")
    assert priv is not None
    assert "AGE-SECRET-KEY" in priv


@pytest.mark.asyncio
async def test_import_sops_key():
    svc = _svc()
    spec = SopsKeyImport(
        id="imported-sops",
        public_key="age1importedpublickey",
        private_key="AGE-SECRET-KEY-1IMPORTEDPRIVATEKEY",
    )
    result = await svc.import_sops_key(spec)
    assert result.id == "imported-sops"
    assert result.public_key == "age1importedpublickey"
    priv = await svc.get_sops_private_key("imported-sops")
    assert priv == "AGE-SECRET-KEY-1IMPORTEDPRIVATEKEY"


@pytest.mark.asyncio
async def test_list_sops_keys():
    svc = _svc()
    await svc.generate_sops_key("sops-a")
    await svc.generate_sops_key("sops-b")
    keys = await svc.list_sops_keys()
    ids = {k.id for k in keys}
    assert {"sops-a", "sops-b"} == ids


@pytest.mark.asyncio
async def test_get_sops_key_missing_returns_none():
    svc = _svc()
    result = await svc.get_sops_key("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_delete_sops_key():
    svc = _svc()
    await svc.generate_sops_key("del-sops")
    deleted = await svc.delete_sops_key("del-sops")
    assert deleted is True
    assert await svc.get_sops_key("del-sops") is None
    priv = await svc.get_sops_private_key("del-sops")
    assert priv is None


@pytest.mark.asyncio
async def test_delete_sops_key_not_found():
    svc = _svc()
    deleted = await svc.delete_sops_key("ghost")
    assert deleted is False
