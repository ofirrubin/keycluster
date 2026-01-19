#!/bin/bash

# Full Integration Test for Keycluster
# This script tests:
# 1. Domain Manager connectivity and Ingress creation.
# 2. Keycloak Realm creation via API.
# 3. Security Isolation (Path-based blocking).
# 4. Connectivity via Simulation (Host Headers).

set -e

# Configuration
NAMESPACE="keycloak"
DOMAIN_MANAGER_URL="http://localhost:8000"
KEYCLOAK_URL="http://localhost:8080"
TEST_REALM="test-service-$(date +%s)"
TEST_DOMAIN="test.keycluster.com"
ADMIN_USER="admin"
ADMIN_PASS="secure-admin-password-replace-me" # Update if you changed secrets.yaml

# Helper: Check if service is reachable
check_service() {
    local url=$1
    local name=$2
    if ! curl -s --connect-timeout 2 "$url" > /dev/null; then
        echo "❌ $name is not reachable at $url."
        echo "   Please run: kubectl port-forward svc/$name 8000:80 -n $NAMESPACE (for domain-manager)"
        echo "   And: kubectl port-forward svc/keycloak 8080:8080 -n $NAMESPACE (for keycloak)"
        exit 1
    fi
}

echo "🚀 Starting Full Integration Test..."

# 1. Verify environment
check_service "$DOMAIN_MANAGER_URL/health" "domain-manager"
check_service "$KEYCLOAK_URL/health/live" "keycloak"

# 2. Register Domain Mapping via API
echo "Step 1: Registering domain mapping..."
curl -s -X POST "$DOMAIN_MANAGER_URL/domains" \
    -H "Content-Type: application/json" \
    -d "{\"realm\": \"$TEST_REALM\", \"domain\": \"$TEST_DOMAIN\"}" | jq .

# 3. Get Keycloak Admin Token
echo "Step 2: Authenticating with Keycloak..."
TOKEN=$(curl -s -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "username=$ADMIN_USER" \
    -d "password=$ADMIN_PASS" \
    -d "grant_type=password" | jq -r .access_token)

if [ "$TOKEN" == "null" ]; then
    echo "❌ Failed to get admin token. Check credentials."
    exit 1
fi
echo "✅ Authenticated."

# 4. Create New Realm
echo "Step 3: Creating test realm '$TEST_REALM'..."
curl -s -X POST "$KEYCLOAK_URL/admin/realms" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"realm\": \"$TEST_REALM\", \"enabled\": true}"
echo "✅ Realm created."

# 5. Verify Connectivity & Security
# Configuration for connection
# Try 127.0.0.1:8081 (port-forward to nginx) first, then tunnel/minikube ip
TARGET_URL="http://127.0.0.1:8081"

echo "Step 4: Verifying Ingress isolation..."

if ! curl -s --connect-timeout 2 "$TARGET_URL" > /dev/null 2>&1 && [ "$TARGET_URL" == "http://127.0.0.1:8081" ]; then
    echo "⚠️  Port 8081 (Ingress Forward) not found."
    MINIKUBE_IP=$(minikube ip)
    if ping -c 1 -W 1 "$MINIKUBE_IP" > /dev/null 2>&1; then
        TARGET_URL="http://$MINIKUBE_IP"
    else
        echo "⚠️  Minikube IP ($MINIKUBE_IP) unreachable. Assuming 'minikube tunnel' on 127.0.0.1:80"
        TARGET_URL="http://127.0.0.1"
    fi
fi

echo "   Testing valid path (/realms/$TEST_REALM) via $TARGET_URL..."
CODE_VALID=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 -H "Host: $TEST_DOMAIN" "$TARGET_URL/realms/$TEST_REALM")
if [ "$CODE_VALID" == "200" ]; then
    echo "   ✅ SUCCESS: Realm is accessible via domain."
else
    echo "   ❌ FAILURE: Got HTTP $CODE_VALID"
    echo "   Try running: kubectl port-forward svc/ingress-nginx-controller 8081:80 -n ingress-nginx"
    exit 1
fi

echo "   Testing blocked path (/realms/master)..."
CODE_BLOCKED=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 -H "Host: $TEST_DOMAIN" "$TARGET_URL/realms/master")
if [ "$CODE_BLOCKED" == "404" ] || [ "$CODE_BLOCKED" == "401" ] || [ "$CODE_BLOCKED" == "403" ]; then
    echo "   ✅ SUCCESS: Master realm is blocked or requires auth."
else
    echo "   ❌ FAILURE: Master realm is accessible (Got HTTP $CODE_BLOCKED)!"
fi

echo "   Testing theme API (/v1/themes/$TEST_REALM)..."
THEME_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 -H "Host: $TEST_DOMAIN" "$TARGET_URL/v1/themes/$TEST_REALM")
if [ "$THEME_CODE" == "200" ]; then
    echo "   ✅ SUCCESS: Theme API is accessible."
else
    echo "   ❌ FAILURE: Theme API returned HTTP $THEME_CODE"
    exit 1
fi

echo "---"
echo "🎉 Integration Test Complete!"
echo "To clean up:"
echo "curl -X DELETE $DOMAIN_MANAGER_URL/domains/$TEST_REALM"
