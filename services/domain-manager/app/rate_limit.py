"""Shared rate-limiter instance for all route modules.

slowapi is a hard dependency. If the import fails the service must not start,
because rate limiting is a security control that must never silently degrade.

PRODUCTION REQUIREMENT: This service MUST be deployed behind a trusted reverse
proxy (e.g., Nginx Ingress Controller) that sets the X-Forwarded-For header.
Configure TRUSTED_PROXY_CIDRS with the pod/service CIDR of the ingress
controller so that rate limiting keys on the real client IP instead of the
proxy IP. Without this, all requests appear to come from the same IP and a
single abusive client can exhaust the rate limit for all users.
"""

import ipaddress
import logging
import os

from fastapi import Request
from slowapi import Limiter

logger = logging.getLogger(__name__)

# Trusted proxy CIDRs — only the first-hop reverse proxy (Nginx Ingress)
# should be trusted to set X-Forwarded-For. In Kubernetes, this is the
# pod/service CIDR of the ingress controller.
_TRUSTED_PROXIES_RAW: str = os.getenv("TRUSTED_PROXY_CIDRS", "")
_TRUSTED_PROXY_NETWORKS = []
for cidr in _TRUSTED_PROXIES_RAW.split(","):
    cidr = cidr.strip()
    if cidr:
        try:
            _TRUSTED_PROXY_NETWORKS.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("Invalid TRUSTED_PROXY_CIDRS entry: length=%d", len(cidr))


def _get_real_client_ip(request: Request) -> str:
    """Extract the real client IP from the request.

    When the direct peer (request.client.host) is a trusted proxy, extract the
    client IP from the rightmost untrusted entry in X-Forwarded-For. This is
    the standard approach for Nginx Ingress which appends the real client IP.

    If no trusted proxies are configured or the peer is not trusted, falls back
    to request.client.host (safe default — never trusts X-Forwarded-For from
    untrusted sources).
    """
    client_host = request.client.host if request.client else "127.0.0.1"

    if not _TRUSTED_PROXY_NETWORKS:
        return client_host

    # Check if the direct connection is from a trusted proxy
    try:
        peer_addr = ipaddress.ip_address(client_host)
    except ValueError:
        return client_host

    is_trusted_peer = any(
        peer_addr in network for network in _TRUSTED_PROXY_NETWORKS
    )
    if not is_trusted_peer:
        return client_host

    # Peer is trusted — extract from X-Forwarded-For
    xff = request.headers.get("X-Forwarded-For", "")
    if not xff:
        return client_host

    # X-Forwarded-For is a comma-separated list: client, proxy1, proxy2, ...
    # The rightmost entry added by our trusted proxy is the real client IP.
    # Walk from right to left, skipping trusted proxy IPs, and return the
    # first non-trusted IP.
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    for candidate in reversed(parts):
        try:
            candidate_addr = ipaddress.ip_address(candidate)
        except ValueError:
            # Malformed entry — stop walking and return it as-is for rate
            # limiting (attacker-controlled but unique per request).
            return candidate
        if not any(candidate_addr in net for net in _TRUSTED_PROXY_NETWORKS):
            return candidate

    # All XFF entries are trusted proxies — fall back to direct peer
    return client_host


limiter = Limiter(key_func=_get_real_client_ip)
