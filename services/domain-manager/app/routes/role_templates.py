import logging
import re
import urllib.parse
import httpx
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.role_template import RoleTemplate
from app.auth import (
    require_admin,
    verify_token,
    introspect_token,
    audit_log,
    validate_realm_name,
    get_keycloak_admin_token,
    KEYCLOAK_SERVER_URL,
)
from app.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/role-templates", tags=["role-templates"])

_TEMPLATE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,98}[a-zA-Z0-9]$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RoleDefinition(BaseModel):
    name: str
    description: str = ""
    composite_roles: List[str] = []
    permissions: List[str] = []

    @field_validator("name")
    @classmethod
    def validate_role_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]{1,100}$", v):
            raise ValueError(
                "Role name must be 1-100 alphanumeric characters, hyphens, or underscores"
            )
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("Role description must be under 500 characters")
        return v


class RoleTemplateCreate(BaseModel):
    name: str
    description: str = ""
    roles: List[RoleDefinition]

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _TEMPLATE_NAME_PATTERN.match(v):
            raise ValueError(
                "Template name must be 2-100 alphanumeric characters, hyphens, or underscores"
            )
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("Description must be under 500 characters")
        return v

    @field_validator("roles")
    @classmethod
    def validate_roles_nonempty(cls, v: List[RoleDefinition]) -> List[RoleDefinition]:
        if not v:
            raise ValueError("At least one role is required")
        names = [r.name for r in v]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate role names are not allowed")
        return v


class RoleTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    roles: Optional[List[RoleDefinition]] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TEMPLATE_NAME_PATTERN.match(v):
            raise ValueError(
                "Template name must be 2-100 alphanumeric characters, hyphens, or underscores"
            )
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 500:
            raise ValueError("Description must be under 500 characters")
        return v

    @field_validator("roles")
    @classmethod
    def validate_roles(
        cls, v: Optional[List[RoleDefinition]]
    ) -> Optional[List[RoleDefinition]]:
        if v is not None:
            if not v:
                raise ValueError("At least one role is required")
            names = [r.name for r in v]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate role names are not allowed")
        return v


class RoleTemplateResponse(BaseModel):
    id: int
    name: str
    description: str
    roles: List[RoleDefinition]
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _template_to_response(template: RoleTemplate) -> RoleTemplateResponse:
    roles = [RoleDefinition(**r) for r in template.roles]
    return RoleTemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        roles=roles,
        is_default=template.is_default,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=List[RoleTemplateResponse])
async def list_templates(
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(verify_token),
):
    """List all role templates. Requires authentication to prevent leaking
    security role structure to unauthenticated callers."""
    statement = select(RoleTemplate).order_by(RoleTemplate.name)
    results = await session.execute(statement)
    templates = results.scalars().all()
    return [_template_to_response(t) for t in templates]


@router.post("", response_model=RoleTemplateResponse, status_code=201)
@limiter.limit("15/minute")
async def create_template(
    request: Request,
    body: RoleTemplateCreate,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Create a new role template. Admin only."""
    existing = await session.execute(
        select(RoleTemplate).where(RoleTemplate.name == body.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail=f"Template '{body.name}' already exists"
        )

    template = RoleTemplate(
        name=body.name,
        description=body.description,
        roles=[r.model_dump() for r in body.roles],
        is_default=False,
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)

    audit_log("role_template_create", claims, details=f"name={body.name}")
    return _template_to_response(template)


@router.put("/{template_id}", response_model=RoleTemplateResponse)
@limiter.limit("15/minute")
async def update_template(
    request: Request,
    template_id: int,
    body: RoleTemplateUpdate,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Update a role template. Admin only. Cannot modify default templates."""
    result = await session.execute(
        select(RoleTemplate).where(RoleTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_default:
        raise HTTPException(status_code=403, detail="Cannot modify default templates")

    if body.name is not None:
        if body.name != template.name:
            dup = await session.execute(
                select(RoleTemplate).where(RoleTemplate.name == body.name)
            )
            if dup.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Template '{body.name}' already exists",
                )
        template.name = body.name

    if body.description is not None:
        template.description = body.description

    if body.roles is not None:
        template.roles = [r.model_dump() for r in body.roles]

    template.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(template)

    audit_log("role_template_update", claims, details=f"id={template_id}")
    return _template_to_response(template)


@router.delete("/{template_id}")
@limiter.limit("15/minute")
async def delete_template(
    request: Request,
    template_id: int,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Delete a role template. Cannot delete default templates. Admin only."""
    # Defense-in-depth: introspect token for destructive operations to catch
    # revoked tokens within the JWKS cache TTL window.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token_active = await introspect_token(auth_header[7:])
        if not token_active:
            raise HTTPException(status_code=401, detail="Token has been revoked")

    result = await session.execute(
        select(RoleTemplate).where(RoleTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_default:
        raise HTTPException(status_code=403, detail="Cannot delete default templates")

    await session.delete(template)
    await session.commit()

    audit_log(
        "role_template_delete", claims,
        details=f"id={template_id} name={template.name}",
    )
    return {"status": "deleted", "id": template_id}


@router.post("/{template_id}/apply/{realm}")
@limiter.limit("10/minute")
async def apply_template_to_realm(
    request: Request,
    template_id: int,
    realm: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Apply a role template to a Keycloak realm. Creates roles via the admin API. Admin only."""
    validate_realm_name(realm)

    result = await session.execute(
        select(RoleTemplate).where(RoleTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    token = await get_keycloak_admin_token()
    if not token:
        raise HTTPException(
            status_code=502, detail="Failed to obtain Keycloak admin token"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    created: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    encoded_realm = urllib.parse.quote(realm, safe="")

    async with httpx.AsyncClient() as http:
        # Create base roles first
        for role_def in template.roles:
            role_name = role_def["name"]
            role_desc = role_def.get("description", "")

            payload = {
                "name": role_name,
                "description": role_desc,
            }

            try:
                resp = await http.post(
                    f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/roles",
                    json=payload,
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code == 201:
                    created.append(role_name)
                elif resp.status_code == 409:
                    skipped.append(role_name)
                else:
                    resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(
                    "Failed to create role '%s' in realm '%s': status=%s",
                    role_name, realm, e.response.status_code,
                )
                errors.append(role_name)

        # Set up composite roles after all base roles exist
        for role_def in template.roles:
            composite_roles = role_def.get("composite_roles", [])
            if not composite_roles:
                continue

            role_name = role_def["name"]
            encoded_role = urllib.parse.quote(role_name, safe="")
            composite_payloads = []

            for comp_name in composite_roles:
                encoded_comp = urllib.parse.quote(comp_name, safe="")
                try:
                    resp = await http.get(
                        f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/roles/{encoded_comp}",
                        headers=headers,
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                    composite_payloads.append(resp.json())
                except Exception as e:
                    logger.warning(
                        "Could not resolve composite role '%s' for '%s' in realm '%s': exc_type=%s",
                        comp_name, role_name, realm, type(e).__name__,
                    )

            if composite_payloads:
                try:
                    resp = await http.post(
                        f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/roles/{encoded_role}/composites",
                        json=composite_payloads,
                        headers=headers,
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                except Exception as e:
                    logger.error(
                        "Failed to set composites for role '%s' in realm '%s': exc_type=%s",
                        role_name, realm, type(e).__name__,
                    )

    audit_log(
        "role_template_apply", claims, realm=realm,
        details=f"template={template.name} created={created} skipped={skipped} errors={errors}",
    )

    return {
        "status": "applied",
        "template": template.name,
        "realm": realm,
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }
