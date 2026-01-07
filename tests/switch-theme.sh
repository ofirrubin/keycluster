#!/bin/bash
THEME=$1
if [ -z "$THEME" ]; then
    echo "Usage: ./tests/switch-theme.sh [dynamic-standard|dynamic-half|dynamic-clean]"
    exit 1
fi

echo "Switching example realm to theme: $THEME"
POD=$(kubectl get pods -l app=keycloak -n keycloak -o jsonpath='{.items[0].metadata.name}')

kubectl exec -it $POD -n keycloak -- /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user admin --password secure-admin-password-replace-me
kubectl exec -it $POD -n keycloak -- /opt/keycloak/bin/kcadm.sh update realms/example -s loginTheme=$THEME -s accountTheme=$THEME

echo "✅ Theme switched to $THEME"
