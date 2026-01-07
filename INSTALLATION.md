# Fresh Installation Guide (Production/Staging)

This guide covers setting up Keycluster on any standard Kubernetes cluster (EKS, GKE, DigitalOcean, or Bare Metal).

## Prerequisites

- `kubectl` configured with cluster access.
- `helm` installed (optional but recommended for Ingress).
- Nginx Ingress Controller installed.

## 1. Prepare Ingress Controller

Our domain manager uses **Path-based Isolation** by default, which works on all standard Kubernetes clusters without special configuration. It restricts each domain to only its specific `/realms/{realm}` and `/admin/{realm}` paths.

### (Optional) Enhanced Isolation via Snippets
If you choose to use the legacy snippet-based isolation (which allows more complex regex blocking), you must enable snippet annotations:

```bash
kubectl patch configmap ingress-nginx-controller -n ingress-nginx --type merge -p '{"data":{"allow-snippet-annotations":"true"}}'
```

---

## 2. Infrastructure Setup

### Create Namespace
```bash
kubectl apply -f k8s/namespace.yaml
```

### Setup Secrets
Update `k8s/secrets.yaml` with secure, unique passwords before applying:
```bash
kubectl apply -f k8s/secrets.yaml
```

### Deploy Database
*Note: For production, it is highly recommended to use a managed database (RDS, Cloud SQL) instead of the local StatefulSet.*
```bash
kubectl apply -f k8s/postgres.yaml
# Wait for DB
kubectl wait --for=condition=ready pod -l app=postgres -n keycloak
```

---

## 3. Application Deployment

### Build and Push Images
You must build your images and push them to a registry (Docker Hub, ECR, etc.) that your cluster can access.

```bash
# Example for Docker Hub
docker build -t your-org/keycloak-custom:latest .
docker push your-org/keycloak-custom:latest

docker build -t your-org/domain-manager:latest ./services/domain-manager
docker push your-org/domain-manager:latest
```

### Update Manifests
Update `k8s/keycloak.yaml` and `k8s/domain-manager.yaml` to use your registry images instead of `latest` or local tags.

### Apply Manifests
```bash
kubectl apply -f k8s/keycloak.yaml
kubectl apply -f k8s/domain-manager.yaml
```

---

## Security Audit: `allow-snippet-annotations`

### Is this a concern?
**Yes, it increases the attack surface.** Enabling snippets allows anyone with "Ingress Creation" permissions in the cluster to inject custom Nginx configuration. 

### How we mitigated it:
1. **RBAC Isolation**: In our deployment (`k8s/domain-manager.yaml`), we use a dedicated `ServiceAccount` and `ClusterRole`. 
2. **Namespace Locking**: Only the `domain-manager` should have permission to create/edit Ingresses in the `keycloak` namespace.
3. **Restricted Access**: Human users and other microservices should NOT have permissions to create Ingresses in the `keycloak` namespace.

### Best Practice:
If you are on a multi-tenant cluster where many developers can create Ingresses, use an [Ingress Admission Webhook](https://kubernetes.github.io/ingress-nginx/user-guide/access-control/#ingress-admission-webhook) or a policy engine like **Kyverno** to only allow specific, pre-approved snippets.
