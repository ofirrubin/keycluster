"""keycluster#7: the JWKS rate limiter must never turn into an auth failure.

The old limiter pruned a deque with ``pop(0)`` (TypeError once a timestamp
aged past 60s) and raised ValueError once the limit was hit; verify_token
mapped both to 401 for every caller. These tests drive the real auth and
jwks_cache modules with a fake clock and a fake Keycloak (httpx.AsyncClient),
reloading both per test for fresh cache and limiter state.
"""
import asyncio
import importlib
import time
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import auth, jwks_cache
from app.sliding_window import SlidingWindowLimiter

EPOCH = 1_800_000_000.0
JWKS_URI = "http://keycloak.test/realms/master/protocol/openid-connect/certs"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeKeycloak:
    def __init__(self) -> None:
        self.jwks_fetches = 0
        self.fail = False

    def client(self) -> "FakeClient":
        return FakeClient(self)


class FakeResponse:
    def __init__(self, body: dict[str, Any], status: int) -> None:
        self._body = body
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise RuntimeError(f"upstream status {self._status}")

    def json(self) -> dict[str, Any]:
        return self._body


class FakeClient:
    def __init__(self, kc: FakeKeycloak) -> None:
        self._kc = kc

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, timeout: float) -> FakeResponse:
        if self._kc.fail:
            return FakeResponse({}, 503)
        if url.endswith("/.well-known/openid-configuration"):
            return FakeResponse({"jwks_uri": JWKS_URI}, 200)
        self._kc.jwks_fetches += 1
        return FakeResponse({"keys": [{"kid": "k1"}], "n": self._kc.jwks_fetches}, 200)


def _reload() -> None:
    """Fresh cache + limiter state; auth re-imports the reloaded functions."""
    importlib.reload(jwks_cache)
    importlib.reload(auth)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeClock, FakeKeycloak]]:
    clock = FakeClock()
    kc = FakeKeycloak()
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(time, "time", lambda: EPOCH + clock.t)
    _reload()
    monkeypatch.setattr(jwks_cache.httpx, "AsyncClient", kc.client)

    def verify(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
        if not any(k.get("kid") == "k1" for k in jwks.get("keys", [])):
            raise ValueError("unknown kid")
        now = time.time()
        return {
            "typ": "Bearer",
            "sub": "user",
            "exp": now + 300,
            "iat": now,
            "iss": f"{auth.KEYCLOAK_SERVER_URL}/realms/{auth.KEYCLOAK_REALM}",
            "aud": [auth.KEYCLOAK_CLIENT_ID],
            "azp": auth.KEYCLOAK_CLIENT_ID,
        }

    monkeypatch.setattr(auth, "_verify_jwt_signature", verify)
    yield clock, kc
    monkeypatch.undo()
    _reload()


def _request() -> Request:
    headers = [(b"authorization", b"Bearer a.b.c")]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _verify() -> dict[str, Any]:
    return asyncio.run(auth.verify_token(_request()))


def test_fetch_after_window_ages_out_does_not_crash(env: tuple[FakeClock, FakeKeycloak]) -> None:
    """Old code: TypeError from deque.pop(0) on the first fetch after 60s."""
    clock, kc = env
    assert _verify()["sub"] == "user"
    clock.t = 301.0  # cache expired, first timestamp is older than the window
    assert _verify()["sub"] == "user"
    assert kc.jwks_fetches == 2


def test_limit_hit_then_one_more_refresh_serves_cache(env: tuple[FakeClock, FakeKeycloak]) -> None:
    """Old code: the 11th refresh raised ValueError instead of degrading."""
    _, kc = env
    for _ in range(jwks_cache._JWKS_MAX_FETCHES_PER_MIN):
        asyncio.run(jwks_cache._refresh_jwks())
    jwks = asyncio.run(jwks_cache._refresh_jwks())
    assert jwks["keys"] == [{"kid": "k1"}]
    assert kc.jwks_fetches == jwks_cache._JWKS_MAX_FETCHES_PER_MIN
    assert _verify()["sub"] == "user"


def test_limit_hit_with_stale_cache_still_authenticates(env: tuple[FakeClock, FakeKeycloak]) -> None:
    clock, kc = env
    assert _verify()["sub"] == "user"
    clock.t = 301.0
    kc.fail = True  # Keycloak outage burns the refresh budget
    # each failed verify spends two slots: the cache refill, then the retry refresh
    for _ in range(jwks_cache._JWKS_MAX_FETCHES_PER_MIN // 2):
        with pytest.raises(HTTPException) as exc_info:
            _verify()
        assert exc_info.value.status_code == 401
    assert _verify()["sub"] == "user"  # limiter denies, stale JWKS is served


def test_limit_hit_without_cache_returns_429(env: tuple[FakeClock, FakeKeycloak]) -> None:
    clock, kc = env
    kc.fail = True
    for _ in range(jwks_cache._JWKS_MAX_FETCHES_PER_MIN // 2):
        with pytest.raises(HTTPException):
            _verify()
    clock.t = 15.0
    with pytest.raises(HTTPException) as exc_info:
        _verify()
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "45"}
    kc.fail = False
    clock.t = 60.0
    assert _verify()["sub"] == "user"


def test_limiter_memory_is_bounded() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60.0, clock=clock)
    results = []
    for i in range(1000):
        clock.t = i * 0.01
        results.append(limiter.try_acquire())
        assert len(limiter._events) <= 3
    assert results.count(True) == 3


def test_limiter_window_slides() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, 60.0, clock=clock)
    assert limiter.try_acquire()
    clock.t = 10.0
    assert limiter.try_acquire()
    assert not limiter.try_acquire()
    clock.t = 30.0
    assert limiter.retry_after() == 30.0
    clock.t = 60.0  # the t=0 event leaves the window, the t=10 one stays
    assert limiter.try_acquire()
    assert not limiter.try_acquire()


def test_limiter_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0, 60.0)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(1, 0.0)
