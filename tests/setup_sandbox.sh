#!/bin/bash
set -e

# Config
KEYCLOAK_URL="http://localhost:8080"
DOMAIN_MANAGER_URL="http://localhost:8000"
ADMIN_USER="admin"
ADMIN_PASS="secure-admin-password-replace-me"
SANDBOX_REALM="sandbox"
SANDBOX_DOMAIN="sandbox.keycluster.com"
DEMO_USER="demo"
DEMO_PASS="password"

echo "🚀 Setting up Dynamic Theme Sandbox..."

# 1. Get Admin Token
echo "-> Authenticating as Admin..."
TOKEN=$(curl -s -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "username=$ADMIN_USER" \
    -d "password=$ADMIN_PASS" \
    -d "grant_type=password" | jq -r .access_token)

if [ "$TOKEN" == "null" ]; then
    echo "❌ Failed to get admin token."
    exit 1
fi

# 2. Create Sandbox Realm (if not exists)
echo "-> Creating Realm '$SANDBOX_REALM'..."
# Check existence
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" "$KEYCLOAK_URL/admin/realms/$SANDBOX_REALM")

if [ "$HTTP_CODE" == "404" ]; then
    curl -s -X POST "$KEYCLOAK_URL/admin/realms" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"realm\": \"$SANDBOX_REALM\", \"enabled\": true, \"registrationAllowed\": false}"
    echo "   ✅ Realm created."
else
    echo "   ⚠️  Realm already exists."
fi

# 3. Create Demo User
echo "-> Creating User '$DEMO_USER'..."
# Search for user to see if exists
USER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" "$KEYCLOAK_URL/admin/realms/$SANDBOX_REALM/users?username=$DEMO_USER" | jq -r '.[0].id')

if [ "$USER_ID" == "null" ]; then
    # Create User
    curl -s -X POST "$KEYCLOAK_URL/admin/realms/$SANDBOX_REALM/users" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"$DEMO_USER\", \"enabled\": true, \"emailVerified\": true, \"firstName\": \"Demo\", \"lastName\": \"User\", \"credentials\": [{\"type\": \"password\", \"value\": \"$DEMO_PASS\", \"temporary\": false}]}"
    echo "   ✅ User created."
else
    echo "   ⚠️  User already exists."
fi

# 4. Map Domain
echo "-> Mapping Domain '$SANDBOX_DOMAIN'..."
curl -s -X POST "$DOMAIN_MANAGER_URL/domains" \
    -H "Content-Type: application/json" \
    -d "{\"realm\": \"$SANDBOX_REALM\", \"domain\": \"$SANDBOX_DOMAIN\"}" | jq .

echo "🎉 Sandbox Setup Complete!"
echo "   Login at: http://$SANDBOX_DOMAIN/realms/$SANDBOX_REALM/account (via Ingress)"
echo "   User: $DEMO_USER / $DEMO_PASS"
