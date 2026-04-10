from pydantic import BaseModel


class SSHResult(BaseModel):
    host: str
    command: str
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0
