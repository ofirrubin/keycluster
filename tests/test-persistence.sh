#!/bin/bash

# Test Persistence of Domain Manager
# 1. Create a domain mapping
# 2. Verify it exists
# 3. Restart the pod
# 4. Verify it still exists

REALM="persistence-test-$(date +%s)"
DOMAIN="persistence.keycluster.com"
API_URL="http://localhost:8000"

echo "🧪 Starting Persistence Test..."

# 1. Create
echo "   -> Creating domain mapping for realm '$REALM'..."
curl -s -X POST "$API_URL/domains" \
    -H "Content-Type: application/json" \
    -d "{\"realm\": \"$REALM\", \"domain\": \"$DOMAIN\", \"enabled\": true}" | grep "synced" > /dev/null

if [ $? -eq 0 ]; then
    echo "   ✅ Domain created."
else
    echo "   ❌ Failed to create domain."
    exit 1
fi

# 2. Restart Pod
echo "   -> Restarting domain-manager model..."
kubectl rollout restart deployment domain-manager -n keycloak > /dev/null
kubectl rollout status deployment domain-manager -n keycloak --timeout=60s > /dev/null

# 3. Wait for startup
echo "   -> Waiting for API to be available..."
sleep 5
until curl -s -f "$API_URL/health" > /dev/null; do
    echo "      ...waiting"
    sleep 2
done

# 4. Verify Persistence (For now, we try to create again, if logic works it should update, not error)
# Ideally we'd have a GET endpoint, but Sync handles existence.
# Let's check if we can delete it (which requires finding it in DB)
echo "   -> Verifying persistence by attempting deletion..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$API_URL/domains/$REALM")

if [ "$HTTP_CODE" -eq 200 ]; then
    echo "   ✅ Persistence Verified! Domain was found and deleted after restart."
else
    echo "   ❌ Verification Failed. HTTP Code: $HTTP_CODE"
    exit 1
fi

echo "🎉 Test Passed."
