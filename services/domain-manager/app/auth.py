"""
Shared authentication and validation utilities for the domain-manager service.
Extracted to avoid circular imports between main.py and route modules.
"""
import base64
import json
import logging
import os
import re
import time
from typing import List, Optional

import httpx
from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KEYCLOAK_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "master")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "")

ADMIN_ROLES = [
    r.strip()
    for r in os.getenv("ADMIN_ROLES", "admin,keycluster-admin,realm-admin").split(",")
    if r.strip()
]

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_REALM_PATTERN = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9])?$")
_DOMAIN_PATTERN = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
)
_HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


def validate_realm_name(value: str) -> str:
    if not _REALM_PATTERN.match(value):
        raise ValueError(
            "Realm name must be 1-63 alphanumeric characters, hyphens, or underscores"
        )
    if value.lower() == "master":
        raise ValueError("Cannot target the master realm")
    return value


def validate_domain(value: str) -> str:
    if not _DOMAIN_PATTERN.match(value):
        raise ValueError("Invalid domain format")
    return value


def validate_hex_color(value: str, field_name: str) -> str:
    if not _HEX_COLOR_PATTERN.match(value):
        raise ValueError(f"{field_name} must be a valid hex color (e.g. #ff0000)")
    return value


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------
audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)


def audit_log(action: str, claims: dict, realm: str = "", details: str = "") -> None:
    user = claims.get("preferred_username", claims.get("sub", "unknown"))
    msg = f"action={action} user={user}"
    if realm:
        msg += f" realm={realm}"
    if details:
        msg += f" details={details}"
    audit_logger.info(msg)


# ---------------------------------------------------------------------------
# JWKS cache and JWT verification
# ---------------------------------------------------------------------------
_jwks_cache: Optional[dict] = None
_jwks_uri_cache: Optional[str] = None


async def _fetch_keycloak_jwks_uri() -> str:
    url = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
    async with httpx.AsyncClient() as http:
        resp = await http.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()["jwks_uri"]


async def _fetch_jwks(jwks_uri: str) -> dict:
    async with httpx.AsyncClient() as http:
        resp = await http.get(jwks_uri, timeout=5.0)
        resp.raise_for_status()
        return resp.json()


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_uri_cache
    if _jwks_cache is None:
        _jwks_uri_cache = await _fetch_keycloak_jwks_uri()
        _jwks_cache = await _fetch_jwks(_jwks_uri_cache)
    return _jwks_cache


async def _refresh_jwks() -> dict:
    global _jwks_cache, _jwks_uri_cache
    if _jwks_uri_cache is None:
        _jwks_uri_cache = await _fetch_keycloak_jwks_uri()
    _jwks_cache = await _fetch_jwks(_jwks_uri_cache)
    return _jwks_cache


def _decode_jwt_unverified_header(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    header_b64 = parts[0] + "=" * (4 - len(parts[0]) % 4)
    return json.loads(base64.urlsafe_b64decode(header_b64))


def _verify_jwt_signature(token: str, jwks: dict) -> dict:
    from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    from cryptography.hazmat.backends import default_backend

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")

    header = _decode_jwt_unverified_header(token)
    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    if alg != "RS256":
        raise ValueError(f"Unsupported algorithm: {alg}")

    matching_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid and key.get("kty") == "RSA":
            matching_key = key
            break

    if matching_key is None:
        raise ValueError(f"No matching key found for kid={kid}")

    def _b64_to_int(b64_str: str) -> int:
        padded = b64_str + "=" * (4 - len(b64_str) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return int.from_bytes(decoded, byteorder="big")

    n = _b64_to_int(matching_key["n"])
    e = _b64_to_int(matching_key["e"])
    public_key = RSAPublicNumbers(e, n).public_key(default_backend())

    message = (parts[0] + "." + parts[1]).encode("utf-8")
    signature_b64 = parts[2] + "=" * (4 - len(parts[2]) % 4)
    signature = base64.urlsafe_b64decode(signature_b64)

    public_key.verify(signature, message, PKCS1v15(), SHA256())

    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


async def verify_token(request: Request) -> dict:
    """FastAPI dependency: verify Bearer token, return JWT claims."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]

    try:
        jwks = await _get_jwks()
        payload = _verify_jwt_signature(token, jwks)
    except Exception:
        try:
            jwks = await _refresh_jwks()
            payload = _verify_jwt_signature(token, jwks)
        except Exception as exc:
            logger.warning("JWT verification failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid token") from exc

    exp = payload.get("exp")
    if exp is not None and time.time() > exp:
        raise HTTPException(status_code=401, detail="Token expired")

    expected_issuer = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}"
    if payload.get("iss") != expected_issuer:
        raise HTTPException(status_code=401, detail="Invalid token issuer")

    # Validate aud and azp claims when KEYCLOAK_CLIENT_ID is configured (opt-in)
    if KEYCLOAK_CLIENT_ID:
        aud = payload.get("aud")
        if aud is None:
            raise HTTPException(status_code=401, detail="Token missing aud claim")
        aud_list: List[str] = [aud] if isinstance(aud, str) else list(aud)
        if KEYCLOAK_CLIENT_ID not in aud_list:
            raise HTTPException(status_code=401, detail="Token audience mismatch")

        azp = payload.get("azp")
        if azp is not None and azp != KEYCLOAK_CLIENT_ID:
            raise HTTPException(status_code=401, detail="Token authorized party mismatch")

    return payload


def require_admin(claims: dict = Depends(verify_token)) -> dict:
    """FastAPI dependency: require caller to have an admin role."""
    realm_access = claims.get("realm_access", {})
    user_roles: list = realm_access.get("roles", [])

    if not any(role in ADMIN_ROLES for role in user_roles):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return claims


# ---------------------------------------------------------------------------
# Keycloak admin token
# ---------------------------------------------------------------------------
async def get_keycloak_admin_token() -> Optional[str]:
    username = os.getenv("KEYCLOAK_ADMIN")
    password = os.getenv("KEYCLOAK_ADMIN_PASSWORD")
    if not username or not password:
        logger.error("KEYCLOAK_ADMIN credentials not set")
        return None

    async with httpx.AsyncClient() as http:
        try:
            resp = await http.post(
                f"{KEYCLOAK_SERVER_URL}/realms/master/protocol/openid-connect/token",
                data={
                    "client_id": "admin-cli",
                    "username": username,
                    "password": password,
                    "grant_type": "password",
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
        except Exception as e:
            logger.error("Failed to authenticate with Keycloak: %s", e)
            return None
