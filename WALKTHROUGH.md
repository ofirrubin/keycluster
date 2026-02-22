# Minikube Testing Walkthrough

Follow these steps to test the Keycluster deployment locally using Minikube.

## 1. Prepare Minikube

Ensure Minikube is running and enable the Ingress controller.

```bash
minikube start
minikube addons enable ingress
```

Path-based isolation works out of the box. If you want to use the legacy
snippet-based isolation (see INSTALLATION.md for details), enable snippet
annotations:

```bash
kubectl patch configmap ingress-nginx-controller -n ingress-nginx \
  --type merge -p '{"data":{"allow-snippet-annotations":"true"}}'
```

## 2. Build the Custom Images

Build the Keycloak and Domain Manager images inside Minikube's Docker
environment.

```bash
# Point your shell to Minikube's Docker daemon
eval $(minikube docker-env)

# Build the images
docker build -t keycloak-custom:latest .
docker build -t domain-manager:latest ./services/domain-manager
```

## 3. Update Manifest to Use Local Image

Edit `k8s/keycloak.yaml` to use `keycloak-custom:latest` and set
`imagePullPolicy: IfNotPresent`.

## 4. Configure Secrets

Edit `k8s/secrets.yaml` and replace all placeholder passwords with strong,
unique values before applying. Never deploy with the default passwords.

## 5. Deploy to Kubernetes

Apply the configuration files.

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
make deploy-db
# Wait for postgres to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n keycloak --timeout=60s

make deploy-all
# Wait for services to be ready
kubectl wait --for=condition=ready pod -l app=keycloak -n keycloak --timeout=120s
kubectl wait --for=condition=ready pod -l app=domain-manager -n keycloak --timeout=60s
```

## 6. Simulate Multi-Domain Access

### A. Setup Host Mapping

Get the Minikube IP:
```bash
minikube ip
```
Add these entries to your `/etc/hosts` (replace `MINIKUBE_IP` with the output
above):
```
MINIKUBE_IP auth.tenant-a.example.com
MINIKUBE_IP auth.tenant-b.example.com
```

### B. Port-Forward the Domain Manager

```bash
kubectl port-forward svc/domain-manager 8000:80 -n keycloak
```

### C. Create/Update Ingresses via API

Use the Domain Manager API (requires a valid admin Bearer token):

```bash
# Create or update a domain
curl -X POST http://localhost:8000/domains \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <admin-token>" \
  -d '{"realm": "tenant-a", "domain": "auth.tenant-a.example.com"}'

# Delete a domain
curl -X DELETE http://localhost:8000/domains/tenant-a \
  -H "Authorization: Bearer <admin-token>"
```

## 7. Verify Security Isolation

1. Access the Keycloak Admin Console:
   ```bash
   kubectl port-forward svc/keycloak 8080:8080 -n keycloak
   ```
2. Open `http://localhost:8080/admin` and create two realms: `tenant-a` and
   `tenant-b`.
3. Visit `http://auth.tenant-a.example.com/realms/tenant-a/account`. It should
   load successfully.
4. Visit `http://auth.tenant-a.example.com/realms/tenant-b/account`. It should
   return a **403 Forbidden** because the Ingress rules restrict cross-realm
   access.

## 8. Test Dynamic Theme

1. In Keycloak Admin for `tenant-a`, set the **Login Theme** to
   `dynamic-standard`.
2. Visit the realm login page. Without a saved theme config, it uses defaults.
3. Save a custom theme via the API:
   ```bash
   curl -X POST http://localhost:8000/v1/themes/tenant-a \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer <admin-token>" \
     -d '{"primaryColor": "#2563eb", "themeMode": "dark"}'
   ```
4. Reload the login page to see the applied theme.

### Theme Test URLs

Once a realm and domain are configured, use URLs like these to test locale and
theme mode combinations (replace the host with your mapped domain):

```
http://auth.tenant-a.example.com/realms/tenant-a/account
http://auth.tenant-a.example.com/realms/tenant-a/account?ui_locales=en
http://auth.tenant-a.example.com/realms/tenant-a/account?ui_locales=he
http://auth.tenant-a.example.com/realms/tenant-a/account?ui_locales=en&ui_theme=light
http://auth.tenant-a.example.com/realms/tenant-a/account?ui_locales=he&ui_theme=dark
http://auth.tenant-a.example.com/realms/tenant-a/account?ui_theme=system
```

## 9. Updating Services

If you make changes to the code (e.g., updating the Python logic in
`domain-manager` or the theme in `Dockerfile`), follow this process:

1. **Rebuild the image**: `docker build -t domain-manager:latest
   ./services/domain-manager` (ensure you are using `minikube docker-env`).
2. **Restart the pod**: `kubectl rollout restart deployment domain-manager -n
   keycloak`.

Running `kubectl apply` usually will not work for `latest` tags because
Kubernetes does not detect a change in the manifest.

A shortcut is available in the Makefile:
```bash
make update
```
This builds both images and restarts the deployments.
