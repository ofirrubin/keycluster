#!/bin/bash
set -e

echo "🔄 Updating Domain Manager..."

# Ensure we are using minikube docker env
eval $(minikube docker-env)

# Build Domain Manager Image
echo "🐳 Building Domain Manager Image..."
docker build -t domain-manager:latest ./services/domain-manager

# Restart Deployment
echo "♻️  Restarting Domain Manager..."
kubectl rollout restart deployment domain-manager -n keycloak

# Wait for rollout
echo "⏳ Waiting for Domain Manager deployment..."
kubectl rollout status deployment/domain-manager -n keycloak

echo "✅ Domain Manager updated."
