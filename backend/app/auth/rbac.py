"""RBAC dependencies (doc 02 §7).

Roles are patient/caregiver, coordinator, nurse, doctor, admin. Access is granted
by *explicit* role sets per route — deliberately not by rank comparison. A
seniority ladder reads well until you need "nurses and coordinators, but not
doctors", and then it quietly grants the wrong thing.

Usage:

    @router.get("/queue", dependencies=[Depends(require_roles(Role.COORDINATOR))])
    async def list_queue(...): ...

    @router.get("/me")
    async def me(principal: Principal = Depends(current_principal)): ...
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import TokenError, decode_token
from app.config import Settings, get_settings
from app.db import get_session
from app.models.enums import Role
from app.models.org import User

# auto_error=False so a missing header produces our 401 shape, not Starlette's 403.
_bearer = HTTPBearer(auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, as far as a route needs to know."""

    id: uuid.UUID
    role: Role
    name: str
    hospital_id: uuid.UUID | None

    def has_any(self, *roles: Role) -> bool:
        return self.role in roles


async def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """Authenticate from the bearer token.

    The token is re-checked against the database on every request: a JWT that is
    still inside its 30-minute window must stop working the moment the user is
    deactivated or soft-deleted. At pilot scale (doc 02 §1) that lookup is a
    primary-key hit on a warm page, not a bottleneck.
    """
    if credentials is None:
        raise CREDENTIALS_EXCEPTION

    try:
        claims = decode_token(credentials.credentials, settings, expected_type="access")
        user_id = uuid.UUID(claims["sub"])
        role = Role(claims["role"])
    except (TokenError, KeyError, ValueError) as exc:
        raise CREDENTIALS_EXCEPTION from exc

    user = await session.get(User, user_id)
    if user is None or not user.can_login:
        raise CREDENTIALS_EXCEPTION

    # The DB is the source of truth: a role changed after this token was minted
    # must not keep the old grant alive for the rest of the token's life.
    if user.role != role:
        raise CREDENTIALS_EXCEPTION

    return Principal(
        id=user.id,
        role=user.role,
        name=user.name,
        hospital_id=user.hospital_id,
    )


def require_roles(*roles: Role) -> Callable[[Principal], Awaitable[Principal]]:
    """Route guard admitting only the listed roles."""
    if not roles:
        raise ValueError("require_roles() needs at least one role")

    allowed = frozenset(roles)

    async def _guard(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in allowed:
            # 403, not 404: the caller is authenticated, just not permitted.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient role",
            )
        return principal

    return _guard


# Named bundles for the roles that recur across S8–S18, so route files agree on
# what "staff" means instead of each listing its own tuple.
STAFF_ROLES = (Role.COORDINATOR, Role.NURSE, Role.DOCTOR, Role.ADMIN)
CLINICAL_ROLES = (Role.NURSE, Role.DOCTOR, Role.ADMIN)

require_staff = require_roles(*STAFF_ROLES)
require_clinical = require_roles(*CLINICAL_ROLES)
require_admin = require_roles(Role.ADMIN)
require_doctor = require_roles(Role.DOCTOR, Role.ADMIN)
