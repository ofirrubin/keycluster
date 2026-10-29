"""JWKS discovery, cache and rate-limited refresh for the domain-manager service.

Holds the process-wide JWKS cache. Refreshes are bounded by a sliding-window
limiter; a refresh it denies serves the cached JWKS (stale or not), and only a
process that has never fetched keys raises JwksRateLimited (429 upstream).
"""
import asyncio
import logging
import os
import time
import urllib.parse
from typing import Optional

import httpx

from app.sliding_window import SlidingWindowLimiter

logger = logging.getLogger(__name__)

KEYCLOAK_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "master")

_jwks_cache: Optional[dict] = None
_jwks_uri_cache: Optional[str] = None
_jwks_cache_time: float = 0.0
_JWKS_CACHE_TTL: float = 300.0  # 5 minutes
_jwks_lock: asyncio.Lock = asyncio.Lock()

# Rate limiter: at most 10 JWKS refresh cycles (discovery + keys) per 60 seconds.
# A refresh denied by the limiter serves the cached JWKS, stale or not; only a
# process that has never fetched keys answers 429 (see app.auth.verify_token).
_JWKS_MAX_FETCHES_PER_MIN: int = 10
_jwks_rate_limiter = SlidingWindowLimiter(_JWKS_MAX_FETCHES_PER_MIN, 60.0)


class JwksRateLimited(Exception):
    """JWKS refresh denied by the rate limiter and no cached JWKS to fall back on."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("JWKS fetch rate limit exceeded")
        self.retry_after = retry_after


def _jwks_cache_fresh() -> bool:
    return _jwks_cache is not None and (time.monotonic() - _jwks_cache_time) <= _JWKS_CACHE_TTL


async def _load_jwks() -> dict:
    """Fetch discovery + JWKS under the limiter. Caller must hold _jwks_lock."""
    global _jwks_cache, _jwks_uri_cache, _jwks_cache_time
    if not _jwks_rate_limiter.try_acquire():
        if _jwks_cache is not None:
            logger.warning("jwks_rate_limited serving_cached=true")
            return _jwks_cache
        logger.warning("jwks_rate_limited serving_cached=false")
        raise JwksRateLimited(_jwks_rate_limiter.retry_after())
    if _jwks_uri_cache is None:
        _jwks_uri_cache = await _fetch_keycloak_jwks_uri()
    try:
        _jwks_cache = await _fetch_jwks(_jwks_uri_cache)
    except Exception:
        _jwks_uri_cache = None  # rediscover next time in case jwks_uri moved
        raise
    _jwks_cache_time = time.monotonic()
    return _jwks_cache


async def _fetch_keycloak_jwks_uri() -> str:
    encoded_realm = urllib.parse.quote(KEYCLOAK_REALM, safe="")
    url = f"{KEYCLOAK_SERVER_URL}/realms/{encoded_realm}/.well-known/openid-configuration"
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
    # Fast path: return cached JWKS without acquiring the lock
    if _jwks_cache_fresh():
        return _jwks_cache
    # Slow path: acquire lock so only one coroutine fetches at a time
    async with _jwks_lock:
        # Double-check after acquiring the lock (another coroutine may have refreshed)
        if _jwks_cache_fresh():
            return _jwks_cache
        return await _load_jwks()


async def _refresh_jwks() -> dict:
    async with _jwks_lock:
        return await _load_jwks()
