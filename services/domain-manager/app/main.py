import asyncio
import ipaddress
import logging
import os
import re
import socket
import sys
import urllib.parse
import httpx
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException
from sqlmodel import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.domain import DomainMapping as DomainMappingModel, DomainMappingResponse
from app.models.theme import RealmTheme
from app.auth import (
    KEYCLOAK_SERVER_URL,
    require_admin,
    validate_realm_name as _validate_realm_name,
    validate_domain as _validate_domain,
    validate_hex_color as _validate_hex_color,
    audit_log as _audit_log_raw,
    get_keycloak_admin_token as get_keycloak_token,
    introspect_token,
    verify_token,
)
from app.routes.role_templates import router as role_templates_router
from app.routes.service_accounts import router as service_accounts_router
from app.routes.cluster import router as cluster_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (environment-first, safe defaults)
# ---------------------------------------------------------------------------
NAMESPACE: str = os.environ.get("K8S_NAMESPACE", "keycloak")
INGRESS_CLASS: str = os.environ.get("INGRESS_CLASS", "nginx")
_HSTS_MAX_AGE_CAP: int = int(os.environ.get("HSTS_MAX_AGE_CAP", "63072000"))  # 2 years default
_KEYCLOAK_TIMEOUT: float = float(os.environ.get("KEYCLOAK_TIMEOUT", "5.0"))
_HEALTH_CHECK_TIMEOUT: float = float(os.environ.get("HEALTH_CHECK_TIMEOUT", "3.0"))


# ---------------------------------------------------------------------------
# DNS rebinding protection
# ---------------------------------------------------------------------------
_BLOCKED_DOMAIN_SUFFIXES = (".svc.cluster.local", ".local")


async def _resolve_and_validate(hostname: str) -> List[str]:
    """Resolve a hostname and validate that all IPs are public.

    Returns a list of validated public IP strings.
    Raises ValueError if the hostname is internal, unresolvable, or resolves
    to any private/reserved IP.

    Uses async DNS resolution to avoid blocking the event loop.
    """
    lower = hostname.lower()

    # Block well-known internal hostnames
    if lower in ("localhost",):
        raise ValueError("Hostname is a blocked internal name")
    for suffix in _BLOCKED_DOMAIN_SUFFIXES:
        if lower.endswith(suffix):
            raise ValueError("Hostname matches a blocked internal suffix")

    # Resolve the hostname asynchronously and check all resulting IPs
    loop = asyncio.get_running_loop()
    try:
        addrinfos = await loop.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Hostname cannot be resolved")

    validated_ips: List[str] = []
    for family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError("Resolved address is not a valid IP")

        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise ValueError("Hostname resolves to a private or internal address")

        validated_ips.append(ip_str)

    if not validated_ips:
        raise ValueError("Hostname resolved to no usable addresses")

    return validated_ips


