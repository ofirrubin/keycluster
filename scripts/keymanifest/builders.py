"""Builders for the core Keycloak Service + Deployment manifest objects."""

from __future__ import annotations

from typing import Any

from .config import KeycloakManifestConfig


def _secret_env(name: str, secret_name: str, key: str) -> dict[str, Any]:
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}}


def build_service(cfg: KeycloakManifestConfig) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "keycloak", "namespace": cfg.namespace},
        "spec": {
            "ports": [{"port": 8080, "targetPort": 8080, "name": "http"}],
            "selector": {"app": "keycloak"},
        },
    }


def _base_env(cfg: KeycloakManifestConfig) -> list[dict[str, Any]]:
    env = [
        {"name": "KC_DB", "value": "postgres"},
        {"name": "KC_DB_URL", "value": cfg.db_url},
        {"name": "KC_DB_USERNAME", "value": "keycloak"},
        _secret_env("KC_DB_PASSWORD", "keycloak-db-secret", "KC_DB_PASSWORD"),
        _secret_env("KEYCLOAK_ADMIN", "keycloak-admin-secret", "KEYCLOAK_ADMIN"),
        _secret_env("KEYCLOAK_ADMIN_PASSWORD", "keycloak-admin-secret", "KEYCLOAK_ADMIN_PASSWORD"),
        {"name": "KC_HOSTNAME_STRICT", "value": "false"},
        {"name": "KC_PROXY_HEADERS", "value": "xforwarded"},
        {"name": "KC_HTTP_ENABLED", "value": "true"},
    ]
    if cfg.is_ha:
        # Infinispan (ispn) distributed cache + KUBE_PING discovery so replicas
        # form one JGroups cluster and replicate session/auth state instead of
        # running side-by-side unaware of each other.
        env.extend(
            [
                {"name": "KC_CACHE", "value": "ispn"},
                {"name": "KC_CACHE_STACK", "value": cfg.cache_stack},
                {
                    "name": "KUBERNETES_NAMESPACE",
                    "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}},
                },
                {"name": "KUBERNETES_LABELS", "value": cfg.label_selector},
            ]
        )
    return env


def _container_security_context() -> dict[str, Any]:
    return {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}


def _pod_security_context() -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 1000,
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _probes() -> dict[str, Any]:
    # startupProbe gives the JGroups/Infinispan cluster view time to form
    # before readiness/liveness start counting failures, so replicas don't
    # flap while they're still discovering each other via KUBE_PING.
    return {
        "startupProbe": {
            "httpGet": {"path": "/health/started", "port": 9000},
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "failureThreshold": 30,
        },
        "readinessProbe": {
            "httpGet": {"path": "/health/ready", "port": 9000},
            "periodSeconds": 10,
            "failureThreshold": 3,
        },
        "livenessProbe": {
            "httpGet": {"path": "/health/live", "port": 9000},
            "periodSeconds": 10,
            "failureThreshold": 5,
        },
    }


def build_deployment(cfg: KeycloakManifestConfig) -> dict[str, Any]:
    ports = [{"containerPort": 8080, "name": "http"}, {"containerPort": 9000, "name": "health"}]
    pod_spec: dict[str, Any] = {
        "automountServiceAccountToken": cfg.is_ha,
        "securityContext": _pod_security_context(),
        "containers": [
            {
                "name": "keycloak",
                "image": cfg.image,
                "imagePullPolicy": "Always",
                "securityContext": _container_security_context(),
                "args": ["start", "--optimized"],
                "env": _base_env(cfg),
                "ports": ports,
                "resources": {
                    "requests": {"cpu": cfg.cpu_request, "memory": cfg.memory_request},
                    "limits": {"cpu": cfg.cpu_limit, "memory": cfg.memory_limit},
                },
                **_probes(),
            }
        ],
    }

    if cfg.is_ha:
        # A dedicated, minimally-scoped ServiceAccount is required for
        # KUBE_PING to list/watch pods -- the only automount exception, and it
        # only ever mounts this narrow-RBAC token, never the namespace default.
        pod_spec["serviceAccountName"] = "keycloak-cluster-sa"
        ports.append({"containerPort": 7800, "name": "jgroups"})
        pod_spec["affinity"] = {
            "podAntiAffinity": {
                "preferredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "weight": 100,
                        "podAffinityTerm": {
                            "labelSelector": {"matchLabels": {"app": "keycloak"}},
                            "topologyKey": "kubernetes.io/hostname",
                        },
                    }
                ]
            }
        }

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "keycloak", "namespace": cfg.namespace},
        "spec": {
            "replicas": cfg.replicas,
            "selector": {"matchLabels": {"app": "keycloak"}},
            "template": {"metadata": {"labels": {"app": "keycloak"}}, "spec": pod_spec},
        },
    }
