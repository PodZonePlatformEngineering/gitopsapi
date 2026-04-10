import json
import os
from pathlib import Path

from .hypervisor_service import HypervisorService
from .ssh_orchestration_service import SSHOrchestrationService
from ..models.ssh_result import SSHResult

_SCRIPT_DIR = Path(__file__).parent.parent / "egg_scripts"
_REMOTE_DIR = "/tmp/egg"


class EggScriptError(Exception):
    """Raised when an Egg script exits non-zero."""


class EggScriptService:

    def __init__(self):
        self._ssh = SSHOrchestrationService()
        self._hyp = HypervisorService()

    async def _ctx(self, hypervisor_name: str) -> dict:
        return await self._hyp.get_ssh_context(hypervisor_name)

    async def _upload(self, host: str, creds_ref: str, script_name: str) -> None:
        content = (_SCRIPT_DIR / script_name).read_bytes()
        remote = f"{_REMOTE_DIR}/{script_name}"
        await self._ssh.execute(host, creds_ref, f"mkdir -p {_REMOTE_DIR}")
        await self._ssh.upload(host, creds_ref, content, remote)
        await self._ssh.execute(host, creds_ref, f"chmod +x {remote}")

    async def _run(self, host: str, creds_ref: str, script_name: str,
                   env: dict | None = None) -> SSHResult:
        env_str = " ".join(f'{k}="{v}"' for k, v in (env or {}).items())
        cmd = f"{env_str} {_REMOTE_DIR}/{script_name}".strip()
        result = await self._ssh.execute(host, creds_ref, cmd)
        if not result.success:
            raise EggScriptError(
                f"{script_name} exited {result.exit_code}: {result.stderr}"
            )
        return result

    async def audit(self, hypervisor_name: str) -> dict:
        """Run egg-audit.sh; return parsed JSON dict."""
        ctx = await self._ctx(hypervisor_name)
        await self._upload(ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-audit.sh")
        result = await self._run(ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-audit.sh")
        return json.loads(result.stdout)

    async def create_template(self, hypervisor_name: str, config: dict) -> dict:
        """Run egg-template.sh; return parsed JSON result."""
        ctx = await self._ctx(hypervisor_name)
        await self._upload(ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-template.sh")
        result = await self._run(
            ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-template.sh", env=config
        )
        return json.loads(result.stdout)

    async def provision_cluster(self, hypervisor_name: str, config: dict) -> dict:
        """Run egg-provision.sh; return parsed JSON result (includes kubeconfig_path)."""
        ctx = await self._ctx(hypervisor_name)
        await self._upload(ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-provision.sh")
        result = await self._run(
            ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-provision.sh", env=config
        )
        return json.loads(result.stdout)

    async def download_kubeconfig(self, hypervisor_name: str,
                                  remote_path: str) -> bytes:
        """Download kubeconfig file from hypervisor after provisioning."""
        ctx = await self._ctx(hypervisor_name)
        return await self._ssh.download(
            ctx["host_ip"], ctx["ssh_credentials_ref"], remote_path
        )

    async def platform_install(self, hypervisor_name: str, config: dict) -> dict:
        """Run egg-platform-install.sh; return parsed JSON result."""
        ctx = await self._ctx(hypervisor_name)
        await self._upload(
            ctx["host_ip"], ctx["ssh_credentials_ref"], "egg-platform-install.sh"
        )
        result = await self._run(
            ctx["host_ip"], ctx["ssh_credentials_ref"],
            "egg-platform-install.sh", env=config
        )
        return json.loads(result.stdout)
