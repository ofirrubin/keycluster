"""
Shared authentication and validation utilities for the domain-manager service.
Extracted to avoid circular imports between main.py and route modules.
"""
import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
import urllib.parse
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

if not KEYCLOAK_CLIENT_ID:
    logger.critical("KEYCLOAK_CLIENT_ID is required for JWT audience validation")
    sys.exit(1)

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
# JWKS cache, rate limiter, and JWT verification
# ---------------------------------------------------------------------------
_jwks_cache: Optional[dict] = None
_jwks_uri_cache: Optional[str] = None
_jwks_cache_time: float = 0.0
_JWKS_CACHE_TTL: float = 300.0  # 5 minutes
_jwks_lock: asyncio.Lock = asyncio.Lock()

# Rate limiter: track JWKS fetch timestamps (max 10 per 60 seconds)
_JWKS_MAX_FETCHES_PER_MIN: int = 10
_jwks_fetch_timestamps: list[float] = []


def _check_jwks_rate_limit() -> None:
    """Enforce max 10 JWKS fetches per 60 seconds."""
    now = time.time()
    cutoff = now - 60.0
    # Prune old entries
    while _jwks_fetch_timestamps and _jwks_fetch_timestamps[0] < cutoff:
        _jwks_fetch_timestamps.pop(0)
    if len(_jwks_fetch_timestamps) >= _JWKS_MAX_FETCHES_PER_MIN:
        raise ValueError("JWKS fetch rate limit exceeded")
    _jwks_fetch_timestamps.append(now)


async def _fetch_keycloak_jwks_uri() -> str:
    encoded_realm = urllib.parse.quote(KEYCLOAK_REALM, safe="")
    url = f"{KEYCLOAK_SERVER_URL}/realms/{encoded_realm}/.well-known/openid-configuration"
    async with httpx.AsyncClient() as http:
        resp = await http.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()["jwks_uri"]


async def _fetch_jwks(jwks_uri: str) -> dict:
    _check_jwks_rate_limit()
    async with httpx.AsyncClient() as http:
        resp = await http.get(jwks_uri, timeout=5.0)
        resp.raise_for_status()
        return resp.json()


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_uri_cache, _jwks_cache_time
    # Fast path: return cached JWKS without acquiring the lock
    if _jwks_cache is not None and (time.time() - _jwks_cache_time) <= _JWKS_CACHE_TTL:
        return _jwks_cache
    # Slow path: acquire lock so only one coroutine fetches at a time
    async with _jwks_lock:
        # Double-check after acquiring the lock (another coroutine may have refreshed)
        if _jwks_cache is not None and (time.time() - _jwks_cache_time) <= _JWKS_CACHE_TTL:
            return _jwks_cache
        _jwks_uri_cache = await _fetch_keycloak_jwks_uri()
        _jwks_cache = await _fetch_jwks(_jwks_uri_cache)
        _jwks_cache_time = time.time()
    return _jwks_cache


async def _refresh_jwks() -> dict:
    global _jwks_cache, _jwks_uri_cache
    async with _jwks_lock:
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
            logger.warning("JWT verification failed: exc_type=%s", type(exc).__name__)
            raise HTTPException(status_code=401, detail="Invalid token") from exc

    now = time.time()

    exp = payload.get("exp")
    if exp is None:
        raise HTTPException(status_code=401, detail="Token missing exp claim")
    if now > exp:
        raise HTTPException(status_code=401, detail="Token expired")

    nbf = payload.get("nbf")
    if nbf is not None and now < nbf:
        raise HTTPException(status_code=401, detail="Token not yet valid")

    iat = payload.get("iat")
    if iat is not None and iat > now + 60:
        raise HTTPException(status_code=401, detail="Token issued in the future")

    expected_issuer = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}"
    if payload.get("iss") != expected_issuer:
        raise HTTPException(status_code=401, detail="Invalid token issuer")

    # Validate aud and azp claims (KEYCLOAK_CLIENT_ID is required at startup)
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
_ADMIN_CLIENT_ID: str = os.getenv("KEYCLOAK_ADMIN_CLIENT_ID", "")
_ADMIN_CLIENT_SECRET: str = os.getenv("KEYCLOAK_ADMIN_CLIENT_SECRET", "")
_USE_CLIENT_CREDENTIALS: bool = bool(_ADMIN_CLIENT_ID and _ADMIN_CLIENT_SECRET)

# KEYCLOAK_AUTH_MODE controls which grant types are attempted:
#   "auto"               - try client_credentials first, fall back to password (dev only)
#   "client_credentials" - only client_credentials, never fall back to password
# In production, password grant fallback is never allowed regardless of this setting.
_KEYCLOAK_AUTH_MODE: str = os.getenv("KEYCLOAK_AUTH_MODE", "auto").lower()
_IS_PRODUCTION: bool = os.getenv("ENVIRONMENT", "production").lower() != "dev"

