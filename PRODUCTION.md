# Production Readiness Roadmap

This document outlines the steps needed to take Keycluster from a local
development setup to a production SaaS deployment supporting many tenants.

## 1. High Availability and Scalability

- **Keycloak Clustering (done)**: `scripts/generate_keycloak_manifest.py`
  generates a production HA manifest -- Infinispan (`ispn`) distributed
  session cache, `KUBE_PING` JGroups discovery (K8s API-based, no extra
  headless Service needed), 3 replicas by default (configurable, minimum 2),
  pod anti-affinity to spread replicas across nodes, a `PodDisruptionBudget`,
  and a `startupProbe` sized to survive cluster-formation time without
  flapping readiness/liveness. Defaults to HA for **new** instances; existing
  running instances keep the original single-replica manifest
  (`k8s/keycloak.yaml`) until an operator explicitly runs
  `make deploy-keycloak-ha` to upgrade one. See `scripts/keymanifest/` and
  `tests/test_keycloak_manifest.py`.
- **Managed Database**: Do not run PostgreSQL in a StatefulSet for production.
  Use a managed service such as AWS RDS, GKE Cloud SQL, or Azure Database for
  PostgreSQL. Update `DATABASE_URL` to a secure connection string.
- **Horizontal Pod Autoscaling (HPA)**: Set up HPA to scale Keycloak replicas
  based on CPU, memory, or custom metrics such as active login count.

## 2. Infrastructure and Networking

- **SSL/TLS (Cert-Manager)**: Deploy `cert-manager` with the Let's Encrypt
  ACME issuer. The Domain Manager can be extended to automatically create
  `Certificate` resources for every new domain.
- **Ingress Controller Tuning**: For high traffic, tune the Nginx Ingress
  controller:
  - `proxy-buffer-size`: 128k (already included in generated Ingresses).
  - `worker-connections`: Increase to 10k+.
  - `keep-alive`: Optimize for OIDC redirect flows.

## 3. Security Hardening

- **Encryption at Rest**: Ensure the database is encrypted at rest (managed
  database services provide this by default).
- **Secrets Management**: Use a provider such as HashiCorp Vault or AWS Secrets
  Manager to inject credentials instead of storing them in plain
  `k8s/secrets.yaml`.
- **Admin Isolation**: Disable the Keycloak Admin Console on public domains.
  Restrict admin access to a VPN or internal-only Ingress.
- **CORS Restriction**: Set `CORS_ALLOWED_ORIGIN` to your admin panel's
  specific origin rather than `*`.
- **HSTS**: Enable HSTS with a long max-age on all domain mappings (the default
  is 31536000 seconds / one year).
- **Network Policies**: Apply
  `k8s/templates/network-policy-default.yaml` to restrict pod-to-pod traffic.
- **RBAC**: Ensure only the Domain Manager service account has permission to
  create or modify Ingresses in the `keycloak` namespace.

## 4. Operational Excellence

- **Prometheus / Grafana**: Keycloak exposes a `/metrics` endpoint. Use it to
  monitor:
  - Login failure rates (brute force detection).
  - Database connection pool usage.
  - Token verification latency.
- **Centralized Logging**: Ship logs to an ELK stack or Loki for cross-realm
  debugging and audit trail analysis.

## 5. Theme API Integration

- The Domain Manager already persists theme configuration per realm. Connect
  your admin UI to the `POST /v1/themes/{realm}` endpoint to let tenants
  customize their login pages.
- Use a CDN (Cloudflare, CloudFront) in front of the Keycloak Ingress to
  cache static assets while keeping OIDC endpoints dynamic.
