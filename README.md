# Keycluster

Project for managing multi-tenant Keycloak on Kubernetes with dynamic domains, security isolation, and no-code theme preview.

## Architecture

- **Keycloak**: Core identity provider (Custom image with themes).
- **Postgres**: Database for Keycloak.
- **Domain Manager**: FastAPI service that manages Ingress resources, realm security headers, and serves theme configurations.
- **Ingress Controller**: Routing layer (Nginx).

## Deployment

### Prerequisites
- Kubernetes Cluster (Minikube, Kind, or Cloud).
- `kubectl` configured.
- `docker` (for building images).

### 1. Build Images
Build the custom Keycloak and Domain Manager images:

```bash
# Keycloak (with themes)
docker build -t keycloak-custom:latest ./keycloak-custom

# Domain Manager
docker build -t domain-manager:latest ./services/domain-manager
```

### 2. Deploy Infrastructure
Apply the Kubernetes manifests:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml       # Ensure you set your secrets first!
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/keycloak.yaml
kubectl apply -f k8s/domain-manager.yaml
kubectl apply -f k8s/templates/network-policy-default.yaml
```

### 3. Verification
Ensure all pods are running:

```bash
kubectl get pods -n keycloak
```

## Configuration

### Environment Variables
**Domain Manager** (`k8s/domain-manager.yaml`):
- `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`: Credentials for Keycloak Admin API access (from Secrets).
- `DATABASE_URL`: Connection string for its own DB (e.g., SQLite or Postgres).
- `EDITOR_ORIGIN`: The origin URL of the Live Editor for CSP configuration (default: `http://localhost:8000`).

**Keycloak** (`k8s/keycloak.yaml`):
- Standard Keycloak env vars (`KC_DB`, `KC_HOSTNAME_STRICT`, etc.).

## Management Workflows

### Managing Realms
Realms are managed via the standard Keycloak Admin Console.

1.  **Access**: Port-forward the Keycloak service (if Ingress is locked down):
    ```bash
    kubectl port-forward svc/keycloak 8080:8080 -n keycloak
    ```
2.  **Login**: Open `http://localhost:8080/admin` and log in with your admin credentials.
3.  **Create Realm**: Create a new realm (e.g., `tenant-a`).
4.  **Configure**: Set up clients, users, etc. as usual.

### Managing Domains & Security
Use the **Domain Manager API** to bind a generic domain to a specific realm and configure security policies.

**API Endpoint**: `POST /domains`
**Host**: Domain Manager Service (port 80 inside cluster, 8000 locally via port-forward)

```bash
# Example Payload
curl -X POST http://localhost:8000/domains \
  -H "Content-Type: application/json" \
  -d '{
    "realm": "tenant-a",
    "domain": "login.tenant-a.com",
    "enabled": true,
    "csp_allowed_origins": ["https://editor.myapp.com"],
    "ssl_required": "external",
    "hsts_max_age": 31536000
  }'
```

**Parameters**:
- `realm`: Target Keycloak realm name.
- `domain`: The public domain for this realm.
- `csp_allowed_origins`: List of URLs allowed to frame the login page (e.g. your editor/CMS).
- `ssl_required`: Keycloak SSL mode (`all`, `external`, `none`).
- `hsts_max_age`: HSTS max-age in seconds (0 to disable).

This action will:
1.  Create/Update a Kubernetes Ingress for the domain.
2.  Patch the Keycloak Realm's security headers (CSP, X-Frame-Options) to match the settings.

### Managing Themes
**Live Preview (PoC)**:
1.  Navigate to `examples/live-editor`.
2.  Run `python3 -m http.server 8000`.
3.  Open `http://localhost:8000`.
4.  Enter your realm URL (configured above) to preview changes live.

**Persistence**:
Currently, the system serves a default theme configuration via `GET /v1/themes/{realm}`. To enable persistent custom themes per realm, the Domain Manager database schema and API need to be extended to store/retrieve the `ThemeConfig` JSON object.

## API Reference

### Domain Manager

| Method | Path | Description |
|:---|:---|:---|
| `POST` | `/domains` | Create/Update domain mapping & security headers. |
| `DELETE` | `/domains/{realm}` | Remove domain mapping & Ingress. |
| `POST` | `/cleanup` | Trigger background cleanup of orphan Ingresses/Realms. |
| `GET` | `/v1/themes/{realm}` | Fetch theme configuration for a realm. |
| `GET` | `/health` | Service health check. |

## Production Readiness Checklist

### Security & Hardening
- [ ] **Disable HTTP**: Ensure `sslRequired` is set to `all` or `external`.
- [ ] **HSTS**: Enable `hsts_max_age` (e.g., 31536000).
- [ ] **CSP Strictness**: Restrict `csp_allowed_origins` to trusted domains only.
- [ ] **Network Policies**: `k8s/templates/network-policy-default.yaml` is applied.

### Validation & Stability
- [ ] **Resource Limits**: Define CPU/Memory requests/limits for all containers.
- [ ] **Liveness/Readiness Probes**: Tune probes in deployment manifests.
- [ ] **Database**: Ensure Postgres is HA with backups.

### Scaling
- [ ] **Replicas**: Configure HPA for Keycloak/Domain Manager.
- [ ] **Infinispan**: Configure distributed caching for multi-replica Keycloak.
