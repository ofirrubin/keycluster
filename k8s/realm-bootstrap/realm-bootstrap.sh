#!/usr/bin/env bash
#
# realm-bootstrap.sh -- idempotent full seed of a dedicated Keycloak realm.
#
# Runs against the Keycloak Admin REST API and ensures EVERYTHING a fresh
# eCommerce-style tenant needs for a working admin login on the FIRST try:
#   * the realm (reused if it already exists)
#   * a confidential server client (standardFlow + serviceAccounts) with the
#     correct redirectUris / webOrigins / post.logout.redirect.uris
#   * customer + admin public clients (PKCE S256) with the same URI hardening
#   * a realm-roles -> ID-token protocol mapper on every client so
#     `realm_access.roles` lands in the ID token (the server reads roles there)
#   * the `admin` and `realm-admin` realm roles
#   * the admin user (with password) and the realm roles assigned to it
#   * the confidential server client secret, written back to a K8s Secret
#     <realm>-auth (key SERVER_CLIENT_SECRET) in the target namespace
#
# Everything is idempotent: re-running converges state without duplicating.
# Secrets are never echoed.
#
# Required environment (all parameterized -- no hardcoded hosts/realms/ids):
#   KC_URL                 e.g. http://keycloak.keycloak.svc.cluster.local:8080
#   KC_ADMIN_USER          Keycloak master-realm admin username
#   KC_ADMIN_PASSWORD      Keycloak master-realm admin password
#   REALM                  target realm name (e.g. ecommerce)
#   APP_ADMIN_USER         realm admin username to seed (e.g. admin)
#   APP_ADMIN_PASSWORD     realm admin password to seed
#   APP_ADMIN_EMAIL        (optional) admin email
#   APP_ADMIN_FIRST_NAME   (optional) admin first name
#   APP_ADMIN_LAST_NAME    (optional) admin last name
#   SERVER_CLIENT_ID       confidential server client id (e.g. ecommerce-server)
#   CUSTOMER_CLIENT_ID     public storefront client id  (e.g. ecommerce-customer)
#   ADMIN_CLIENT_ID        public admin client id       (e.g. ecommerce-admin)
#   STOREFRONT_ORIGIN      storefront origin URL (scheme://host[:port])
#   API_ORIGIN             api origin URL
#   ADMIN_ORIGIN           admin dashboard origin URL
#   TARGET_NAMESPACE       namespace to write the <realm>-auth Secret into
#
# Optional:
#   REALM_ROLES            space/comma list of realm roles to ensure+assign
#                          (default: "admin realm-admin")
#   SERVER_SA_REALM_MGMT_ROLES
#                          realm-management CLIENT roles granted to the server
#                          client's service account so the app can query/manage
#                          realm users via the admin API. Default is least-
#                          privilege for the eCommerce server, which both reads
#                          and writes users:
#                          "view-users query-users query-groups view-realm manage-users".
#                          For a read-only app, drop manage-users.
#
set -euo pipefail

log() { printf '%s %s\n' "[realm-bootstrap]" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

for v in KC_URL KC_ADMIN_USER KC_ADMIN_PASSWORD REALM \
         APP_ADMIN_USER APP_ADMIN_PASSWORD \
         SERVER_CLIENT_ID CUSTOMER_CLIENT_ID ADMIN_CLIENT_ID \
         STOREFRONT_ORIGIN API_ORIGIN ADMIN_ORIGIN TARGET_NAMESPACE; do
  [ -n "${!v:-}" ] || die "missing required env var: $v"
done

APP_ADMIN_EMAIL="${APP_ADMIN_EMAIL:-}"
APP_ADMIN_FIRST_NAME="${APP_ADMIN_FIRST_NAME:-}"
APP_ADMIN_LAST_NAME="${APP_ADMIN_LAST_NAME:-}"
REALM_ROLES="${REALM_ROLES:-admin realm-admin}"
REALM_ROLES="${REALM_ROLES//,/ }"

command -v curl >/dev/null || die "curl not found"
command -v jq   >/dev/null || die "jq not found"
command -v kubectl >/dev/null || die "kubectl not found"

KC_URL="${KC_URL%/}"

# --- helpers ---------------------------------------------------------------

# kc <METHOD> <path> [json-body] -> stdout is response body; sets KC_HTTP_CODE
KC_HTTP_CODE=0
kc() {
  local method="$1" path="$2" body="${3:-}" out code
  local tmp; tmp="$(mktemp)"
  if [ -n "$body" ]; then
    code="$(curl -sS -o "$tmp" -w '%{http_code}' -X "$method" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "$body" "${KC_URL}${path}")"
  else
    code="$(curl -sS -o "$tmp" -w '%{http_code}' -X "$method" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      "${KC_URL}${path}")"
  fi
  KC_HTTP_CODE="$code"
  cat "$tmp"; rm -f "$tmp"
}

get_token() {
  log "authenticating to Keycloak admin API"
  local resp
  resp="$(curl -sS -X POST \
    "${KC_URL}/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=password" \
    --data-urlencode "client_id=admin-cli" \
    --data-urlencode "username=${KC_ADMIN_USER}" \
    --data-urlencode "password=${KC_ADMIN_PASSWORD}")"
  ACCESS_TOKEN="$(printf '%s' "$resp" | jq -r '.access_token // empty')"
  [ -n "$ACCESS_TOKEN" ] || die "failed to obtain admin token (check KC_URL/creds)"
}

# wait for Keycloak to be reachable
wait_ready() {
  local i
  for i in $(seq 1 60); do
    if curl -sf -o /dev/null "${KC_URL}/realms/master/.well-known/openid-configuration"; then
      return 0
    fi
    log "waiting for Keycloak to be ready ($i)"
    sleep 5
  done
  die "Keycloak not reachable at ${KC_URL}"
}

json_escape() { jq -Rn --arg s "$1" '$s'; }

# --- realm -----------------------------------------------------------------

ensure_realm() {
  kc GET "/admin/realms/${REALM}" >/dev/null
  if [ "$KC_HTTP_CODE" = "200" ]; then
    log "realm '${REALM}' exists -- reusing"
    return 0
  fi
  log "creating realm '${REALM}'"
  local body
  body="$(jq -n --arg r "$REALM" \
    '{realm:$r, enabled:true, sslRequired:"external",
      registrationAllowed:false, loginWithEmailAllowed:true,
      duplicateEmailsAllowed:false}')"
  kc POST "/admin/realms" "$body" >/dev/null
  [ "$KC_HTTP_CODE" = "201" ] || die "realm create failed (HTTP $KC_HTTP_CODE)"
}

