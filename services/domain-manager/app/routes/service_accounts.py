import logging
import re
import urllib.parse
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator
import httpx

from app.auth import (
    require_admin,
    audit_log,
    validate_realm_name,
    get_keycloak_admin_token,
    KEYCLOAK_SERVER_URL,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/realms/{realm}/service-accounts",
    tags=["service-accounts"],
)

_CLIENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,253}[a-zA-Z0-9]$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ServiceAccountCreate(BaseModel):
    client_id: str
    description: str = ""
    roles: List[str] = []

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, v: str) -> str:
        if not _CLIENT_ID_PATTERN.match(v):
            raise ValueError(
                "client_id must be 2-255 alphanumeric characters, dots, hyphens, or underscores"
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
    def validate_roles(cls, v: List[str]) -> List[str]:
        for role in v:
            if not re.match(r"^[a-zA-Z0-9_-]{1,100}$", role):
                raise ValueError(f"Invalid role name: {role}")
        return v


class ServiceAccountResponse(BaseModel):
    id: str
    client_id: str
    description: str
    enabled: bool


class ServiceAccountSecret(BaseModel):
    client_id: str
    secret: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
async def _admin_headers() -> dict:
    token = await get_keycloak_admin_token()
    if not token:
        raise HTTPException(
            status_code=502, detail="Failed to obtain Keycloak admin token"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _resolve_client_internal_id(
    http: httpx.AsyncClient,
    realm: str,
    client_id: str,
    headers: dict,
) -> str:
    """Resolve a Keycloak clientId to its internal UUID."""
    encoded_realm = urllib.parse.quote(realm, safe="")
    resp = await http.get(
        f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients",
        params={"clientId": client_id},
        headers=headers,
        timeout=10.0,
    )
    resp.raise_for_status()
    matches = resp.json()

    for m in matches:
        if m["clientId"] == client_id:
            return m["id"]

    raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")


async def _assign_service_account_roles(
    http: httpx.AsyncClient,
    realm: str,
    internal_client_id: str,
    role_names: List[str],
    headers: dict,
) -> None:
    """Assign realm roles to the service account user of a client."""
    encoded_realm = urllib.parse.quote(realm, safe="")
    sa_resp = await http.get(
        f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients/{internal_client_id}/service-account-user",
        headers=headers,
        timeout=10.0,
    )
    sa_resp.raise_for_status()
    sa_user_id = sa_resp.json()["id"]

    role_payloads = []
    for rn in role_names:
        encoded_role = urllib.parse.quote(rn, safe="")
        role_resp = await http.get(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/roles/{encoded_role}",
            headers=headers,
            timeout=10.0,
        )
        if role_resp.status_code == 404:
            logger.warning("Role '%s' not found in realm '%s', skipping", rn, realm)
            continue
        role_resp.raise_for_status()
        role_payloads.append(role_resp.json())

    if role_payloads:
        assign_resp = await http.post(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/users/{sa_user_id}/role-mappings/realm",
            json=role_payloads,
            headers=headers,
            timeout=10.0,
        )
        assign_resp.raise_for_status()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=List[ServiceAccountResponse])
async def list_service_accounts(
    realm: str,
    claims: dict = Depends(require_admin),
):
    """List all clients with service accounts enabled in the realm. Admin only."""
    validate_realm_name(realm)

    headers = await _admin_headers()
    encoded_realm = urllib.parse.quote(realm, safe="")

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients",
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=404, detail=f"Realm '{realm}' not found"
            )
        resp.raise_for_status()
        clients = resp.json()

    result: List[ServiceAccountResponse] = []
    for c in clients:
        if not c.get("serviceAccountsEnabled", False):
            continue
        result.append(
            ServiceAccountResponse(
                id=c["id"],
                client_id=c["clientId"],
                description=c.get("description", ""),
                enabled=c.get("enabled", True),
            )
        )

    return result


@router.post("", response_model=ServiceAccountSecret, status_code=201)
async def create_service_account(
    realm: str,
    body: ServiceAccountCreate,
    claims: dict = Depends(require_admin),
):
    """Create a new service account client in the realm. Admin only."""
    validate_realm_name(realm)

    headers = await _admin_headers()
    encoded_realm = urllib.parse.quote(realm, safe="")

    client_payload = {
        "clientId": body.client_id,
        "description": body.description,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "clientAuthenticatorType": "client-secret",
    }

    async with httpx.AsyncClient() as http:
        # Create client
        resp = await http.post(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients",
            json=client_payload,
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code == 409:
            raise HTTPException(
                status_code=409,
                detail=f"Client '{body.client_id}' already exists",
            )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=404, detail=f"Realm '{realm}' not found"
            )
        resp.raise_for_status()

        # Get internal ID from Location header
        location = resp.headers.get("Location", "")
        internal_id = location.rsplit("/", 1)[-1] if location else None

        if not internal_id:
            internal_id = await _resolve_client_internal_id(
                http, realm, body.client_id, headers
            )

        # Fetch the generated client secret
        secret_resp = await http.get(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients/{internal_id}/client-secret",
            headers=headers,
            timeout=10.0,
        )
        secret_resp.raise_for_status()
        secret = secret_resp.json().get("value", "")

        # Assign roles if requested
        if body.roles:
            await _assign_service_account_roles(
                http, realm, internal_id, body.roles, headers
            )

    audit_log(
        "service_account_create", claims, realm=realm,
        details=f"client_id={body.client_id}",
    )

    return ServiceAccountSecret(client_id=body.client_id, secret=secret)


@router.delete("/{client_id}")
async def delete_service_account(
    realm: str,
    client_id: str,
    claims: dict = Depends(require_admin),
):
    """Delete a service account client from the realm. Admin only."""
    validate_realm_name(realm)

    if not _CLIENT_ID_PATTERN.match(client_id):
        raise HTTPException(status_code=400, detail="Invalid client_id format")

    headers = await _admin_headers()
    encoded_realm = urllib.parse.quote(realm, safe="")

    async with httpx.AsyncClient() as http:
        internal_id = await _resolve_client_internal_id(
            http, realm, client_id, headers
        )

        resp = await http.delete(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients/{internal_id}",
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Client not found")
        resp.raise_for_status()

    audit_log(
        "service_account_delete", claims, realm=realm,
        details=f"client_id={client_id}",
    )
    return {"status": "deleted", "realm": realm, "client_id": client_id}


@router.post("/{client_id}/rotate", response_model=ServiceAccountSecret)
async def rotate_client_secret(
    realm: str,
    client_id: str,
    claims: dict = Depends(require_admin),
):
    """Rotate the client secret for a service account. Admin only."""
    validate_realm_name(realm)

    if not _CLIENT_ID_PATTERN.match(client_id):
        raise HTTPException(status_code=400, detail="Invalid client_id format")

    headers = await _admin_headers()
    encoded_realm = urllib.parse.quote(realm, safe="")

    async with httpx.AsyncClient() as http:
        internal_id = await _resolve_client_internal_id(
            http, realm, client_id, headers
        )

        # POST to regenerate the secret
        resp = await http.post(
            f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}/clients/{internal_id}/client-secret",
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        new_secret = resp.json().get("value", "")

    audit_log(
        "service_account_rotate_secret", claims, realm=realm,
        details=f"client_id={client_id}",
    )

    return ServiceAccountSecret(client_id=client_id, secret=new_secret)
