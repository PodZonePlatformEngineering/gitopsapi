"""CC-187 — PROJ-012/S1-GAP-K: Instance SOPS bootstrap endpoint.

POST /api/v1/instances/self/sops-bootstrap
"""

from fastapi import APIRouter, HTTPException

from ...services.instance_sops_service import InstanceSopsService
from ..auth import require_role

router = APIRouter(prefix="/instances", tags=["instances"])


@router.post("/self/sops-bootstrap")
async def instance_sops_bootstrap(
    _=require_role("cluster_operator"),
):
    """Generate an age key-pair for this gitopsapi instance.

    Stores the private key as K8s Secret `gitopsapi-sops-age` in the
    gitopsapi namespace. Returns the public key for the caller to commit
    to `.sops.yaml` in the instance repo.

    Idempotent — re-calling rotates the key (caller must re-encrypt
    existing secrets).
    """
    svc = InstanceSopsService()
    try:
        public_key = await svc.bootstrap()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "public_key": public_key,
        "secret_name": "gitopsapi-sops-age",
        "namespace": svc.namespace,
        "message": "Private key stored as K8s Secret. Add public_key to .sops.yaml in instance repo.",
    }
