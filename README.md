# Keycluster

Project for managing multi-tenant Keycloak on Kubernetes with dynamic domains and no-code themes.

## Architecture

- **Keycloak**: Core identity provider.
- **Postgres**: Database for Keycloak.
- **Ingress Controller**: Handles domain routing.
- **Dynamic Theme**: A theme that fetches configuration from an external API.

## Requirements Checklist

- [x] Domain per realm isolation.
- [x] Non-persistent to persistent domain mapping.
- [x] API-editable themes (No-code).
- [x] Secure (2FA, WebAuthn).
- [x] Scalable K8s deployment.

## Automation Flow

### 1. Live Domain Updates
When your project's API adds a new domain for a customer:
1. Use the [Kubernetes Client SDK](https://github.com/kubernetes-client/javascript) (or similar) in your microservice.
2. Template the `k8s/templates/realm-ingress.yaml`.
3. Apply the Ingress resource to the cluster.
4. Nginx Ingress Controller will pick up the change immediately.

### 2. No-Code Theme Editing
1. Your UI allows users to pick colors/fonts.
2. Save these values to your database.
3. Expose the endpoint `GET /v1/themes/:realm` for the `theme-injector.js` to consume.
4. Changes appear on the next page load of the Keycloak login screen.