if _KEYCLOAK_AUTH_MODE not in ("auto", "client_credentials"):
    logger.critical("KEYCLOAK_AUTH_MODE must be 'auto' or 'client_credentials', got '%s'", _KEYCLOAK_AUTH_MODE)
    sys.exit(1)

# In production with auth_mode=auto, password fallback is still blocked.
# Only in non-production + auto mode is the fallback allowed.
_ALLOW_PASSWORD_FALLBACK: bool = (
    _KEYCLOAK_AUTH_MODE == "auto" and not _IS_PRODUCTION
)

if not _USE_CLIENT_CREDENTIALS:
    if _IS_PRODUCTION:
        logger.critical(
            "KEYCLOAK_ADMIN_CLIENT_ID and KEYCLOAK_ADMIN_CLIENT_SECRET are required in production. "
            "Password grant fallback is not allowed."
        )
        sys.exit(1)
    else:
        logger.warning(
            "Using password grant for admin API. This is only acceptable in development. "
            "Set KEYCLOAK_ADMIN_CLIENT_ID and KEYCLOAK_ADMIN_CLIENT_SECRET for production."
        )


async def _get_token_via_client_credentials() -> Optional[str]:
    """Obtain an admin token using client_credentials grant (scoped service account)."""
    async with httpx.AsyncClient() as http:
        try:
            resp = await http.post(
                f"{KEYCLOAK_SERVER_URL}/realms/master/protocol/openid-connect/token",
                data={
                    "client_id": _ADMIN_CLIENT_ID,
                    "client_secret": _ADMIN_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
        except Exception as e:
            logger.error(
                "Failed to authenticate with Keycloak (client_credentials): exc_type=%s",
                type(e).__name__,
            )
            return None


async def _get_token_via_password() -> Optional[str]:
    """Obtain an admin token using password grant (legacy, master realm super-admin).

    Only allowed in development environments with KEYCLOAK_AUTH_MODE=auto.
    """
    username: str = os.getenv("KEYCLOAK_ADMIN", "")
    password: str = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "")
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
            logger.error(
                "Failed to authenticate with Keycloak (password): exc_type=%s",
                type(e).__name__,
            )
            return None


async def get_keycloak_admin_token() -> Optional[str]:
    """Obtain a Keycloak admin API token.

    Prefers client_credentials grant when KEYCLOAK_ADMIN_CLIENT_ID and
    KEYCLOAK_ADMIN_CLIENT_SECRET are set (scoped service account, lower blast
    radius). Falls back to password grant ONLY in development environments
    with KEYCLOAK_AUTH_MODE=auto.
    """
    if _USE_CLIENT_CREDENTIALS:
        token = await _get_token_via_client_credentials()
        if token is not None:
            return token
        # client_credentials failed; only fall back if explicitly allowed
        if not _ALLOW_PASSWORD_FALLBACK:
            logger.error("client_credentials grant failed and password fallback is disabled")
            return None
        logger.warning("client_credentials grant failed, falling back to password grant (dev only)")

    if not _ALLOW_PASSWORD_FALLBACK:
        logger.error("Password grant fallback is disabled (production or KEYCLOAK_AUTH_MODE=client_credentials)")
        return None

    return await _get_token_via_password()


# ---------------------------------------------------------------------------
# Token introspection (defense-in-depth for destructive operations)
# ---------------------------------------------------------------------------
async def introspect_token(token: str) -> bool:
    """Introspect a token against Keycloak to verify it has not been revoked.

    Used as defense-in-depth on destructive (DELETE) endpoints. Within the JWKS
    cache TTL window, a revoked token would still pass local JWT validation.
    Introspection catches this by checking the token's active status server-side.

    Returns True if the token is active, False otherwise.
    """
    # Introspection requires client credentials to authenticate the request
    if not _ADMIN_CLIENT_ID or not _ADMIN_CLIENT_SECRET:
        # Cannot introspect without client credentials; log and allow
        # (the JWT was already validated locally)
        logger.warning(
            "Token introspection skipped: KEYCLOAK_ADMIN_CLIENT_ID/SECRET not configured"
        )
        return True

    encoded_realm = urllib.parse.quote(KEYCLOAK_REALM, safe="")
    introspect_url = (
        f"{KEYCLOAK_SERVER_URL}/realms/{encoded_realm}/protocol/openid-connect/token/introspect"
    )

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                introspect_url,
                data={
                    "token": token,
                    "client_id": _ADMIN_CLIENT_ID,
                    "client_secret": _ADMIN_CLIENT_SECRET,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("active", False)
    except Exception as e:
        logger.error(
            "Token introspection failed: exc_type=%s", type(e).__name__
        )
        # Fail open: if introspection is unavailable, rely on local JWT validation.
        # This is a defense-in-depth measure, not the primary auth gate.
        return True
