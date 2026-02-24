"""Shared rate-limiter instance for all route modules.

slowapi is a hard dependency. If the import fails the service must not start,
because rate limiting is a security control that must never silently degrade.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
