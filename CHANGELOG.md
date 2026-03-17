# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2026-02-24

Initial open-source release.

### Features

- **K-1: Domain-to-Realm Mapping** -- Bind a public domain to a Keycloak realm
  via the REST API. Each mapping generates a dedicated Kubernetes Ingress with
  path-based isolation so cross-realm access is blocked at the infrastructure
  level.

- **K-2: Security Header Management** -- Configure CSP, HSTS, X-Frame-Options,
  and SSL requirements per realm through the API. Headers are patched directly
  into Keycloak via the Admin REST API.

- **K-3: Dynamic Theming** -- Three bundled theme variants (`dynamic-standard`,
  `dynamic-half`, `dynamic-clean`) that load configuration from the API at
  runtime. Supports dark/light/system mode, custom colors, logo, font family,
  custom CSS, background images, and i18n (English, Hebrew).

- **K-4: TLS Support** -- Domain mappings support `tls_enabled` and
  `tls_secret_name` fields to configure TLS termination on generated Ingress
  resources. Works with cert-manager for automated Let's Encrypt certificates.

- **K-5: Role Templates** -- Define reusable sets of realm roles and apply them
  to any realm in one API call. Ships with three default templates: ecommerce,
  saas, and minimal.

- **K-6: Service Accounts** -- Create, list, delete, and rotate secrets for
  Keycloak service-account clients from a single set of endpoints.

- **K-7: Cluster Health** -- Aggregated health endpoint that checks Keycloak
  reachability, database connectivity, and domain mapping count.

- **K-8: Orphan Cleanup** -- Background job that removes Ingress resources no
  longer associated with a valid domain mapping.

- **K-9: Bulk Domain Operations** -- Create or update up to 500 domain mappings
  in a single transaction.

- **K-10: Domain Health Checks** -- Per-domain health endpoint that verifies DNS
  resolution, TLS certificate validity, and Keycloak responsiveness. Includes
  SSRF protection (RFC 1918 and loopback addresses blocked).

### Security

- JWT verification with JWKS rotation and RS256 algorithm pinning.
- Rate-limited JWKS refresh (max 10 per 60 seconds).
- SSRF protection on domain health checks (private IP ranges blocked).
- CSS injection prevention (@import, @font-face, data: URIs, javascript:
  blocked in custom CSS and background CSS).
- Input validation on all endpoints via Pydantic field validators.
- Audit logging for all mutating operations.
- Network policies for pod-to-pod traffic isolation.
- Non-root containers with dropped capabilities in all K8s manifests.
- Multi-stage Docker build for the Domain Manager.
