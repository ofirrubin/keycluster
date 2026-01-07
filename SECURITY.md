# Security & Integration Guide

## Multi-Domain Security

### 1. Ingress Isolation
We use Nginx Ingress annotations to ensure that a domain assigned to a specific realm cannot access other realms. This is handled by the `realm-ingress.yaml` template which blocks all `/realms/` paths except the authorized one.

### 2. Hostname Configuration
In `keycloak.yaml`, we set `KC_HOSTNAME_STRICT=false`. This allows Keycloak to serve requests from any domain passed by the Ingress. For production, ensure `KC_PROXY_HEADERS=xforwarded` is set so Keycloak correctly detects the client domain for generating redirect URIs.

### 3. SSL/TLS
Each domain must have a valid SSL certificate. Use [Cert-Manager](https://cert-manager.io/) with Let's Encrypt for automated certificate management.

## No-Code Theme Integration

The `dynamic-standard` theme is designed to be styled via an external API.

### How it works:
1. When the login page loads, `theme-injector.js` fetches settings from `https://api.keycluster.example.com/v1/themes/{realm}`.
2. It applies these settings to CSS variables (colors, logo, fonts).
3. For structural changes (ToS, Privacy links), your Keycloak API should use Keycloak's [Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html) to:
   - Update `Required Actions` (for 2FA).
   - Update `Realm Settings` (for ToS/Privacy links).
   - Update `Authentication Flows` (for WebAuthn).

## Enabling 2FA and WebAuthn

Keycloak supports these out of the box. To enable them via your "drag and drop" API:

1. **2FA (OTP)**:
   - Set the realm's default OTP policy.
   - Set `OTP` as a required action for new users.
2. **WebAuthn**:
   - Create a copy of the browser flow.
   - Add a "WebAuthn Authenticator" execution.
   - Set it to `Required`.

## Scalability

- **Database**: The PostgreSQL StatefulSet should be backed by a managed service (like RDS or Cloud SQL) for production.
- **Keycloak**: Can be scaled by increasing `replicas` in the deployment. Ensure you use a [JGroups DNS_PING](https://www.keycloak.org/server/caching) for clustering and cache discovery.
