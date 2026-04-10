"""
Unit tests for SSHOrchestrationService — all tests use GITOPS_SKIP_SSH=1.
Real SSH connections are never made; only the service contract is tested.
"""

import pytest

import gitopsgui.services.ssh_orchestration_service as ssh_svc
from gitopsgui.models.ssh_result import SSHResult
from gitopsgui.services.ssh_orchestration_service import SSHOrchestrationService


@pytest.fixture(autouse=True)
def skip_ssh(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_SSH", "1")


@pytest.fixture(autouse=True)
def reset_mock(monkeypatch):
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", None)
    yield
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", None)


def _svc() -> SSHOrchestrationService:
    return SSHOrchestrationService()


# ---------------------------------------------------------------------------
# execute — default mock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_default_returns_success():
    result = await _svc().execute("192.168.4.52", "mercury-root", "echo hello")
    assert isinstance(result, SSHResult)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.host == "192.168.4.52"
    assert result.command == "echo hello"


@pytest.mark.asyncio
async def test_execute_returns_injected_mock(monkeypatch):
    injected = SSHResult(
        host="192.168.4.52",
        command="uname -a",
        stdout="Linux mercury 6.6.0",
        stderr="",
        exit_code=0,
    )
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", injected)
    result = await _svc().execute("192.168.4.52", "mercury-root", "uname -a")
    assert result is injected
    assert result.stdout == "Linux mercury 6.6.0"


# ---------------------------------------------------------------------------
# SSHResult.success property
# ---------------------------------------------------------------------------

def test_success_true_when_exit_code_zero():
    r = SSHResult(host="h", command="c", stdout="", stderr="", exit_code=0)
    assert r.success is True


def test_success_false_when_exit_code_nonzero():
    r = SSHResult(host="h", command="c", stdout="", stderr="err", exit_code=1)
    assert r.success is False


def test_success_false_when_exit_code_negative():
    r = SSHResult(host="h", command="c", stdout="", stderr="", exit_code=-1)
    assert r.success is False


# ---------------------------------------------------------------------------
# upload / download — skip path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_skip_returns_none():
    result = await _svc().upload("192.168.4.52", "mercury-root", b"script content", "/tmp/egg.sh")
    assert result is None


@pytest.mark.asyncio
async def test_download_skip_returns_empty_bytes():
    result = await _svc().download("192.168.4.52", "mercury-root", "/tmp/output.txt")
    assert result == b""


# ---------------------------------------------------------------------------
# SSHResult JSON roundtrip
# ---------------------------------------------------------------------------

def test_ssh_result_roundtrip():
    original = SSHResult(
        host="192.168.4.52",
        command="ls /tmp",
        stdout="egg.sh\n",
        stderr="",
        exit_code=0,
    )
    serialised = original.model_dump_json()
    restored = SSHResult.model_validate_json(serialised)
    assert restored.host == original.host
    assert restored.command == original.command
    assert restored.stdout == original.stdout
    assert restored.stderr == original.stderr
    assert restored.exit_code == original.exit_code
