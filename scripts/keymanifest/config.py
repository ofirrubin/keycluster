"""Configuration for generating the Keycloak Kubernetes manifest.

Everything is environment-driven (CLAUDE.md rule: no hardcoded values). Two
modes are supported:

- ``ha`` (default): production Infinispan-clustered, multi-replica Keycloak.
  New keycluster deployments get this by default.
- ``standalone``: the original single-replica, non-clustered shape. Existing
  tenant instances keep running this way unless an operator explicitly
  regenerates their manifest with ``--mode ha`` -- keycluster never mutates a
  running deployment on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

Mode = Literal["standalone", "ha"]

_VALID_MODES: tuple[Mode, ...] = ("standalone", "ha")


class ManifestConfigError(ValueError):
    """Raised when the requested manifest configuration is invalid."""


@dataclass(frozen=True)
class KeycloakManifestConfig:
    mode: Mode
    namespace: str
    replicas: int
    image: str
    db_host: str
    db_port: str
    db_name: str
    cache_stack: str
    label_selector: str
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str

    @property
    def is_ha(self) -> bool:
        return self.mode == "ha"

    @property
    def db_url(self) -> str:
        return f"jdbc:postgresql://{self.db_host}:{self.db_port}/{self.db_name}"


def _get(values: dict[str, str], name: str, default: str) -> str:
    value = values.get(name)
    return value if value else default


def _get_int(values: dict[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ManifestConfigError(f"{name} must be an integer") from exc


def load_config(overrides: Optional[dict[str, Optional[str]]] = None) -> KeycloakManifestConfig:
    """Build config from environment variables, layering optional overrides on top."""
    values: dict[str, str] = dict(os.environ)
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                values[key] = value

    mode = _get(values, "KEYCLUSTER_MODE", "ha")
    if mode not in _VALID_MODES:
        raise ManifestConfigError(f"KEYCLUSTER_MODE must be one of {_VALID_MODES}")

    default_replicas = 3 if mode == "ha" else 1
    replicas = _get_int(values, "KEYCLUSTER_REPLICAS", default_replicas)

    if replicas < 1:
        raise ManifestConfigError("KEYCLUSTER_REPLICAS must be >= 1")
    if mode == "ha" and replicas < 2:
        raise ManifestConfigError("HA mode requires KEYCLUSTER_REPLICAS >= 2")

    return KeycloakManifestConfig(
        mode=mode,  # type: ignore[arg-type]
        namespace=_get(values, "KEYCLUSTER_NAMESPACE", "keycloak"),
        replicas=replicas,
        image=_get(values, "KC_IMAGE", "keycloak-custom:latest"),
        db_host=_get(values, "KC_DB_HOST", "postgres"),
        db_port=_get(values, "KC_DB_PORT", "5432"),
        db_name=_get(values, "KC_DB_NAME", "keycloak"),
        cache_stack=_get(values, "KEYCLUSTER_CACHE_STACK", "kubernetes"),
        label_selector=_get(values, "KEYCLUSTER_LABEL_SELECTOR", "app=keycloak"),
        cpu_request=_get(values, "KC_CPU_REQUEST", "500m"),
        cpu_limit=_get(values, "KC_CPU_LIMIT", "1000m"),
        memory_request=_get(values, "KC_MEMORY_REQUEST", "1Gi"),
        memory_limit=_get(values, "KC_MEMORY_LIMIT", "2Gi"),
    )