async def _is_private_or_internal(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP or matches
    blocked DNS patterns. Prevents DNS rebinding attacks where a domain
    could resolve to internal services."""
    try:
        await _resolve_and_validate(hostname)
        return False
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# Audit log adapter (keeps existing call-sites working)
# ---------------------------------------------------------------------------
def _audit_log(action: str, realm: str, claims: dict, details: str = "") -> None:
    _audit_log_raw(action, claims, realm=realm, details=details)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class ThemeConfig(BaseModel):
    # Colors & Layout
    primaryColor: str = "#000000"
    secondaryColor: str = "#333333"
    backgroundColor: str = "#ffffff"
    backgroundUrl: str | None = None
    backgroundCss: str | None = None
    cardBg: str | None = None
    borderRadius: int = 4
    fontFamily: str = "Roboto, sans-serif"

    # Modes
    themeMode: str = "light"

    # Brand
    logoUrl: str | None = None
    showRealmName: bool = True

    # Content
    footerText: str | None = "Secured by Keycluster"
    customCss: str | None = ""

    # Text Overrides
    loginTitle: str | None = None
    loginButtonText: str | None = None

    @field_validator("footerText", "loginTitle", "loginButtonText")
    @classmethod
    def validate_text_fields(cls, v: str | None) -> str | None:
        if v is not None:
            if len(v) > 500:
                raise ValueError("Text field must be under 500 characters")
            if "<script" in v.lower() or "javascript:" in v.lower():
                raise ValueError("Text field must not contain script content")
            if "onerror" in v.lower() or "onload" in v.lower():
                raise ValueError("Text field must not contain event handlers")
        return v

    @field_validator("primaryColor", "secondaryColor", "backgroundColor")
    @classmethod
    def validate_colors(cls, v: str) -> str:
        return _validate_hex_color(v, "color")

    @field_validator("cardBg")
    @classmethod
    def validate_card_bg(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_hex_color(v, "cardBg")
        return v

    @field_validator("themeMode")
    @classmethod
    def validate_theme_mode(cls, v: str) -> str:
        if v not in ("light", "dark", "system"):
            raise ValueError("themeMode must be 'light', 'dark', or 'system'")
        return v

    @field_validator("borderRadius")
    @classmethod
    def validate_border_radius(cls, v: int) -> int:
        if v < 0 or v > 50:
            raise ValueError("borderRadius must be between 0 and 50")
        return v

    @field_validator("customCss")
    @classmethod
    def validate_custom_css(cls, v: str | None) -> str | None:
        if v is not None:
            if len(v) > 10_000:
                raise ValueError("customCss must be under 10000 characters")
            lower = v.lower()
            if "</style" in lower:
                raise ValueError("customCss must not contain closing style tags")
            if "@import" in lower:
                raise ValueError("customCss must not contain @import rules")
            if "@font-face" in lower:
                raise ValueError("customCss must not contain @font-face rules")
            if "expression(" in lower:
                raise ValueError("customCss must not contain expression()")
            if re.search(r"url\s*\(", lower):
                raise ValueError("customCss must not contain url() functions")
            if "javascript:" in lower:
                raise ValueError("customCss must not contain javascript: URIs")
            if "behavior:" in lower or "-moz-binding:" in lower:
                raise ValueError("customCss must not contain behavior or binding directives")
            if "<script" in lower or "onerror" in lower or "onload" in lower:
                raise ValueError("CSS fields must not contain HTML/script content")
        return v

    @field_validator("fontFamily")
    @classmethod
    def validate_font_family(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("fontFamily must be under 200 characters")
        if not re.match(r'^[a-zA-Z0-9\s,\-]+$', v):
            raise ValueError("fontFamily contains disallowed characters")
        return v

    @field_validator("backgroundCss")
    @classmethod
    def validate_background_css(cls, v: str | None) -> str | None:
        if v is not None:
            if len(v) > 2000:
                raise ValueError("backgroundCss must be under 2000 characters")
            lower = v.lower()
            if "</style" in lower:
                raise ValueError("backgroundCss must not contain closing style tags")
            if "@import" in lower:
                raise ValueError("backgroundCss must not contain @import rules")
            if "@font-face" in lower:
                raise ValueError("backgroundCss must not contain @font-face rules")
            if "expression(" in lower:
                raise ValueError("backgroundCss must not contain expression()")
            if re.search(r"url\s*\(", lower):
                raise ValueError("backgroundCss must not contain url() functions")
            if "javascript:" in lower:
                raise ValueError("backgroundCss must not contain javascript: URIs")
            if "behavior:" in lower or "-moz-binding:" in lower:
                raise ValueError("backgroundCss must not contain behavior or binding directives")
            if "<script" in lower or "onerror" in lower or "onload" in lower:
                raise ValueError("CSS fields must not contain HTML/script content")
        return v

    @field_validator("backgroundUrl", "logoUrl")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.startswith("https://"):
                raise ValueError("URLs must use HTTPS")
            if len(v) > 2048:
                raise ValueError("URL must be under 2048 characters")
            parsed = urllib.parse.urlparse(v)
            if parsed.hostname:
                hn = parsed.hostname.lower()
                if hn in ("localhost",) or hn.endswith((".local", ".internal", ".svc.cluster.local")):
                    raise ValueError("URL hostname must not be internal")
        return v


class DomainMappingSchema(BaseModel):
    realm: str
    domain: str
    enabled: bool = True

    # Security
    csp_allowed_origins: List[str] = []
    ssl_required: str = "external"
    hsts_max_age: int = 31536000

    # TLS
    tls_enabled: bool = False
    tls_secret_name: Optional[str] = None

    @field_validator("realm")
    @classmethod
    def validate_realm(cls, v: str) -> str:
        return _validate_realm_name(v)

    @field_validator("domain")
    @classmethod
    def validate_domain_field(cls, v: str) -> str:
        return _validate_domain(v)

    @field_validator("ssl_required")
    @classmethod
    def validate_ssl_required(cls, v: str) -> str:
        if v not in ("all", "external", "none"):
            raise ValueError("ssl_required must be one of: all, external, none")
        return v

    @field_validator("hsts_max_age")
    @classmethod
    def validate_hsts_max_age(cls, v: int) -> int:
        if v < 0:
            raise ValueError("hsts_max_age must be non-negative")
        if v > _HSTS_MAX_AGE_CAP:
            raise ValueError(
                f"hsts_max_age must not exceed {_HSTS_MAX_AGE_CAP} (2 years)"
            )
        return v

    @field_validator("csp_allowed_origins")
    @classmethod
    def validate_origins(cls, v: List[str]) -> List[str]:
        if v and len(v) > 50:
            raise ValueError("csp_allowed_origins must not contain more than 50 entries")
        validated: List[str] = []
        blocked_csp_keywords = ("unsafe-inline", "unsafe-eval", "unsafe-hashes",
                                "data:", "blob:", "mediastream:", "filesystem:",
                                "wasm-unsafe-eval", "none", "self", "strict-dynamic")
        for origin in v:
            origin = origin.strip()
            if not origin.startswith(("http://", "https://")):
                raise ValueError("Origins must start with http:// or https://")
            if any(c in origin for c in (";", "'", '"', " ")):
                raise ValueError("Origins cannot contain semicolons, quotes, or spaces")
            lower = origin.lower()
            if any(kw in lower for kw in blocked_csp_keywords):
                raise ValueError("Origins cannot contain CSP directive keywords")
            # Reject wildcard subdomains (e.g. *.example.com) -- only explicit
            # fully-qualified origins are acceptable in CSP headers.
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.hostname and "*" in parsed_origin.hostname:
                raise ValueError("Wildcard origins are not allowed; use explicit domains")
            validated.append(origin)
        return validated

    @field_validator("tls_secret_name")
    @classmethod
    def validate_tls_secret_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,252}$", v):
                raise ValueError(
                    "tls_secret_name must be alphanumeric with dots, hyphens, or underscores"
                )
        return v


class BulkDomainMappingItem(BaseModel):
    realm: str
    domain: str
    enabled: bool = True
    csp_allowed_origins: List[str] = []
    ssl_required: str = "external"
    hsts_max_age: int = 31536000
    tls_enabled: bool = False
    tls_secret_name: Optional[str] = None

    @field_validator("realm")
    @classmethod
    def validate_realm(cls, v: str) -> str:
        return _validate_realm_name(v)

    @field_validator("domain")
    @classmethod
    def validate_domain_field(cls, v: str) -> str:
        return _validate_domain(v)

    @field_validator("ssl_required")
    @classmethod
    def validate_ssl_required(cls, v: str) -> str:
        if v not in ("all", "external", "none"):
            raise ValueError("ssl_required must be one of: all, external, none")
        return v

    @field_validator("hsts_max_age")
    @classmethod
    def validate_hsts_max_age(cls, v: int) -> int:
        if v < 0:
            raise ValueError("hsts_max_age must be non-negative")
        if v > _HSTS_MAX_AGE_CAP:
            raise ValueError(
                f"hsts_max_age must not exceed {_HSTS_MAX_AGE_CAP} (2 years)"
            )
        return v

    @field_validator("csp_allowed_origins")
    @classmethod
    def validate_origins(cls, v: List[str]) -> List[str]:
        if v and len(v) > 50:
            raise ValueError("csp_allowed_origins must not contain more than 50 entries")
        validated: List[str] = []
        blocked_csp_keywords = ("unsafe-inline", "unsafe-eval", "unsafe-hashes",
                                "data:", "blob:", "mediastream:", "filesystem:",
                                "wasm-unsafe-eval", "none", "self", "strict-dynamic")
        for origin in v:
            origin = origin.strip()
            if not origin.startswith(("http://", "https://")):
                raise ValueError("Origins must start with http:// or https://")
            if any(c in origin for c in (";", "'", '"', " ")):
                raise ValueError("Origins cannot contain semicolons, quotes, or spaces")
            lower = origin.lower()
            if any(kw in lower for kw in blocked_csp_keywords):
                raise ValueError("Origins cannot contain CSP directive keywords")
            # Reject wildcard subdomains (e.g. *.example.com) -- only explicit
            # fully-qualified origins are acceptable in CSP headers.
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.hostname and "*" in parsed_origin.hostname:
                raise ValueError("Wildcard origins are not allowed; use explicit domains")
            validated.append(origin)
        return validated

    @field_validator("tls_secret_name")
    @classmethod
    def validate_tls_secret_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,252}$", v):
                raise ValueError(
                    "tls_secret_name must be alphanumeric with dots, hyphens, or underscores"
                )
        return v


class BulkDomainRequest(BaseModel):
    mappings: List[BulkDomainMappingItem]

    @field_validator("mappings")
    @classmethod
    def validate_mappings_nonempty(
        cls, v: List[BulkDomainMappingItem]
    ) -> List[BulkDomainMappingItem]:
        if not v:
            raise ValueError("At least one mapping is required")
        if len(v) > 500:
            raise ValueError("Bulk request must not exceed 500 entries")
        realms = [m.realm for m in v]
        if len(realms) != len(set(realms)):
            raise ValueError("Duplicate realms are not allowed in a bulk request")
        domains = [m.domain for m in v]
        if len(domains) != len(set(domains)):
            raise ValueError("Duplicate domains are not allowed")
        return v


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
_is_dev = os.getenv("ENVIRONMENT", "production").lower() == "dev"

app = FastAPI(
    title="Keycluster Domain Manager",
    description=(
        "Management API for multi-tenant Keycloak on Kubernetes. "
        "Handles domain-to-realm mapping, Ingress lifecycle, security header "
        "configuration, dynamic theme serving, role templates, and service accounts."
    ),
    version="1.0.0",
    docs_url="/docs" if _is_dev else None,
    redoc_url="/redoc" if _is_dev else None,
    openapi_url="/openapi.json" if _is_dev else None,
)

# Rate limiting — hard dependency, must not silently degrade
from slowapi.errors import RateLimitExceeded
from app.rate_limit import limiter

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded"}},
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    logger.warning("Validation error: exc_type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "VALIDATION_ERROR", "message": "Invalid request body"}},
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Standardize all HTTPException responses to a consistent structure."""
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    content = {"error": {"code": "REQUEST_ERROR", "message": message}}
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: exc_type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}},
    )

