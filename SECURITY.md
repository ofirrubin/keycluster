# Security and Integration Guide

## Multi-Domain Security

### 1. Ingress Isolation

Nginx Ingress annotations ensure that a domain assigned to a specific realm
cannot access other realms. The generated Ingress only allows paths under
`/realms/{realm}` and related resource paths for the bound realm.

### 2. Hostname Configuration

In `keycloak.yaml`, `KC_HOSTNAME_STRICT` is set to `false`. This allows
Keycloak to serve requests from any domain passed by the Ingress. For
production, ensure `KC_PROXY_HEADERS=xforwarded` is set so Keycloak correctly
detects the client domain when generating redirect URIs.

### 3. SSL/TLS

Each domain should have a valid SSL certificate. Use
[cert-manager](https://cert-manager.io/) with Let's Encrypt for automated
certificate management. The Domain Manager supports `tls_enabled` and
`tls_secret_name` fields on domain mappings to configure TLS on generated
Ingress resources.

## Dynamic Theme Integration

The bundled dynamic themes (`dynamic-standard`, `dynamic-half`, `dynamic-clean`)
are styled via the Domain Manager API.

### How it works

1. When the login page loads, `theme-injector.js` fetches settings from
   `/v1/themes/{realm}` on the Domain Manager.
2. It applies these settings to CSS variables (colors, logo, fonts, dark/light
   mode).
3. For structural changes (Terms of Service links, Privacy Policy links), use
   the Keycloak
   [Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
   to:
   - Update Required Actions (for 2FA).
   - Update Realm Settings (for ToS/Privacy links).
   - Update Authentication Flows (for WebAuthn).

## Enabling 2FA and WebAuthn

Keycloak supports these out of the box:

1. **2FA (OTP)**:
   - Set the realm's default OTP policy.
   - Set `OTP` as a required action for new users.
2. **WebAuthn**:
   - Create a copy of the browser authentication flow.
   - Add a "WebAuthn Authenticator" execution.
   - Set it to `Required`.

## Scalability

- **Database**: The PostgreSQL StatefulSet included in `k8s/` is intended for
  development. For production, use a managed database service (AWS RDS, GKE
  Cloud SQL, or equivalent).
- **Keycloak**: Scale by increasing `replicas` in the deployment. Configure
  [JGroups DNS_PING](https://www.keycloak.org/server/caching) for clustering
  and cache discovery.