# --- realm roles -----------------------------------------------------------

ensure_realm_role() {
  local role="$1"
  kc GET "/admin/realms/${REALM}/roles/${role}" >/dev/null
  if [ "$KC_HTTP_CODE" = "200" ]; then
    log "realm role '${role}' exists"
    return 0
  fi
  log "creating realm role '${role}'"
  kc POST "/admin/realms/${REALM}/roles" \
    "$(jq -n --arg n "$role" '{name:$n}')" >/dev/null
  [ "$KC_HTTP_CODE" = "201" ] || die "role '${role}' create failed (HTTP $KC_HTTP_CODE)"
}

# --- clients ---------------------------------------------------------------

# find client uuid by clientId (empty if not found)
client_uuid() {
  kc GET "/admin/realms/${REALM}/clients?clientId=$1" \
    | jq -r '.[0].id // empty'
}

# realm-roles -> ID token mapper, added to a client (idempotent)
ensure_role_mapper() {
  local cuuid="$1"
  local existing
  existing="$(kc GET "/admin/realms/${REALM}/clients/${cuuid}/protocol-mappers/models" \
    | jq -r '.[] | select(.protocolMapper=="oidc-usermodel-realm-role-mapper") | .id' | head -n1)"
  local mapper
  mapper="$(jq -n '{
    name:"realm-roles",
    protocol:"openid-connect",
    protocolMapper:"oidc-usermodel-realm-role-mapper",
    consentRequired:false,
    config:{
      "claim.name":"realm_access.roles",
      "jsonType.label":"String",
      "multivalued":"true",
      "id.token.claim":"true",
      "access.token.claim":"true",
      "userinfo.token.claim":"true"
    }}')"
  if [ -n "$existing" ]; then
    kc PUT "/admin/realms/${REALM}/clients/${cuuid}/protocol-mappers/models/${existing}" \
      "$(printf '%s' "$mapper" | jq --arg id "$existing" '. + {id:$id}')" >/dev/null
    log "  updated realm-roles ID-token mapper"
  else
    kc POST "/admin/realms/${REALM}/clients/${cuuid}/protocol-mappers/models" "$mapper" >/dev/null
    [ "$KC_HTTP_CODE" = "201" ] || die "mapper create failed (HTTP $KC_HTTP_CODE)"
    log "  created realm-roles ID-token mapper"
  fi
}

