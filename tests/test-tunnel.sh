#!/bin/bash

# Port-forwarding script for local Keycluster testing
# This opens the three necessary tunnels to interact with the cluster from your host.

echo "🔌 Opening tunnels to Keycluster..."

# Function to kill all backgrounds on exit
cleanup() {
    echo -e "\n🛑 Closing tunnels..."
    kill $(jobs -p)
    exit
}

trap cleanup SIGINT SIGTERM

# 1. Domain Manager (Management API)
echo "   -> Port 8000: Domain Manager"
kubectl port-forward svc/domain-manager 8000:80 -n keycloak > /dev/null 2>&1 &

# 2. Keycloak (Admin API)
echo "   -> Port 8080: Keycloak"
kubectl port-forward svc/keycloak 8080:8080 -n keycloak > /dev/null 2>&1 &

# 3. Ingress Console (Traffic Verification)
echo "   -> Port 8081: Ingress (Nginx)"
kubectl port-forward svc/ingress-nginx-controller 8081:80 -n ingress-nginx > /dev/null 2>&1 &

echo "✅ All tunnels open. Press Ctrl+C to stop."
echo "You can now run: ./tests/integration_test.sh"

# Keep script alive
wait
