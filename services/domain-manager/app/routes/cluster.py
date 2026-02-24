import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.auth import KEYCLOAK_SERVER_URL, require_admin
from app.database import get_session
from app.models.domain import DomainMapping

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cluster"])


async def _check_keycloak() -> dict:
    """Check Keycloak reachability and attempt to read version."""
    url = f"{KEYCLOAK_SERVER_URL}/realms/master"
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.get(url)
            version: Optional[str] = None
            try:
                health_resp = await http.get(
                    f"{KEYCLOAK_SERVER_URL}/health/ready", timeout=3.0
                )
                if health_resp.status_code == 200:
                    health_data = health_resp.json()
                    version = health_data.get("keycloak_version")
            except Exception:
                pass
            return {"reachable": True, "version": version}
    except httpx.TimeoutException:
        logger.warning("Keycloak health check timed out at %s", url)
        return {"reachable": False, "version": None}
    except Exception as e:
        logger.warning("Keycloak health check failed: exc_type=%s", type(e).__name__)
        return {"reachable": False, "version": None}


async def _check_database(session: AsyncSession) -> dict:
    """Check DB connectivity with a simple SELECT 1."""
    try:
        await session.execute(text("SELECT 1"))
        return {"connected": True}
    except Exception as e:
        logger.error("Database health check failed: exc_type=%s", type(e).__name__)
        return {"connected": False}


async def _count_domains(session: AsyncSession) -> int:
    try:
        result = await session.execute(select(func.count()).select_from(DomainMapping))
        return result.scalar_one()
    except Exception as e:
        logger.error("Failed to count domain mappings: exc_type=%s", type(e).__name__)
        return -1


@router.get("/health")
async def cluster_health(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Return cluster-level health aggregation.
    Public endpoint — no auth required.
    """
    keycloak_status = await _check_keycloak()
    db_status = await _check_database(session)
    domain_count = await _count_domains(session)

    if keycloak_status["reachable"] and db_status["connected"]:
        status = "healthy"
    elif db_status["connected"] or keycloak_status["reachable"]:
        status = "degraded"
    else:
        status = "down"

    return {
        "status": status,
        "keycloak": {"reachable": keycloak_status["reachable"]},
        "database": {"connected": db_status["connected"]},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics")
async def cluster_metrics(
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(require_admin),
) -> dict:
    """Return basic cluster metrics. Admin only."""
    domain_count = await _count_domains(session)

    return {
        "realm_count": -1,
        "domain_count": domain_count,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
