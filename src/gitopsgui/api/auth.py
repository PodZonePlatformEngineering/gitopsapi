"""
JWT-based auth via OAuth2 proxy + Keycloak.
The OAuth2 proxy injects the decoded JWT as X-Forwarded-User and X-Auth-Request-Groups headers.
Roles are derived from Keycloak group membership: cluster-operators, build-managers, senior-developers.
"""

import os
from dataclasses import dataclass
from fastapi import Header, HTTPException


_GROUP_TO_ROLE = {
    "cluster-operators": "cluster_operator",
    "build-managers": "build_manager",
    "senior-developers": "senior_developer",
}


@dataclass
class CallerInfo:
    username: str
    role: str


def _extract_caller(
    x_forwarded_user: str = Header(default=""),
    x_auth_request_groups: str = Header(default=""),
) -> CallerInfo:
    username = x_forwarded_user or "unknown"
    groups = [g.strip() for g in x_auth_request_groups.split(",") if g.strip()]
    # First matching group wins; production callers may be in multiple groups
    role = None
    for group in groups:
        role = _GROUP_TO_ROLE.get(group)
        if role:
            break

    if not role:
        # Dev/local fallback: honour GITOPSGUI_DEV_ROLE env var
        dev_role = os.getenv("GITOPSGUI_DEV_ROLE", "")
        if dev_role in _GROUP_TO_ROLE.values():
            role = dev_role
        else:
            raise HTTPException(status_code=401, detail="No recognised role in auth headers")

    return CallerInfo(username=username, role=role)


def require_role(*allowed_roles: str):
    """Dependency factory — raises 403 if caller role is not in allowed_roles."""
    def _dep(caller: CallerInfo = _extract_caller) -> CallerInfo:
        if caller.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role {caller.role!r} is not permitted for this operation",
            )
        return caller
    return _dep
