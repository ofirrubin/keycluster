"""Assemble and serialize the full Keycloak manifest for a given config."""

from __future__ import annotations

from typing import Any

import yaml

from .builders import build_deployment, build_service
from .config import KeycloakManifestConfig
from .ha_extras import build_cluster_rbac, build_jgroups_network_policy, build_pod_disruption_budget


def build_manifest_objects(cfg: KeycloakManifestConfig) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = [build_service(cfg), build_deployment(cfg)]
    if cfg.is_ha:
        objects.extend(build_cluster_rbac(cfg))
        objects.append(build_jgroups_network_policy(cfg))
        objects.append(build_pod_disruption_budget(cfg))
    return objects


def render_manifest_yaml(cfg: KeycloakManifestConfig) -> str:
    docs = build_manifest_objects(cfg)
    return "---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs)
