#!/bin/bash

# Configuration
# Run minikube ip to get the IP
MINIKUBE_IP=$(minikube ip)
echo "Minikube IP: $MINIKUBE_IP"

# Test Realm A
echo "--- Testing realm-a domain ---"
# Accessible path
curl -I -v -H "Host: auth.realm-a.com" "http://$MINIKUBE_IP/realms/realm-a" 2>&1 | grep "HTTP/"
# Forbidden path (should 404 since no ingress rule matches)
curl -I -v -H "Host: auth.realm-a.com" "http://$MINIKUBE_IP/realms/realm-b" 2>&1 | grep "HTTP/"

echo "--- Testing Static Resource Isolation ---"
# Should allow own theme
curl -I -v -H "Host: auth.realm-a.com" "http://$MINIKUBE_IP/resources/123/login/dynamic-standard" 2>&1 | grep "HTTP/"
# Should block other themes (assuming 'other-theme' and 'realm-a' is assigned 'dynamic-standard')
curl -I -v -H "Host: auth.realm-a.com" "http://$MINIKUBE_IP/resources/123/login/other-theme" 2>&1 | grep "HTTP/"

echo "--- Testing Path Traversal Protection ---"
# Should be blocked or normalized by Nginx
curl -I -v -H "Host: auth.realm-a.com" "http://$MINIKUBE_IP/realms/realm-a/..//..//master" 2>&1 | grep "HTTP/"