_cors_origin = os.getenv("CORS_ALLOWED_ORIGIN", "")
_cors_origins = [o.strip() for o in _cors_origin.split(",") if o.strip()] if _cors_origin else []

if not _is_dev:
    for _origin in _cors_origins:
        if _origin == "*" or "localhost" in _origin or "127.0.0.1" in _origin:
            logger.critical("Wildcard or localhost CORS origin not allowed in production: %s", _origin)
            sys.exit(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Include routers
app.include_router(role_templates_router)
app.include_router(service_accounts_router)
app.include_router(cluster_router, prefix="/v1/cluster")


# ---------------------------------------------------------------------------
# Keycloak admin helpers
# ---------------------------------------------------------------------------
def _log_task_exception(task: asyncio.Task) -> None:
    """Callback for fire-and-forget tasks to log unhandled exceptions."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task '%s' failed: exc_type=%s",
            task.get_name(), type(exc).__name__,
        )


async def patch_realm_security_headers(
    realm: str,
    allowed_origins: List[str],
    ssl_required: str,
    hsts_max_age: int,
) -> None:
    token = await get_keycloak_token()
    if not token:
        logger.error("Cannot patch realm %s: No admin token", realm)
        return

    sources = ["'self'"] + allowed_origins
    sources_str = " ".join(sources)
    csp_value = f"frame-src {sources_str}; frame-ancestors {sources_str}; object-src 'none';"
    x_frame_value = "SAMEORIGIN" if allowed_origins else "DENY"

    payload = {
        "sslRequired": ssl_required,
        "browserSecurityHeaders": {
            "contentSecurityPolicy": csp_value,
            "xFrameOptions": x_frame_value,
            "strictTransportSecurity": f"max-age={hsts_max_age}",
        },
    }

    encoded_realm = urllib.parse.quote(realm, safe="")
    async with httpx.AsyncClient() as http:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await http.put(
                f"{KEYCLOAK_SERVER_URL}/admin/realms/{encoded_realm}",
                json=payload,
                headers=headers,
                timeout=_KEYCLOAK_TIMEOUT,
            )
            resp.raise_for_status()
            logger.info("Successfully patched security headers for realm '%s'", realm)
        except Exception as e:
            logger.error(
                "Failed to patch realm headers for '%s': exc_type=%s",
                realm, type(e).__name__,
            )


# ---------------------------------------------------------------------------
# Kubernetes helpers
# ---------------------------------------------------------------------------
async def get_kubernetes_client() -> client.NetworkingV1Api:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    return client.NetworkingV1Api()


def generate_ingress_manifest(
    realm: str,
    domain: str,
    theme_name: str = "dynamic-standard",
    tls_enabled: bool = False,
    tls_secret_name: Optional[str] = None,
) -> dict:
    rules = [
        (f"/realms/{realm}", "Prefix"),
        (r"/resources/.*/common/.*", "ImplementationSpecific"),
        (r"/resources/.*/account/.*", "ImplementationSpecific"),
        (r"/resources/.*/admin/.*", "ImplementationSpecific"),
        (r"/resources/.*/welcome/.*", "ImplementationSpecific"),
        ("/js", "Prefix"),
        (f"/resources/.*/login/{theme_name}", "ImplementationSpecific"),
        (f"/resources/.*/email/{theme_name}", "ImplementationSpecific"),
        (f"/resources/.*/account/{theme_name}", "ImplementationSpecific"),
        (f"/v1/themes/{realm}", "Prefix"),
        ("/robots.txt", "Exact"),
        ("/favicon.ico", "Exact"),
    ]

    ingress_paths = []
    for p, pt in rules:
        service_name = "keycloak"
        service_port = 8080

        if p.startswith("/v1/themes"):
            service_name = "domain-manager"
            service_port = 80

        ingress_paths.append(
            {
                "path": p,
                "pathType": pt,
                "backend": {
                    "service": {
                        "name": service_name,
                        "port": {"number": service_port},
                    }
                },
            }
        )

    annotations = {
        "nginx.ingress.kubernetes.io/proxy-buffer-size": "128k",
        "nginx.ingress.kubernetes.io/use-regex": "true",
        "nginx.ingress.kubernetes.io/ssl-redirect": "true" if tls_enabled else "false",
        "nginx.ingress.kubernetes.io/configuration-snippet": (
            'more_set_headers "X-Content-Type-Options: nosniff";\n'
            'more_set_headers "X-Frame-Options: DENY";'
        ),
    }

    if tls_enabled:
        annotations["nginx.ingress.kubernetes.io/configuration-snippet"] += (
            '\nmore_set_headers "Strict-Transport-Security: max-age=31536000; includeSubDomains";'
        )

    spec: dict = {
        "ingressClassName": INGRESS_CLASS,
        "rules": [
            {
                "host": domain,
                "http": {"paths": ingress_paths},
            }
        ],
    }

    if tls_enabled:
        tls_entry: dict = {"hosts": [domain]}
        if tls_secret_name:
            tls_entry["secretName"] = tls_secret_name
        spec["tls"] = [tls_entry]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": f"keycloak-realm-{realm}",
            "namespace": NAMESPACE,
            "annotations": annotations,
            "labels": {
                "app.kubernetes.io/managed-by": "keycluster-domain-manager",
                "keycluster.io/realm": realm,
            },
        },
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Domain routes (admin-only)
# ---------------------------------------------------------------------------
@app.post("/domains")
@limiter.limit("30/minute")
async def sync_domain(
    request: Request,
    mapping: DomainMappingSchema,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Create or update a domain mapping for a realm.

    Domain ownership verification is the responsibility of the orchestration
    layer (e.g., Magma). This endpoint trusts authenticated callers.
    """
    api = await get_kubernetes_client()
    ingress_name = f"keycloak-realm-{mapping.realm}"

    # 1. Update Database
    statement = select(DomainMappingModel).where(
        DomainMappingModel.realm == mapping.realm
    )
    results = await session.execute(statement)
    db_mapping = results.scalar_one_or_none()

    if not mapping.enabled:
        if db_mapping:
            await session.delete(db_mapping)
            await session.commit()
        _audit_log("domain_disable", mapping.realm, claims, f"domain={mapping.domain}")
        return await delete_domain_ingress(mapping.realm, api)

    # SSRF/DNS validation: reject domains that resolve to private/internal addresses
    if await _is_private_or_internal(mapping.domain):
        raise HTTPException(status_code=422, detail="Domain points to a private or internal address")

    csp_csv = ",".join(mapping.csp_allowed_origins)

    if not db_mapping:
        db_mapping = DomainMappingModel(
            realm=mapping.realm,
            domain=mapping.domain,
            enabled=mapping.enabled,
            csp_allowed_origins=csp_csv,
            ssl_required=mapping.ssl_required,
            hsts_max_age=mapping.hsts_max_age,
            tls_enabled=mapping.tls_enabled,
            tls_secret_name=mapping.tls_secret_name,
        )
        session.add(db_mapping)
        _audit_log("domain_create", mapping.realm, claims, f"domain={mapping.domain}")
    else:
        db_mapping.domain = mapping.domain
        db_mapping.enabled = mapping.enabled
        db_mapping.csp_allowed_origins = csp_csv
        db_mapping.ssl_required = mapping.ssl_required
        db_mapping.hsts_max_age = mapping.hsts_max_age
        db_mapping.tls_enabled = mapping.tls_enabled
        db_mapping.tls_secret_name = mapping.tls_secret_name
        db_mapping.updated_at = datetime.now(timezone.utc)
        _audit_log("domain_update", mapping.realm, claims, f"domain={mapping.domain}")

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
        if pgcode == "23505":
            logger.warning("Domain mapping conflict: exc_type=%s", type(e).__name__)
            raise HTTPException(status_code=409, detail="Domain mapping already exists")
        logger.error("Domain mapping integrity error: exc_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Database operation failed")
    await session.refresh(db_mapping)

    # 2. Update Kubernetes
    manifest = generate_ingress_manifest(
        mapping.realm,
        mapping.domain,
        tls_enabled=mapping.tls_enabled,
        tls_secret_name=mapping.tls_secret_name,
    )
    try:
        try:
            await api.read_namespaced_ingress(ingress_name, NAMESPACE)
            await api.replace_namespaced_ingress(ingress_name, NAMESPACE, manifest)
            logger.info("Updated ingress for realm %s", mapping.realm)
        except ApiException as e:
            if e.status == 404:
                await api.create_namespaced_ingress(NAMESPACE, manifest)
                logger.info("Created ingress for realm %s", mapping.realm)
            else:
                raise

        # 3. Patch Realm Headers (fire-and-forget with error logging)
        task = asyncio.create_task(
            patch_realm_security_headers(
                mapping.realm,
                mapping.csp_allowed_origins,
                mapping.ssl_required,
                mapping.hsts_max_age,
            ),
            name=f"patch_security_headers:{mapping.realm}",
        )
        task.add_done_callback(_log_task_exception)

    except ApiException as e:
        logger.error("Kubernetes API Error: status=%s", e.status)
        raise HTTPException(
            status_code=502,
            detail="Failed to sync domain with cluster",
        )

    return {
        "status": "synced",
        "realm": mapping.realm,
        "domain": mapping.domain,
        "db_id": db_mapping.id,
    }


async def delete_domain_ingress(realm: str, api: client.NetworkingV1Api) -> dict:
    ingress_name = f"keycloak-realm-{realm}"
    try:
        await api.delete_namespaced_ingress(ingress_name, NAMESPACE)
        logger.info("Deleted ingress for realm %s", realm)
    except ApiException as e:
        if e.status != 404:
            logger.error(
                "Failed to delete ingress for realm %s: exc_type=%s status=%s",
                realm, type(e).__name__, e.status,
            )
            raise HTTPException(
                status_code=502,
                detail="Failed to delete domain ingress",
            )
    return {"status": "deleted", "realm": realm}


@app.delete("/domains/{realm}")
@limiter.limit("30/minute")
async def delete_domain(
    request: Request,
    realm: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Delete a domain mapping for a realm.

    Deletes the Ingress resource FIRST to prevent subdomain takeover via a
    dangling Ingress, then removes the DB record.
    """
    _validate_realm_name(realm)

    # Defense-in-depth: introspect token for destructive operations to catch
    # revoked tokens within the JWKS cache TTL window.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token_active = await introspect_token(auth_header[7:])
        if not token_active:
            raise HTTPException(status_code=401, detail="Token has been revoked")

    # 1. Delete Ingress FIRST to avoid dangling Ingress (subdomain takeover risk)
    api = await get_kubernetes_client()
    result = await delete_domain_ingress(realm, api)

    # 2. Delete DB record AFTER Ingress is gone
    statement = select(DomainMappingModel).where(DomainMappingModel.realm == realm)
    results = await session.execute(statement)
    db_mapping = results.scalar_one_or_none()

    if db_mapping:
        await session.delete(db_mapping)
        await session.commit()

    _audit_log("domain_delete", realm, claims)
    return result


@app.get("/v1/domains/{realm}/health")
@limiter.limit("30/minute")
async def get_domain_health(
    request: Request,
    realm: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
) -> dict:
    """Check health of the domain mapped to a realm. Admin only."""
    _validate_realm_name(realm)

    statement = select(DomainMappingModel).where(DomainMappingModel.realm == realm)
    results = await session.execute(statement)
    db_mapping = results.scalar_one_or_none()

    if db_mapping is None:
        raise HTTPException(
            status_code=404, detail="Domain mapping not found"
        )

    domain = db_mapping.domain
    tls_enabled = db_mapping.tls_enabled
    resolves = False
    cert_valid = False
    keycloak_responding = False

    # DNS rebinding protection: resolve once, validate all IPs are public,
    # then connect to the validated IP directly to prevent TOCTOU attacks
    # where DNS could resolve to a different (internal) IP between
    # validation and connection.
    try:
        validated_ips = await _resolve_and_validate(domain)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Domain resolves to a private or internal address",
        )

    # Connect directly to the first validated IP, using the Host header
    # for correct virtual-host routing and TLS SNI.
    target_ip = validated_ips[0]
    scheme = "https" if tls_enabled else "http"
    check_url = f"{scheme}://{target_ip}/realms/master"

    try:
        transport = httpx.AsyncHTTPTransport(
            verify=tls_enabled,
        )
        async with httpx.AsyncClient(
            timeout=_HEALTH_CHECK_TIMEOUT,
            follow_redirects=False,
            transport=transport,
        ) as http:
            resp = await http.get(
                check_url,
                headers={"Host": domain},
                extensions={"sni_hostname": domain} if tls_enabled else {},
            )
            resolves = True
            keycloak_responding = resp.status_code < 500
            if tls_enabled:
                cert_valid = True
    except httpx.ConnectError:
        resolves = False
    except httpx.TimeoutException:
        resolves = True
    except Exception as e:
        logger.warning("Domain health check error for realm '%s': exc_type=%s", realm, type(e).__name__)
        error_name = type(e).__name__.lower()
        if "ssl" in error_name or "certificate" in error_name:
            resolves = True
            cert_valid = False

    if not tls_enabled:
        cert_valid = True

    if keycloak_responding and (not tls_enabled or cert_valid):
        status = "healthy"
    elif resolves:
        status = "degraded"
    else:
        status = "down"

    return {
        "domain": domain,
        "resolves": resolves,
        "cert_valid": cert_valid,
        "keycloak_responding": keycloak_responding,
        "status": status,
    }


@app.post("/v1/domains/bulk")
@limiter.limit("5/minute")
async def bulk_create_domains(
    request: Request,
    body: BulkDomainRequest,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
) -> dict:
    """Create or update multiple domain mappings in a single transaction. Admin only.

    The operation is atomic: all items are validated first, and if any item
    fails validation the entire request is rejected. On success, Kubernetes
    Ingress resources are created/updated for each domain (fire-and-forget).
    """
    errors: List[dict] = []

    # ------------------------------------------------------------------
    # Phase 1: Validate all items and look up existing DB records.
    #          No mutations happen here.
    # ------------------------------------------------------------------
    existing_mappings: dict[str, Optional[DomainMappingModel]] = {}
    for item in body.mappings:
        # SSRF/DNS validation: reject domains that resolve to private/internal addresses
        if item.enabled and await _is_private_or_internal(item.domain):
            errors.append({"realm": item.realm, "error": "Domain points to a private or internal address"})
            continue

        try:
            statement = select(DomainMappingModel).where(
                DomainMappingModel.realm == item.realm
            )
            results = await session.execute(statement)
            existing_mappings[item.realm] = results.scalar_one_or_none()
        except Exception as e:
            logger.error(
                "Bulk domain validation error for realm '%s': exc_type=%s",
                item.realm, type(e).__name__,
            )
            errors.append({"realm": item.realm, "error": "Validation failed"})

    if errors:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Validation failed for one or more mappings", "errors": errors}},
        )

    # ------------------------------------------------------------------
    # Phase 2: Apply all DB mutations (create or update).
    # ------------------------------------------------------------------
    for item in body.mappings:
        db_mapping = existing_mappings[item.realm]
        csp_csv = ",".join(item.csp_allowed_origins)

        if db_mapping is None:
            db_mapping = DomainMappingModel(
                realm=item.realm,
                domain=item.domain,
                enabled=item.enabled,
                csp_allowed_origins=csp_csv,
                ssl_required=item.ssl_required,
                hsts_max_age=item.hsts_max_age,
                tls_enabled=item.tls_enabled,
                tls_secret_name=item.tls_secret_name,
            )
            session.add(db_mapping)
        else:
            db_mapping.domain = item.domain
            db_mapping.enabled = item.enabled
            db_mapping.csp_allowed_origins = csp_csv
            db_mapping.ssl_required = item.ssl_required
            db_mapping.hsts_max_age = item.hsts_max_age
            db_mapping.tls_enabled = item.tls_enabled
            db_mapping.tls_secret_name = item.tls_secret_name
            db_mapping.updated_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Phase 3: Single commit for all changes.
    # ------------------------------------------------------------------
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
        if pgcode == "23505":
            logger.warning("Bulk domain commit conflict: exc_type=%s", type(e).__name__)
            raise HTTPException(
                status_code=409,
                detail="Duplicate domain mapping conflict",
            ) from e
        logger.error("Bulk domain commit integrity error: exc_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Database commit failed",
        ) from e
    except Exception as e:
        await session.rollback()
        logger.error("Bulk domain commit failed: exc_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Database commit failed",
        ) from e

    # Build response list from committed records.
    created: List[DomainMappingResponse] = []
    for item in body.mappings:
        result = await session.execute(
            select(DomainMappingModel).where(DomainMappingModel.realm == item.realm)
        )
        db_mapping = result.scalar_one_or_none()
        if db_mapping is not None:
            await session.refresh(db_mapping)
            created.append(DomainMappingResponse.model_validate(db_mapping))

    # ------------------------------------------------------------------
    # Phase 4: Create/update Kubernetes Ingress for each domain
    #          (fire-and-forget background tasks, same pattern as sync_domain).
    # ------------------------------------------------------------------
    for item in body.mappings:
        if item.enabled:
            task = asyncio.create_task(
                _sync_ingress_for_realm(
                    realm=item.realm,
                    domain=item.domain,
                    tls_enabled=item.tls_enabled,
                    tls_secret_name=item.tls_secret_name,
                    csp_allowed_origins=item.csp_allowed_origins,
                    ssl_required=item.ssl_required,
                    hsts_max_age=item.hsts_max_age,
                ),
                name=f"sync_ingress:{item.realm}",
            )
            task.add_done_callback(_log_task_exception)

    _audit_log(
        "domain_bulk_create",
        "*",
        claims,
        f"created={len(created)}",
    )

    return {
        "created": [r.model_dump() for r in created],
        "errors": errors,
    }


async def _sync_ingress_for_realm(
    realm: str,
    domain: str,
    tls_enabled: bool,
    tls_secret_name: Optional[str],
    csp_allowed_origins: List[str],
    ssl_required: str,
    hsts_max_age: int,
) -> None:
    """Create or update a Kubernetes Ingress for a single realm, then patch
    Keycloak security headers.  Intended to be called as a fire-and-forget
    background task from bulk operations."""
    ingress_name = f"keycloak-realm-{realm}"
    manifest = generate_ingress_manifest(
        realm, domain, tls_enabled=tls_enabled, tls_secret_name=tls_secret_name,
    )
    try:
        api = await get_kubernetes_client()
        try:
            await api.read_namespaced_ingress(ingress_name, NAMESPACE)
            await api.replace_namespaced_ingress(ingress_name, NAMESPACE, manifest)
            logger.info("Bulk: updated ingress for realm %s", realm)
        except ApiException as e:
            if e.status == 404:
                await api.create_namespaced_ingress(NAMESPACE, manifest)
                logger.info("Bulk: created ingress for realm %s", realm)
            else:
                raise
    except Exception as e:
        logger.error(
            "Bulk: failed to sync ingress for realm '%s': exc_type=%s",
            realm, type(e).__name__,
        )
        return

    # Patch realm security headers (best-effort)
    await patch_realm_security_headers(
        realm, csp_allowed_origins, ssl_required, hsts_max_age,
    )


@app.post("/cleanup")
@limiter.limit("5/minute")
async def cleanup_orphans(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Trigger a background job to remove orphan ingresses."""
    statement = select(DomainMappingModel.realm)
    results = await session.execute(statement)
    valid_realms = results.scalars().all()

    background_tasks.add_task(run_cleanup, valid_realms)
    _audit_log("cleanup_trigger", "*", claims, f"valid_realms={len(valid_realms)}")
    return {"status": "cleanup_triggered", "valid_realms_count": len(valid_realms)}


async def run_cleanup(valid_realms: list[str]) -> None:
    logger.info("Running orphan cleanup. Valid realms: %s", valid_realms)
    api = await get_kubernetes_client()

    try:
        label_selector = "app.kubernetes.io/managed-by=keycluster-domain-manager"
        ingresses = await api.list_namespaced_ingress(
            NAMESPACE, label_selector=label_selector
        )

        for ing in ingresses.items:
            realm_label = ing.metadata.labels.get("keycluster.io/realm")
            if realm_label and realm_label not in valid_realms:
                logger.warning(
                    "Found orphan ingress for realm %s. Deleting...", realm_label
                )
                try:
                    await api.delete_namespaced_ingress(ing.metadata.name, NAMESPACE)
                    logger.info("Deleted orphan ingress: %s", ing.metadata.name)
                except Exception as e:
                    logger.error(
                        "Failed to delete orphan %s: exc_type=%s",
                        ing.metadata.name, type(e).__name__,
                    )

    except ApiException as e:
        logger.error("Failed to list ingresses for cleanup: exc_type=%s status=%s", type(e).__name__, e.status)


# ---------------------------------------------------------------------------
# Theme routes
# ---------------------------------------------------------------------------
@app.get("/v1/themes/{realm}", response_model=ThemeConfig)
@limiter.limit("120/minute")
async def get_theme(
    request: Request,
    realm: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Serve theme configuration for the realm.
    Returns persisted config if it exists, otherwise defaults.
    This endpoint is public (called by the login page theme-injector.js).
    """
    _validate_realm_name(realm)

    statement = select(RealmTheme).where(RealmTheme.realm == realm)
    results = await session.execute(statement)
    theme = results.scalar_one_or_none()

    if theme is None:
        return ThemeConfig()

    return ThemeConfig(**theme.config)


@app.post("/v1/themes/{realm}", response_model=ThemeConfig)
@limiter.limit("30/minute")
async def save_theme(
    request: Request,
    realm: str,
    theme_config: ThemeConfig,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Save or update theme configuration for a realm."""
    _validate_realm_name(realm)

    # SSRF defense: resolve theme URL hostnames and reject any that point to
    # private/internal IPs. The synchronous Pydantic validator can only check
    # hostname strings; actual DNS resolution must happen async here.
    for url_field in ("logoUrl", "backgroundUrl"):
        url_val = getattr(theme_config, url_field, None)
        if url_val is not None:
            parsed = urllib.parse.urlparse(url_val)
            if parsed.hostname:
                try:
                    await _resolve_and_validate(parsed.hostname)
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{url_field} resolves to a private or internal address",
                    )

    statement = select(RealmTheme).where(RealmTheme.realm == realm)
    results = await session.execute(statement)
    theme = results.scalar_one_or_none()

    config_dict = theme_config.model_dump()

    if theme is None:
        theme = RealmTheme(
            realm=realm,
            config=config_dict,
        )
        session.add(theme)
        _audit_log("theme_create", realm, claims)
    else:
        theme.config = config_dict
        theme.updated_at = datetime.now(timezone.utc)
        _audit_log("theme_update", realm, claims)

    await session.commit()
    await session.refresh(theme)

    return ThemeConfig(**theme.config)


@app.delete("/v1/themes/{realm}")
@limiter.limit("30/minute")
async def delete_theme(
    request: Request,
    realm: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Delete persisted theme for a realm, resetting to defaults."""
    _validate_realm_name(realm)

    # Defense-in-depth: introspect token for destructive operations
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token_active = await introspect_token(auth_header[7:])
        if not token_active:
            raise HTTPException(status_code=401, detail="Token has been revoked")

    statement = select(RealmTheme).where(RealmTheme.realm == realm)
    results = await session.execute(statement)
    theme = results.scalar_one_or_none()

    if theme is None:
        raise HTTPException(
            status_code=404, detail="No custom theme found for this realm"
        )

    await session.delete(theme)
    await session.commit()
    _audit_log("theme_delete", realm, claims)

    return {"status": "reset", "realm": realm}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
@limiter.limit("120/minute")
async def health(request: Request):
    """Return service health status."""
    return {"status": "ok"}
