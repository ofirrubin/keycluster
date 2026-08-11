#!/usr/bin/env python3
"""Generate keycluster's Keycloak Kubernetes manifest.

Defaults to production HA mode (Infinispan cache + KUBE_PING clustering,
multi-replica). Existing running tenant instances are never mutated by this
script -- it only ever writes a new manifest to stdout/a file; applying it is
a separate, explicit operator action (see Makefile `deploy-keycloak-ha`).

Usage:
    python3 scripts/generate_keycloak_manifest.py [--mode ha|standalone]
        [--replicas N] [--namespace NS] [--output FILE]

All settings are also configurable via environment variables (KEYCLUSTER_MODE,
KEYCLUSTER_REPLICAS, KEYCLUSTER_NAMESPACE, KC_IMAGE, KC_DB_HOST, ...); see
scripts/keymanifest/config.py for the full list.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from keymanifest.config import ManifestConfigError, load_config
from keymanifest.render import render_manifest_yaml


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate keycluster's Keycloak manifest.")
    parser.add_argument("--mode", choices=["standalone", "ha"], help="Overrides KEYCLUSTER_MODE")
    parser.add_argument("--replicas", type=int, help="Overrides KEYCLUSTER_REPLICAS")
    parser.add_argument("--namespace", help="Overrides KEYCLUSTER_NAMESPACE")
    parser.add_argument("--output", "-o", help="Write manifest to this file instead of stdout")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    overrides = {
        "KEYCLUSTER_MODE": args.mode,
        "KEYCLUSTER_REPLICAS": str(args.replicas) if args.replicas is not None else None,
        "KEYCLUSTER_NAMESPACE": args.namespace,
    }

    try:
        cfg = load_config(overrides)
    except ManifestConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    manifest = render_manifest_yaml(cfg)
    if args.output:
        Path(args.output).write_text(manifest)
    else:
        print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
