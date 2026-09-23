"""Unit-test environment for domain-manager.

app.auth reads its configuration at import time and exits without a client id,
and refuses to start in production without admin client credentials, so the
test process pins a dev configuration before any app module is imported.
No network or Keycloak is used by the unit tests.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("KEYCLOAK_CLIENT_ID", "domain-manager")
os.environ.setdefault("KEYCLOAK_SERVER_URL", "http://keycloak.test")
os.environ.setdefault("KEYCLOAK_REALM", "master")
os.environ.setdefault("ENVIRONMENT", "dev")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
