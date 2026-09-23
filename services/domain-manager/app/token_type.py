"""
Access-token type gate (dam#634).

Local copy of `is_bearer_access_token` from the DaM monorepo's dam-auth-py
(libs/dam-auth-py/src/dam_auth/token_type.py, dam#635). keycluster is a
standalone repo whose domain-manager image is built from this directory alone
(see Dockerfile / requirements.txt), so it cannot depend on dam-auth-py; keep
the two in sync.

Keycloak introspection reports refresh, offline and ID tokens as active, and
ID tokens are RS256-signed like access tokens, so neither `active` nor a JWKS
signature check alone proves a bearer is an access token. Only a token whose
`typ` is "Bearer" (case-insensitive) and that carries no `cnf` (sender-
constrained, e.g. DPoP/mTLS-bound) confirmation claim is accepted. A missing
or non-string `typ` is rejected. Callers must make the rejection
indistinguishable from an invalid or inactive token.
"""

import logging
from collections.abc import Mapping

logger = logging.getLogger("app.token_type")

_BEARER_TYP = "bearer"
_MAX_LOGGED_TYP = 32


def is_bearer_access_token(claims: Mapping[str, object]) -> bool:
    """True only for an unconstrained access token (typ == Bearer, no cnf)."""
    typ = claims.get("typ")
    if isinstance(typ, str) and typ.lower() == _BEARER_TYP and "cnf" not in claims:
        return True
    logged = typ[:_MAX_LOGGED_TYP] if isinstance(typ, str) else None
    # Log the typ only (repr-escaped against log injection), never the token
    # or any other claim.
    logger.warning("bearer_rejected_token_type typ=%r", logged, extra={"typ": logged})
    return False
