import asyncio
import base64
import io
import os
from typing import Optional

import paramiko
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException

from ..models.ssh_result import SSHResult

GITOPSAPI_NAMESPACE = os.environ.get("GITOPSAPI_NAMESPACE", "gitopsapi")

_mock_execute_response: Optional[SSHResult] = None   # set by tests


def _skip_ssh() -> bool:
    return os.environ.get("GITOPS_SKIP_SSH", "") == "1"


def _v1() -> k8s_client.CoreV1Api:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return k8s_client.CoreV1Api()


def _read_ssh_credentials(credentials_ref: str) -> dict:
    """Return {'username': ..., 'password': ...} from K8s Secret."""
    try:
        secret = _v1().read_namespaced_secret(credentials_ref, GITOPSAPI_NAMESPACE)
        data = secret.data or {}
        return {
            "username": base64.b64decode(data.get("username", b"root")).decode(),
            "password": base64.b64decode(data.get("password", b"")).decode(),
        }
    except ApiException as e:
        raise RuntimeError(
            f"SSH credentials secret {credentials_ref!r} not found: {e}"
        ) from e


def _sync_execute(host: str, username: str, password: str, command: str) -> SSHResult:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=username, password=password, timeout=30)
        _, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        return SSHResult(
            host=host,
            command=command,
            stdout=stdout.read().decode(errors="replace"),
            stderr=stderr.read().decode(errors="replace"),
            exit_code=exit_code,
        )
    finally:
        client.close()


def _sync_upload(
    host: str, username: str, password: str, content: bytes, remote_path: str
) -> None:
    transport = paramiko.Transport((host, 22))
    transport.connect(username=username, password=password)
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.putfo(io.BytesIO(content), remote_path)
        sftp.close()
    finally:
        transport.close()


def _sync_download(
    host: str, username: str, password: str, remote_path: str
) -> bytes:
    transport = paramiko.Transport((host, 22))
    transport.connect(username=username, password=password)
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        buf = io.BytesIO()
        sftp.getfo(remote_path, buf)
        sftp.close()
        return buf.getvalue()
    finally:
        transport.close()


class SSHOrchestrationService:

    async def execute(
        self,
        host: str,
        ssh_credentials_ref: str,
        command: str,
    ) -> SSHResult:
        if _skip_ssh():
            return _mock_execute_response or SSHResult(
                host=host, command=command, stdout="", stderr="", exit_code=0
            )
        creds = _read_ssh_credentials(ssh_credentials_ref)
        return await asyncio.to_thread(
            _sync_execute, host, creds["username"], creds["password"], command
        )

    async def upload(
        self,
        host: str,
        ssh_credentials_ref: str,
        content: bytes,
        remote_path: str,
    ) -> None:
        if _skip_ssh():
            return
        creds = _read_ssh_credentials(ssh_credentials_ref)
        await asyncio.to_thread(
            _sync_upload,
            host,
            creds["username"],
            creds["password"],
            content,
            remote_path,
        )

    async def download(
        self,
        host: str,
        ssh_credentials_ref: str,
        remote_path: str,
    ) -> bytes:
        if _skip_ssh():
            return b""
        creds = _read_ssh_credentials(ssh_credentials_ref)
        return await asyncio.to_thread(
            _sync_download,
            host,
            creds["username"],
            creds["password"],
            remote_path,
        )
