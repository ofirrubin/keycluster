# Minikube Testing Walkthrough

Follow these steps to test the Keycluster deployment locally using Minikube.

## 1. Prepare Minikube
Ensure Minikube is running and enable the Ingress controller.

```bash
minikube start
minikube addons enable ingress

# IMPORTANT: Enable configuration-snippet annotations in Nginx
# This is required for the realm-domain isolation feature.
kubectl patch configmap ingress-nginx-controller -n ingress-nginx --type merge -p '{"data":{"allow-snippet-annotations":"true"}}'
```

## 2. Build the Custom Image
Since we added a custom theme, we need to build the image inside Minikube's Docker environment.

```bash
# Point your shell to Minikube's Docker daemon
eval $(minikube docker-env)

# Build the images
docker build -t keycloak-custom:latest .
docker build -t domain-manager:latest ./services/domain-manager
```

## 3. Update Manifest to use Local Image
Edit `k8s/keycloak.yaml` to use `keycloak-custom:latest` and set `imagePullPolicy: IfNotPresent`.

## 4. Deploy to Kubernetes
Apply the configuration files.

```bash
kubectl apply -f k8s/secrets.yaml
make deploy-db
# Wait for postgres to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n keycloak --timeout=60s

make deploy-all
# Wait for services to be ready
kubectl wait --for=condition=ready pod -l app=keycloak -n keycloak --timeout=120s
kubectl wait --for=condition=ready pod -l app=domain-manager -n keycloak --timeout=60s
```

## 5. Simulate Multi-Domain Access

### A. Setup Host Mapping
Get the Minikube IP:
```bash
minikube ip
```
Add these entries to your `/etc/hosts` (replace `MINIKUBE_IP` with the output above):
```
MINIKUBE_IP auth.realm-a.com
MINIKUBE_IP auth.realm-b.com
```

### B. Create/Update Ingresses via API
Instead of manual `kubectl` commands, use the Domain Manager API:

```bash
# Create or update a domain
curl -X POST http://localhost:8000/domains \
  -H "Content-Type: application/json" \
  -d '{"realm": "realm-a", "domain": "auth.realm-a.com"}'

# Delete a domain
curl -X DELETE http://localhost:8000/domains/realm-a
```
*(Note: Use port-forwarding to access the Domain Manager locally: `kubectl port-forward svc/domain-manager 8000:80 -n keycloak`)*

## 6. Verify Security Isolation
1.  Go to Keycloak Admin Console (via `kubectl port-forward svc/keycloak 8080:8080 -n keycloak`) and create two realms: `realm-a` and `realm-b`.
2.  Try visiting `http://auth.realm-a.com/realms/realm-a/account`. It should work.
3.  Try visiting `http://auth.realm-a.com/realms/realm-b/account`. It should return a **403 Forbidden** (thanks to our Ingress rules).

## 7. Test Dynamic Theme
1.  In Keycloak Admin for `realm-a`, set the **Login Theme** to `dynamic-standard`.
2.  Since your theme API isn't running yet, the login page will use default values.
3.  To "mock" the API, you can host a simple JSON file locally or use a tool like Mockoon, then update the URL in `theme/dynamic-standard/login/resources/js/theme-injector.js`.

## 8. Updating Services
If you make changes to the code (e.g., updating the Python logic in `domain-manager` or the theme in `Dockerfile`), follow this process:

1. **Rebuild the image**: `docker build -t domain-manager:latest ./services/domain-manager` (ensure you are using `minikube docker-env`).
2. **Restart the pod**: `kubectl rollout restart deployment domain-manager -n keycloak`.

Simply running `kubectl apply` usually **won't work** for `latest` tags because Kubernetes won't detect a change in the manifest.

I've added a shortcut for this in the Makefile:
```bash
make update
```
This will build both images and restart the deployments for you.
