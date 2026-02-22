# Fresh Installation Guide (Production / Staging)

This guide covers setting up Keycluster on any standard Kubernetes cluster
(EKS, GKE, DigitalOcean, or bare metal).

## Prerequisites

- `kubectl` configured with cluster access.
- `helm` installed (optional but recommended for Ingress).
- Nginx Ingress Controller installed.

## 1. Prepare Ingress Controller

The Domain Manager uses **path-based isolation** by default, which works on all
standard Kubernetes clusters without special configuration. Each domain's
Ingress only allows paths for its specific realm (`/realms/{realm}`,
`/admin/{realm}`, and related resource paths).

### (Optional) Enhanced Isolation via Snippets

If you choose to use the legacy snippet-based isolation (which allows more
complex regex blocking), you must enable snippet annotations:

```bash
kubectl patch configmap ingress-nginx-controller -n ingress-nginx \
  --type merge -p '{"data":{"allow-snippet-annotations":"true"}}'
```

See the security audit section below before enabling this in production.

---

## 2. Infrastructure Setup

### Create Namespace

```bash
kubectl apply -f k8s/namespace.yaml
```

### Setup Secrets

Update `k8s/secrets.yaml` with strong, unique passwords before applying. Never
deploy with the default placeholder values.

```bash
kubectl apply -f k8s/secrets.yaml
```

For production, use a secrets manager (HashiCorp Vault, AWS Secrets Manager) to
inject credentials instead of plain Kubernetes secrets.

### Deploy Database

For production, it is strongly recommended to use a managed database (RDS,
Cloud SQL) instead of the included StatefulSet.

```bash
kubectl apply -f k8s/postgres.yaml
# Wait for DB
kubectl wait --for=condition=ready pod -l app=postgres -n keycloak
```

---

## 3. Application Deployment

### Build and Push Images

Build the images and push them to a registry (Docker Hub, ECR, etc.) that your
cluster can access.

```bash
# Example for Docker Hub
docker build -t your-org/keycloak-custom:latest .
docker push your-org/keycloak-custom:latest

docker build -t your-org/domain-manager:latest ./services/domain-manager
docker push your-org/domain-manager:latest
```

### Update Manifests

Update `k8s/keycloak.yaml` and `k8s/domain-manager.yaml` to reference your
registry images instead of `latest` or local tags.

### Apply Manifests

```bash
kubectl apply -f k8s/keycloak.yaml
kubectl apply -f k8s/domain-manager.yaml
```

---

## 4. Post-Deployment Security Hardening

After deployment, take these additional steps:

- Set `ssl_required` to `all` or `external` on all domain mappings.
- Enable HSTS with `hsts_max_age: 31536000` (one year).
- Restrict `CORS_ALLOWED_ORIGIN` to your admin panel's origin.
- Apply the default network policy:
  ```bash
  kubectl apply -f k8s/templates/network-policy-default.yaml
  ```
- Restrict Ingress creation permissions in the `keycloak` namespace to only the
  Domain Manager service account.

---

## Security Audit: `allow-snippet-annotations`

### Is this a concern?

Yes, it increases the attack surface. Enabling snippets allows anyone with
Ingress creation permissions in the cluster to inject custom Nginx
configuration.

### Mitigations

1. **RBAC Isolation**: The deployment (`k8s/domain-manager.yaml`) uses a
   dedicated `ServiceAccount` and `ClusterRole`.
2. **Namespace Locking**: Only the Domain Manager should have permission to
   create or edit Ingresses in the `keycloak` namespace.
3. **Restricted Access**: Other services and human users should not have
   permissions to create Ingresses in the `keycloak` namespace.

### Best Practice

If you are on a multi-tenant cluster where many developers can create
Ingresses, use an
[Ingress Admission Webhook](https://kubernetes.github.io/ingress-nginx/user-guide/access-control/#ingress-admission-webhook)
or a policy engine like **Kyverno** to restrict which snippet content is
allowed.
