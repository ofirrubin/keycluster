import asyncio
import logging
import os
import re
import httpx
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.domain import DomainMapping as DomainMappingModel, DomainMappingResponse
from app.models.theme import RealmTheme
from app.auth import (
    KEYCLOAK_SERVER_URL,
    require_admin,
    verify_token,
    validate_realm_name as _validate_realm_name,
    validate_domain as _validate_domain,
    validate_hex_color as _validate_hex_color,
    audit_log as _audit_log_raw,
    get_keycloak_admin_token as get_keycloak_token,
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
# Configuration
# ---------------------------------------------------------------------------
NAMESPACE = "keycloak"
INGRESS_CLASS = "nginx"


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
        if v not in ("light", "dark"):
            raise ValueError("themeMode must be 'light' or 'dark'")
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
            if "expression(" in lower:
                raise ValueError("customCss must not contain expression()")
            if re.search(r"url\s*\(\s*['\"]?\s*data:", lower):
                raise ValueError("customCss must not contain data: URIs")
            if "javascript:" in lower:
                raise ValueError("customCss must not contain javascript: URIs")
        return v

    @field_validator("fontFamily")
    @classmethod
    def validate_font_family(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("fontFamily must be under 200 characters")
        if any(c in v for c in ("<", ">", "{", "}")):
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
            if "expression(" in lower:
                raise ValueError("backgroundCss must not contain expression()")
            if re.search(r"url\s*\(\s*['\"]?\s*data:", lower):
                raise ValueError("backgroundCss must not contain data: URIs")
            if "javascript:" in lower:
                raise ValueError("backgroundCss must not contain javascript: URIs")
        return v

    @field_validator("backgroundUrl", "logoUrl")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.startswith("https://"):
                raise ValueError("URLs must use HTTPS")
            if len(v) > 2048:
                raise ValueError("URL must be under 2048 characters")
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
        return v

    @field_validator("csp_allowed_origins")
    @classmethod
    def validate_origins(cls, v: List[str]) -> List[str]:
        validated: List[str] = []
        for origin in v:
            origin = origin.strip()
            if not origin.startswith(("http://", "https://")):
                raise ValueError("Origins must start with http:// or https://")
            if any(c in origin for c in (";", "'", '"')):
                raise ValueError("Origins cannot contain semicolons or quotes")
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
        return v

    @field_validator("csp_allowed_origins")
    @classmethod
    def validate_origins(cls, v: List[str]) -> List[str]:
        validated: List[str] = []
        for origin in v:
            origin = origin.strip()
            if not origin.startswith(("http://", "https://")):
                raise ValueError("Origins must start with http:// or https://")
            if any(c in origin for c in (";", "'", '"')):
                raise ValueError("Origins cannot contain semicolons or quotes")
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
        realms = [m.realm for m in v]
        if len(realms) != len(set(realms)):
            raise ValueError("Duplicate realms are not allowed in a bulk request")
        return v


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Keycluster Domain Manager",
    description=(
        "Management API for multi-tenant Keycloak on Kubernetes. "
        "Handles domain-to-realm mapping, Ingress lifecycle, security header "
        "configuration, dynamic theme serving, role templates, and service accounts."
    ),
    version="1.0.0",
)

_cors_origin = os.getenv("CORS_ALLOWED_ORIGIN", "")
_cors_origins = [o.strip() for o in _cors_origin.split(",") if o.strip()] if _cors_origin else []

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

    async with httpx.AsyncClient() as http:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await http.put(
                f"{KEYCLOAK_SERVER_URL}/admin/realms/{realm}",
                json=payload,
                headers=headers,
                timeout=5.0,
            )
            resp.raise_for_status()
            logger.info("Successfully patched security headers for realm '%s'", realm)
        except Exception as e:
            logger.error("Failed to patch realm headers for '%s': %s", realm, e)


# ---------------------------------------------------------------------------
# Kubernetes helpers
# ---------------------------------------------------------------------------
async def get_kubernetes_client():
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
    }

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
async def sync_domain(
    mapping: DomainMappingSchema,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Create or update a domain mapping for a realm."""
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

    await session.commit()
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

        # 3. Patch Realm Headers
        asyncio.create_task(
            patch_realm_security_headers(
                mapping.realm,
                mapping.csp_allowed_origins,
                mapping.ssl_required,
                mapping.hsts_max_age,
            )
        )

    except ApiException as e:
        logger.error("Kubernetes API Error: %s - %s", e.status, e.body)
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


async def delete_domain_ingress(realm: str, api) -> dict:
    ingress_name = f"keycloak-realm-{realm}"
    try:
        await api.delete_namespaced_ingress(ingress_name, NAMESPACE)
        logger.info("Deleted ingress for realm %s", realm)
    except ApiException as e:
        if e.status != 404:
            logger.error("Failed to delete ingress for realm %s: %s", realm, e)
            raise HTTPException(
                status_code=502,
                detail="Failed to delete domain ingress",
            )
    return {"status": "deleted", "realm": realm}


@app.delete("/domains/{realm}")
async def delete_domain(
    realm: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Delete a domain mapping for a realm."""
    _validate_realm_name(realm)

    statement = select(DomainMappingModel).where(DomainMappingModel.realm == realm)
    results = await session.execute(statement)
    db_mapping = results.scalar_one_or_none()

    if db_mapping:
        await session.delete(db_mapping)
        await session.commit()

    api = await get_kubernetes_client()
    _audit_log("domain_delete", realm, claims)
    return await delete_domain_ingress(realm, api)


@app.get("/v1/domains/{realm}/health")
async def get_domain_health(
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
            status_code=404, detail=f"No domain mapping found for realm '{realm}'"
        )

    domain = db_mapping.domain
    tls_enabled = db_mapping.tls_enabled
    resolves = False
    cert_valid = False
    keycloak_responding = False

    scheme = "https" if tls_enabled else "http"
    check_url = f"{scheme}://{domain}/realms/master"

    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as http:
            resp = await http.get(check_url)
            resolves = True
            keycloak_responding = resp.status_code < 500
            if tls_enabled:
                cert_valid = True
    except httpx.ConnectError:
        resolves = False
    except httpx.TimeoutException:
        resolves = True
    except Exception as e:
        logger.warning("Domain health check error for realm '%s': %s", realm, e)
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
async def bulk_create_domains(
    body: BulkDomainRequest,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
) -> dict:
    """Create or update multiple domain mappings in a single transaction. Admin only."""
    created: List[DomainMappingResponse] = []
    errors: List[dict] = []

    for item in body.mappings:
        try:
            statement = select(DomainMappingModel).where(
                DomainMappingModel.realm == item.realm
            )
            results = await session.execute(statement)
            db_mapping = results.scalar_one_or_none()

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

        except Exception as e:
            logger.error("Bulk domain error for realm '%s': %s", item.realm, e)
            errors.append({"realm": item.realm, "error": str(e)})

    if errors and not created:
        await session.rollback()
        raise HTTPException(
            status_code=422,
            detail={"message": "All mappings failed validation", "errors": errors},
        )

    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error("Bulk domain commit failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Database commit failed",
        ) from e

    for item in body.mappings:
        if any(err["realm"] == item.realm for err in errors):
            continue
        result = await session.execute(
            select(DomainMappingModel).where(DomainMappingModel.realm == item.realm)
        )
        db_mapping = result.scalar_one_or_none()
        if db_mapping is not None:
            await session.refresh(db_mapping)
            created.append(DomainMappingResponse.model_validate(db_mapping))

    _audit_log(
        "domain_bulk_create",
        "*",
        claims,
        f"created={len(created)} errors={len(errors)}",
    )

    return {
        "created": [r.model_dump() for r in created],
        "errors": errors,
    }


@app.post("/cleanup")
async def cleanup_orphans(
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


async def run_cleanup(valid_realms: List[str]) -> None:
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
                        "Failed to delete orphan %s: %s", ing.metadata.name, e
                    )

    except ApiException as e:
        logger.error("Failed to list ingresses for cleanup: %s", e)

    await cleanup_keycloak_realms(valid_realms)


async def cleanup_keycloak_realms(valid_realms: List[str]) -> None:
    token = await get_keycloak_token()
    if not token:
        return

    async with httpx.AsyncClient() as http:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await http.get(
                f"{KEYCLOAK_SERVER_URL}/admin/realms",
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            realms = resp.json()

            for r in realms:
                rid = r["realm"]
                if rid == "master":
                    continue
                if rid not in valid_realms:
                    logger.warning(
                        "Found orphan Keycloak realm '%s'. Deleting...", rid
                    )
                    try:
                        del_resp = await http.delete(
                            f"{KEYCLOAK_SERVER_URL}/admin/realms/{rid}",
                            headers=headers,
                            timeout=10.0,
                        )
                        del_resp.raise_for_status()
                        logger.info("Deleted orphan realm: %s", rid)
                    except Exception as e:
                        logger.error("Failed to delete realm %s: %s", rid, e)

        except Exception as e:
            logger.error("Failed to list/clean realms: %s", e)


# ---------------------------------------------------------------------------
# Theme routes
# ---------------------------------------------------------------------------
@app.get("/v1/themes/{realm}", response_model=ThemeConfig)
async def get_theme(
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
async def save_theme(
    realm: str,
    theme_config: ThemeConfig,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Save or update theme configuration for a realm."""
    _validate_realm_name(realm)

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
async def delete_theme(
    realm: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
):
    """Delete persisted theme for a realm, resetting to defaults."""
    _validate_realm_name(realm)

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
async def health():
    """Return service health status."""
    return {"status": "ok"}
