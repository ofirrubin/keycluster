FROM quay.io/keycloak/keycloak:26.0.0 AS builder

# Enable health and metrics support
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true

# Configure a database vendor
ENV KC_DB=postgres

WORKDIR /opt/keycloak
# Copy custom theme
COPY theme/ /opt/keycloak/themes/

RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.0.0
COPY --from=builder /opt/keycloak/ /opt/keycloak/

# Default to start-dev for flexibility, but manifest can override
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start-dev"]
