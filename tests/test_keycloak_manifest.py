"""Tests for the keycluster Keycloak manifest generator (scripts/keymanifest)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from keymanifest.config import ManifestConfigError, load_config  # noqa: E402
from keymanifest.render import build_manifest_objects, render_manifest_yaml  # noqa: E402

_ENV_PREFIXES = ("KEYCLUSTER_", "KC_")


def _clean_env() -> dict[str, str]:
    keys = [k for k in os.environ if k.startswith(_ENV_PREFIXES)]
    return {k: os.environ.pop(k) for k in keys}


def _restore_env(saved: dict[str, str]) -> None:
    for k in list(os.environ):
        if k.startswith(_ENV_PREFIXES):
            del os.environ[k]
    os.environ.update(saved)


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = _clean_env()

    def tearDown(self) -> None:
        _restore_env(self._saved)


class ConfigTests(EnvIsolatedTestCase):
    def test_default_mode_is_ha(self) -> None:
        cfg = load_config()
        self.assertEqual(cfg.mode, "ha")
        self.assertEqual(cfg.replicas, 3)
        self.assertTrue(cfg.is_ha)

    def test_standalone_defaults_single_replica(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "standalone"})
        self.assertEqual(cfg.replicas, 1)
        self.assertFalse(cfg.is_ha)

    def test_ha_rejects_single_replica(self) -> None:
        with self.assertRaises(ManifestConfigError):
            load_config({"KEYCLUSTER_MODE": "ha", "KEYCLUSTER_REPLICAS": "1"})

    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(ManifestConfigError):
            load_config({"KEYCLUSTER_MODE": "bogus"})

    def test_non_integer_replicas_rejected(self) -> None:
        with self.assertRaises(ManifestConfigError):
            load_config({"KEYCLUSTER_REPLICAS": "not-a-number"})

    def test_zero_replicas_rejected(self) -> None:
        with self.assertRaises(ManifestConfigError):
            load_config({"KEYCLUSTER_MODE": "standalone", "KEYCLUSTER_REPLICAS": "0"})

    def test_db_url_is_env_driven(self) -> None:
        cfg = load_config({"KC_DB_HOST": "custom-pg", "KC_DB_PORT": "6543", "KC_DB_NAME": "kc"})
        self.assertEqual(cfg.db_url, "jdbc:postgresql://custom-pg:6543/kc")

    def test_replicas_override_wins_over_default(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha", "KEYCLUSTER_REPLICAS": "5"})
        self.assertEqual(cfg.replicas, 5)


class RenderTests(EnvIsolatedTestCase):
    def _deployment(self, objects: list[dict]) -> dict:
        return next(o for o in objects if o["kind"] == "Deployment")

    def test_ha_manifest_contains_expected_kinds(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        kinds = [o["kind"] for o in build_manifest_objects(cfg)]
        for expected in ("Service", "Deployment", "ServiceAccount", "Role", "RoleBinding",
                         "NetworkPolicy", "PodDisruptionBudget"):
            self.assertIn(expected, kinds)

    def test_ha_deployment_uses_production_start_mode(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        deployment = self._deployment(build_manifest_objects(cfg))
        args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(args, ["start", "--optimized"])
        self.assertNotIn("start-dev", args)

    def test_ha_deployment_enables_infinispan_and_kube_ping(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        deployment = self._deployment(build_manifest_objects(cfg))
        env = {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["KC_CACHE"]["value"], "ispn")
        self.assertEqual(env["KC_CACHE_STACK"]["value"], "kubernetes")
        self.assertIn("KUBERNETES_NAMESPACE", env)
        self.assertEqual(
            deployment["spec"]["template"]["spec"]["serviceAccountName"], "keycloak-cluster-sa"
        )

    def test_ha_replicas_configurable(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha", "KEYCLUSTER_REPLICAS": "5"})
        deployment = self._deployment(build_manifest_objects(cfg))
        self.assertEqual(deployment["spec"]["replicas"], 5)

    def test_ha_pdb_min_available_below_replicas(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha", "KEYCLUSTER_REPLICAS": "3"})
        pdb = next(o for o in build_manifest_objects(cfg) if o["kind"] == "PodDisruptionBudget")
        self.assertEqual(pdb["spec"]["minAvailable"], 2)

    def test_probes_avoid_flapping_during_cluster_formation(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        container = self._deployment(build_manifest_objects(cfg))["spec"]["template"]["spec"]["containers"][0]
        self.assertIn("startupProbe", container)
        startup = container["startupProbe"]
        self.assertGreaterEqual(startup["failureThreshold"] * startup["periodSeconds"], 120)
        self.assertGreaterEqual(container["readinessProbe"]["failureThreshold"], 2)
        self.assertGreaterEqual(container["livenessProbe"]["failureThreshold"], 2)

    def test_standalone_manifest_has_no_ha_extras(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "standalone"})
        objects = build_manifest_objects(cfg)
        kinds = [o["kind"] for o in objects]
        self.assertNotIn("PodDisruptionBudget", kinds)
        self.assertNotIn("NetworkPolicy", kinds)
        self.assertNotIn("ServiceAccount", kinds)
        deployment = self._deployment(objects)
        env_names = {e["name"] for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertNotIn("KC_CACHE_STACK", env_names)
        self.assertEqual(deployment["spec"]["replicas"], 1)

    def test_standalone_matches_original_backward_compatible_shape(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "standalone"})
        pod_spec = self._deployment(build_manifest_objects(cfg))["spec"]["template"]["spec"]
        self.assertFalse(pod_spec["automountServiceAccountToken"])
        self.assertNotIn("serviceAccountName", pod_spec)
        self.assertNotIn("affinity", pod_spec)

    def test_security_hardening_present_in_both_modes(self) -> None:
        for mode in ("standalone", "ha"):
            with self.subTest(mode=mode):
                cfg = load_config({"KEYCLUSTER_MODE": mode})
                pod_spec = self._deployment(build_manifest_objects(cfg))["spec"]["template"]["spec"]
                container = pod_spec["containers"][0]
                self.assertEqual(container["imagePullPolicy"], "Always")
                self.assertEqual(pod_spec["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
                self.assertTrue(pod_spec["securityContext"]["runAsNonRoot"])
                self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
                self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])

    def test_no_hardcoded_secrets_in_env(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        deployment = self._deployment(build_manifest_objects(cfg))
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        admin_password = next(e for e in env if e["name"] == "KEYCLOAK_ADMIN_PASSWORD")
        self.assertIn("valueFrom", admin_password)
        self.assertNotIn("value", admin_password)

    def test_rendered_yaml_is_valid_multidoc(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        docs = list(yaml.safe_load_all(render_manifest_yaml(cfg)))
        self.assertGreaterEqual(len(docs), 6)
        for doc in docs:
            self.assertIn("apiVersion", doc)
            self.assertIn("kind", doc)

    def test_jgroups_network_policy_scoped_to_keycloak_pods_only(self) -> None:
        cfg = load_config({"KEYCLUSTER_MODE": "ha"})
        policy = next(o for o in build_manifest_objects(cfg) if o["kind"] == "NetworkPolicy")
        rule = policy["spec"]["ingress"][0]
        self.assertEqual(rule["from"][0]["podSelector"]["matchLabels"], {"app": "keycloak"})
        self.assertEqual(rule["ports"][0]["port"], 7800)


if __name__ == "__main__":
    unittest.main()
