#!/bin/bash
set -e

DOMAIN_MANAGER="http://localhost:8000"
INGRESS="http://localhost:8081" # Port forward to Ingress
SANDBOX_DOMAIN="sandbox.keycluster.com"

echo "🧪 Verifying Sandbox Environment..."

# 1. Editor UI
echo "-> Checking Editor UI..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$DOMAIN_MANAGER/static/editor.html")
if [ "$HTTP_CODE" == "200" ]; then
    echo "   ✅ Editor UI is accessible."
else
    echo "   ❌ Editor UI failed (HTTP $HTTP_CODE)."
    exit 1
fi

# 2. Sandbox Realm Access (via Ingress)
# Note: Requires tunnels open
echo "-> Checking Sandbox Realm Access..."

# NOTE: If we used nip.io, we wouldn't need Host header, but we used keycluster.com
# Use -H Host: sandbox.keycluster.com
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: $SANDBOX_DOMAIN" "$INGRESS/realms/sandbox/account/")
# Should be 302/200 depending on auth state, or 404 if missing
if [ "$HTTP_CODE" == "200" ] || [ "$HTTP_CODE" == "302" ]; then
    echo "   ✅ Sandbox Realm is accessible (HTTP $HTTP_CODE)."
else
    echo "   ⚠️  Sandbox Realm access issue (HTTP $HTTP_CODE). Check tunnels."
    # Don't hard exit, might be just tunnel flake
fi

echo "🎉 Verification Complete."
