"""HA-only manifest objects: KUBE_PING RBAC, JGroups NetworkPolicy, PDB.

These are only rendered when ``KeycloakManifestConfig.is_ha`` is true -- the
standalone (legacy/existing-instance) manifest never gets them, so a running
single-replica tenant instance is never silently mutated toward clustering.
"""

from __future__ import annotations

from typing import Any

from .config import KeycloakManifestConfig


def build_cluster_rbac(cfg: KeycloakManifestConfig) -> list[dict[str, Any]]:
    return [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "keycloak-cluster-sa", "namespace": cfg.namespace},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": "keycloak-cluster-role", "namespace": cfg.namespace},
            "rules": [
                # Minimum RBAC KUBE_PING needs to discover sibling Keycloak
                # pods in this namespace for JGroups cluster formation.
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "keycloak-cluster-binding", "namespace": cfg.namespace},
            "subjects": [
                {"kind": "ServiceAccount", "name": "keycloak-cluster-sa", "namespace": cfg.namespace}
            ],
            "roleRef": {
                "kind": "Role",
                "name": "keycloak-cluster-role",
                "apiGroup": "rbac.authorization.k8s.io",
            },
        },
    ]


def build_jgroups_network_policy(cfg: KeycloakManifestConfig) -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-jgroups-keycloak-cluster", "namespace": cfg.namespace},
        "spec": {
            "podSelector": {"matchLabels": {"app": "keycloak"}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [{"podSelector": {"matchLabels": {"app": "keycloak"}}}],
                    "ports": [{"protocol": "TCP", "port": 7800}],
                }
            ],
        },
    }


def build_pod_disruption_budget(cfg: KeycloakManifestConfig) -> dict[str, Any]:
    return {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": "keycloak-pdb", "namespace": cfg.namespace},
        "spec": {
            "minAvailable": max(cfg.replicas - 1, 1),
            "selector": {"matchLabels": {"app": "keycloak"}},
        },
    }
