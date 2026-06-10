#!/usr/bin/env bash
#
# render-job.sh -- produce a single apply-able realm-bootstrap manifest.
#
# It (a) packages realm-bootstrap.sh into the `realm-bootstrap-script`
# ConfigMap, (b) substitutes the TARGET_NAMESPACE placeholder in the RBAC
# objects, and (c) concatenates the Job manifest. Output goes to stdout.
#
# Usage:
#   TARGET_NAMESPACE=tenant-acme ./render-job.sh > /tmp/realm-bootstrap.rendered.yaml
#   emerge apply <clusterId> /tmp/realm-bootstrap.rendered.yaml
#
# Non-secret params (REALM, *_CLIENT_ID, *_ORIGIN, APP_ADMIN_*) are still edited
# in realm-bootstrap-job.yaml's ConfigMap, or override after render. Only the
# TARGET_NAMESPACE wiring is templated here because it appears in RBAC metadata.
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${TARGET_NAMESPACE:?set TARGET_NAMESPACE (the tenant app namespace)}"

# 1. script ConfigMap (generated from the canonical script file)
kubectl create configmap realm-bootstrap-script \
  --namespace keycloak \
  --from-file=realm-bootstrap.sh="${HERE}/realm-bootstrap.sh" \
  --dry-run=client -o yaml

echo "---"

# 2. Job + RBAC + params, with TARGET_NAMESPACE substituted.
sed "s/TARGET_NAMESPACE/${TARGET_NAMESPACE}/g" "${HERE}/realm-bootstrap-job.yaml"