# ensure_client <clientId> <confidential:true|false>
ensure_client() {
  local cid="$1" confidential="$2"
  local redirect_uris web_origins post_logout
  # redirectUris cover every app origin + wildcard; webOrigins likewise.
  redirect_uris="$(jq -n \
    --arg s "$STOREFRONT_ORIGIN" --arg a "$API_ORIGIN" --arg d "$ADMIN_ORIGIN" \
    '[$s, ($s+"/*"), $a, ($a+"/*"), $d, ($d+"/*")] | unique')"
  web_origins="$(jq -n \
    --arg s "$STOREFRONT_ORIGIN" --arg a "$API_ORIGIN" --arg d "$ADMIN_ORIGIN" \
    '[$s, $a, $d] | unique')"
  # post.logout.redirect.uris is a single ## delimited string
  post_logout="$(printf '%s##%s##%s##%s##%s##%s' \
    "$STOREFRONT_ORIGIN" "${STOREFRONT_ORIGIN}/*" \
    "$ADMIN_ORIGIN" "${ADMIN_ORIGIN}/*" \
    "$API_ORIGIN" "${API_ORIGIN}/*")"

  local payload
  if [ "$confidential" = "true" ]; then
    payload="$(jq -n \
      --arg cid "$cid" --argjson ru "$redirect_uris" --argjson wo "$web_origins" \
      --arg pl "$post_logout" '{
        clientId:$cid, enabled:true, protocol:"openid-connect",
        publicClient:false, standardFlowEnabled:true,
        directAccessGrantsEnabled:false, serviceAccountsEnabled:true,
        redirectUris:$ru, webOrigins:$wo,
        attributes:{"post.logout.redirect.uris":$pl}}')"
  else
    payload="$(jq -n \
      --arg cid "$cid" --argjson ru "$redirect_uris" --argjson wo "$web_origins" \
      --arg pl "$post_logout" '{
        clientId:$cid, enabled:true, protocol:"openid-connect",
        publicClient:true, standardFlowEnabled:true,
        directAccessGrantsEnabled:false, serviceAccountsEnabled:false,
        redirectUris:$ru, webOrigins:$wo,
        attributes:{"pkce.code.challenge.method":"S256",
                    "post.logout.redirect.uris":$pl}}')"
  fi

  local cuuid
  cuuid="$(client_uuid "$cid")"
  if [ -n "$cuuid" ]; then
    log "client '${cid}' exists -- updating URIs/flags"
    kc PUT "/admin/realms/${REALM}/clients/${cuuid}" \
      "$(printf '%s' "$payload" | jq --arg id "$cuuid" '. + {id:$id}')" >/dev/null
    [ "$KC_HTTP_CODE" = "204" ] || die "client '${cid}' update failed (HTTP $KC_HTTP_CODE)"
  else
    log "creating client '${cid}' (confidential=${confidential})"
    kc POST "/admin/realms/${REALM}/clients" "$payload" >/dev/null
    [ "$KC_HTTP_CODE" = "201" ] || die "client '${cid}' create failed (HTTP $KC_HTTP_CODE)"
    cuuid="$(client_uuid "$cid")"
  fi
  [ -n "$cuuid" ] || die "could not resolve uuid for client '${cid}'"
  ensure_role_mapper "$cuuid"
  printf '%s' "$cuuid"
}

# --- admin user ------------------------------------------------------------

ensure_admin_user() {
  local uid
  uid="$(kc GET "/admin/realms/${REALM}/users?username=${APP_ADMIN_USER}&exact=true" \
    | jq -r '.[0].id // empty')"
  local userbody
  userbody="$(jq -n \
    --arg u "$APP_ADMIN_USER" --arg e "$APP_ADMIN_EMAIL" \
    --arg f "$APP_ADMIN_FIRST_NAME" --arg l "$APP_ADMIN_LAST_NAME" '{
      username:$u, enabled:true, emailVerified:true}
      + (if $e != "" then {email:$e} else {} end)
      + (if $f != "" then {firstName:$f} else {} end)
      + (if $l != "" then {lastName:$l} else {} end)')"
  if [ -z "$uid" ]; then
    log "creating admin user '${APP_ADMIN_USER}'"
    kc POST "/admin/realms/${REALM}/users" "$userbody" >/dev/null
    [ "$KC_HTTP_CODE" = "201" ] || die "user create failed (HTTP $KC_HTTP_CODE)"
    uid="$(kc GET "/admin/realms/${REALM}/users?username=${APP_ADMIN_USER}&exact=true" \
      | jq -r '.[0].id // empty')"
  else
    log "admin user '${APP_ADMIN_USER}' exists -- updating profile"
    kc PUT "/admin/realms/${REALM}/users/${uid}" "$userbody" >/dev/null
  fi
  [ -n "$uid" ] || die "could not resolve admin user id"

  # set password (idempotent: reset-password is safe to repeat)
  log "setting admin password (value not logged)"
  kc PUT "/admin/realms/${REALM}/users/${uid}/reset-password" \
    "$(jq -n --arg p "$APP_ADMIN_PASSWORD" \
      '{type:"password", temporary:false, value:$p}')" >/dev/null
  [ "$KC_HTTP_CODE" = "204" ] || die "password set failed (HTTP $KC_HTTP_CODE)"

  # assign realm roles
  local role rolejson
  for role in $REALM_ROLES; do
    rolejson="$(kc GET "/admin/realms/${REALM}/roles/${role}")"
    kc POST "/admin/realms/${REALM}/users/${uid}/role-mappings/realm" \
      "$(printf '%s' "$rolejson" | jq '[ {id, name} ]')" >/dev/null
    log "assigned realm role '${role}' to admin user"
  done
}

# --- server SA: realm-management client roles ------------------------------
#
# The confidential server client queries/manages realm users via the Keycloak
# admin API using its service-account (client_credentials) token. That SA has no
# realm-management roles by default, so users.find()/create()/update()/del()
# return 403 and the dashboard Users page 500s. Grant the SA the minimal set of
# realm-management CLIENT roles. The eCommerce server's users routes both READ
# (find/findOne/listCompositeRealmRoleMappings) and WRITE (create/update/del +
# add/del realm role mappings), so manage-users is required in addition to the
# read roles. Override with SERVER_SA_REALM_MGMT_ROLES for a read-only app.
SERVER_SA_REALM_MGMT_ROLES="${SERVER_SA_REALM_MGMT_ROLES:-view-users query-users query-groups view-realm manage-users}"

grant_server_sa_realm_management() {
  local server_uuid="$1"

  # service-account user of the server client
  local sa_uid
  sa_uid="$(kc GET "/admin/realms/${REALM}/clients/${server_uuid}/service-account-user" \
    | jq -r '.id // empty')"
  [ -n "$sa_uid" ] || die "server client has no service-account user (serviceAccountsEnabled?)"

  # realm-management client uuid
  local rm_uuid
  rm_uuid="$(client_uuid "realm-management")"
  [ -n "$rm_uuid" ] || die "realm-management client not found in realm '${REALM}'"

  # roles already assigned to the SA from realm-management (for idempotency)
  local assigned
  assigned="$(kc GET "/admin/realms/${REALM}/users/${sa_uid}/role-mappings/clients/${rm_uuid}" \
    | jq -r '.[].name')"

  local want="$SERVER_SA_REALM_MGMT_ROLES"
  want="${want//,/ }"

  local role rolejson missing_json="[]" already
  for role in $want; do
    already="$(printf '%s\n' "$assigned" | grep -Fxq "$role" && echo yes || echo no)"
    if [ "$already" = "yes" ]; then
      log "server SA already has realm-management role '${role}'"
      continue
    fi
    rolejson="$(kc GET "/admin/realms/${REALM}/clients/${rm_uuid}/roles/${role}")"
    if [ "$KC_HTTP_CODE" != "200" ]; then
      die "realm-management role '${role}' not found (HTTP $KC_HTTP_CODE)"
    fi
    missing_json="$(jq -n --argjson acc "$missing_json" --argjson r "$rolejson" \
      '$acc + [ {id:$r.id, name:$r.name} ]')"
  done

  if [ "$(printf '%s' "$missing_json" | jq 'length')" = "0" ]; then
    log "server SA realm-management roles already complete"
    return 0
  fi
  kc POST "/admin/realms/${REALM}/users/${sa_uid}/role-mappings/clients/${rm_uuid}" \
    "$missing_json" >/dev/null
  [ "$KC_HTTP_CODE" = "204" ] || die "server SA role assignment failed (HTTP $KC_HTTP_CODE)"
  log "granted server SA realm-management roles: $(printf '%s' "$missing_json" | jq -r '[.[].name]|join(", ")')"
}

# --- server client secret -> K8s Secret ------------------------------------

write_server_secret() {
  local cuuid="$1" secret
  secret="$(kc GET "/admin/realms/${REALM}/clients/${cuuid}/client-secret" \
    | jq -r '.value // empty')"
  if [ -z "$secret" ]; then
    secret="$(kc POST "/admin/realms/${REALM}/clients/${cuuid}/client-secret" "" \
      | jq -r '.value // empty')"
  fi
  [ -n "$secret" ] || die "could not obtain server client secret"

  log "writing Secret '${REALM}-auth' (SERVER_CLIENT_SECRET) into ns '${TARGET_NAMESPACE}' (value not logged)"
  kubectl create secret generic "${REALM}-auth" \
    --namespace "${TARGET_NAMESPACE}" \
    --from-literal=SERVER_CLIENT_SECRET="${secret}" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

# --- main ------------------------------------------------------------------

main() {
  wait_ready
  get_token
  ensure_realm

  local role
  for role in $REALM_ROLES; do ensure_realm_role "$role"; done

  local server_uuid
  server_uuid="$(ensure_client "$SERVER_CLIENT_ID" true)"
  ensure_client "$CUSTOMER_CLIENT_ID" false >/dev/null
  ensure_client "$ADMIN_CLIENT_ID" false >/dev/null

  ensure_admin_user
  grant_server_sa_realm_management "$server_uuid"
  write_server_secret "$server_uuid"

  log "realm bootstrap complete for '${REALM}'"
}

main "$@"
