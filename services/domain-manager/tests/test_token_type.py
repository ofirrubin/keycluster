"""dam#634: only unconstrained typ=Bearer access tokens are accepted.

Covers the helper, the JWKS path (verify_token) and the introspection path
(introspect_token). Signature verification and the Keycloak HTTP call are
replaced with in-process fakes; no real token or network is involved.
"""
import asyncio
import logging
import time
from typing import Any, Optional

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import auth
from app.token_type import is_bearer_access_token

ACCEPTED_TYPS = ["Bearer", "bearer", "BEARER"]

# (case id, claim overrides, typ expected in the rejection log)
REJECTED: list[tuple[str, dict[str, Any], Optional[str]]] = [
    ("refresh", {"typ": "Refresh"}, "Refresh"),
    ("offline", {"typ": "Offline"}, "Offline"),
    ("id", {"typ": "ID"}, "ID"),
    ("dpop-cnf", {"typ": "Bearer", "cnf": {"jkt": "thumbprint"}}, "Bearer"),
    ("missing", {"typ": None}, None),
    ("non-string", {"typ": 1}, None),
    ("empty", {"typ": ""}, ""),
]
REJECTED_IDS = [case[0] for case in REJECTED]

SENTINEL_TOKEN = "header.payload-sentinel.signature"
SENTINEL_SUB = "sub-sentinel-7f3a"


def _claims(overrides: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    claims: dict[str, Any] = {
        "typ": "Bearer",
        "sub": SENTINEL_SUB,
        "exp": now + 300,
        "iat": now - 5,
        "iss": f"{auth.KEYCLOAK_SERVER_URL}/realms/{auth.KEYCLOAK_REALM}",
        "aud": [auth.KEYCLOAK_CLIENT_ID],
        "azp": auth.KEYCLOAK_CLIENT_ID,
    }
    for key, value in overrides.items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return claims


def _request() -> Request:
    headers = [(b"authorization", f"Bearer {SENTINEL_TOKEN}".encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _rejection_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("bearer_rejected_token_type")]


def _assert_rejection_logged(caplog: pytest.LogCaptureFixture, typ: Optional[str]) -> None:
    records = _rejection_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert getattr(record, "typ") == typ
    assert record.getMessage() == f"bearer_rejected_token_type typ={typ!r}"
    # Only the typ is logged: never the token or other claims.
    assert SENTINEL_TOKEN not in record.getMessage()
    assert SENTINEL_SUB not in record.getMessage()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("typ", ACCEPTED_TYPS)
def test_helper_accepts_bearer_any_case(typ: str, caplog: pytest.LogCaptureFixture) -> None:
    assert is_bearer_access_token({"typ": typ}) is True
    assert _rejection_records(caplog) == []


@pytest.mark.parametrize(("case", "overrides", "logged"), REJECTED, ids=REJECTED_IDS)
def test_helper_rejects_non_access_tokens(
    case: str, overrides: dict[str, Any], logged: Optional[str], caplog: pytest.LogCaptureFixture
) -> None:
    assert is_bearer_access_token(_claims(overrides)) is False
    _assert_rejection_logged(caplog, logged)


def test_helper_truncates_logged_typ(caplog: pytest.LogCaptureFixture) -> None:
    assert is_bearer_access_token({"typ": "X" * 100}) is False
    _assert_rejection_logged(caplog, "X" * 32)


# ---------------------------------------------------------------------------
# JWKS path: verify_token
# ---------------------------------------------------------------------------
@pytest.fixture
def signed_claims(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Make signature verification succeed and return the given claims."""
    claims: dict[str, Any] = {}

    async def fake_jwks() -> dict[str, Any]:
        return {"keys": []}

    def fake_verify(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
        return dict(claims)

    monkeypatch.setattr(auth, "_get_jwks", fake_jwks)
    monkeypatch.setattr(auth, "_refresh_jwks", fake_jwks)
    monkeypatch.setattr(auth, "_verify_jwt_signature", fake_verify)
    return claims


def _bad_signature_error(monkeypatch: pytest.MonkeyPatch) -> HTTPException:
    def failing_verify(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad signature")

    with monkeypatch.context() as m:
        m.setattr(auth, "_verify_jwt_signature", failing_verify)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(auth.verify_token(_request()))
    return exc_info.value


@pytest.mark.parametrize("typ", ACCEPTED_TYPS)
def test_verify_token_accepts_bearer(typ: str, signed_claims: dict[str, Any]) -> None:
    signed_claims.update(_claims({"typ": typ}))
    assert asyncio.run(auth.verify_token(_request()))["typ"] == typ


@pytest.mark.parametrize(("case", "overrides", "logged"), REJECTED, ids=REJECTED_IDS)
def test_verify_token_rejects_like_invalid_signature(
    case: str,
    overrides: dict[str, Any],
    logged: Optional[str],
    signed_claims: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    signed_claims.update(_claims(overrides))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth.verify_token(_request()))
    _assert_rejection_logged(caplog, logged)

    invalid = _bad_signature_error(monkeypatch)
    assert exc_info.value.status_code == invalid.status_code == 401
    assert exc_info.value.detail == invalid.detail
    assert exc_info.value.headers == invalid.headers


# ---------------------------------------------------------------------------
# Introspection path: introspect_token
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _fake_client(body: dict[str, Any]) -> type:
    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, data: dict[str, str], timeout: float) -> _FakeResponse:
            return _FakeResponse(body)

    return _FakeClient


def _introspect(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> bool:
    monkeypatch.setattr(auth, "_ADMIN_CLIENT_ID", "introspect-client")
    monkeypatch.setattr(auth, "_ADMIN_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(body))
    return asyncio.run(auth.introspect_token(SENTINEL_TOKEN))


@pytest.mark.parametrize("typ", ACCEPTED_TYPS)
def test_introspect_accepts_active_bearer(typ: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _introspect(monkeypatch, {"active": True, **_claims({"typ": typ})}) is True


@pytest.mark.parametrize(("case", "overrides", "logged"), REJECTED, ids=REJECTED_IDS)
def test_introspect_rejects_like_inactive(
    case: str,
    overrides: dict[str, Any],
    logged: Optional[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rejected = _introspect(monkeypatch, {"active": True, **_claims(overrides)})
    _assert_rejection_logged(caplog, logged)
    assert rejected is _introspect(monkeypatch, {"active": False}) is False


def test_introspect_inactive_does_not_log_typ(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    assert _introspect(monkeypatch, {"active": False}) is False
    assert _rejection_records(caplog) == []
