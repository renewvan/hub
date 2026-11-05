"""Static checks on docker-compose.yml for the security hardening decided in
.scratch/security-audit/issues (03 exposure and credentials, 05 D-Bus and /dev scope).

CI is the only automatic gate; the Pi-side runtime checks live in bin/check-hardening.sh."""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
ENV_EXAMPLE = (ROOT / ".env.example").read_text()


def published_hosts(service):
    """Host address of every `ports:` entry of a service ('' when it binds all interfaces)."""
    hosts = []
    for port in SERVICES[service].get("ports", []):
        parts = str(port).split(":")
        hosts.append(parts[0] if len(parts) == 3 else "")
    return hosts


def env_of(service):
    env = SERVICES[service].get("environment", {})
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {k: str(v) for k, v in env.items()}


class ExposureTest(unittest.TestCase):
    def test_influxdb_and_grafana_publish_on_loopback_only(self):
        for service in ("influxdb", "grafana"):
            hosts = published_hosts(service)
            self.assertTrue(hosts, f"{service} publishes nothing")
            self.assertEqual(set(hosts), {"127.0.0.1"}, service)

    def test_no_service_publishes_on_all_interfaces_except_caddy(self):
        for name in SERVICES:
            if name == "caddy":
                continue
            self.assertNotIn("", published_hosts(name), f"{name} binds 0.0.0.0")


class CredentialsTest(unittest.TestCase):
    def test_no_secret_default_fallbacks(self):
        text = (ROOT / "docker-compose.yml").read_text()
        self.assertNotIn("renewvan-dev", text)
        for match in re.finditer(
            r"\$\{((?:INFLUXDB|GF_)[A-Z_]*(?:TOKEN|PASSWORD)[A-Z_]*)([^}]*)\}", text
        ):
            self.assertTrue(
                match.group(2).startswith(":?"), f"{match.group(1)} must be required (${{VAR:?}})"
            )

    def test_each_consumer_gets_its_own_scoped_token(self):
        self.assertIn("INFLUXDB_LOGGER_TOKEN", env_of("logger")["INFLUXDB_TOKEN"])
        self.assertIn("INFLUXDB_GRAFANA_TOKEN", env_of("grafana")["INFLUXDB_GRAFANA_TOKEN"])
        self.assertIn("INFLUXDB_DASHBOARD_TOKEN", env_of("caddy")["INFLUXDB_DASHBOARD_TOKEN"])

    def test_admin_token_reaches_only_influxdb(self):
        for name in SERVICES:
            blob = " ".join(env_of(name).values())
            if name == "influxdb":
                self.assertIn("INFLUXDB_ADMIN_TOKEN", blob)
            else:
                self.assertNotIn("INFLUXDB_ADMIN_TOKEN", blob, name)

    def test_grafana_admin_password_comes_from_env_and_anonymous_is_off(self):
        env = env_of("grafana")
        self.assertIn("GF_SECURITY_ADMIN_PASSWORD", env["GF_SECURITY_ADMIN_PASSWORD"])
        self.assertEqual(env["GF_AUTH_ANONYMOUS_ENABLED"].lower(), "false")

    def test_env_example_holds_no_usable_secret(self):
        for name in (
            "INFLUXDB_ADMIN_PASSWORD",
            "INFLUXDB_ADMIN_TOKEN",
            "INFLUXDB_LOGGER_TOKEN",
            "INFLUXDB_GRAFANA_TOKEN",
            "INFLUXDB_DASHBOARD_TOKEN",
            "GF_SECURITY_ADMIN_PASSWORD",
        ):
            self.assertRegex(ENV_EXAMPLE, rf"(?m)^{name}=$")


class DriverHardeningTest(unittest.TestCase):
    def check_common(self, name):
        svc = SERVICES[name]
        user = str(svc.get("user", ""))
        self.assertRegex(user, r"^[1-9][0-9]*$", f"{name} must run as a fixed non-root uid")
        self.assertEqual(svc.get("cap_drop"), ["ALL"], name)
        self.assertIn("no-new-privileges:true", svc.get("security_opt", []), name)
        self.assertFalse(svc.get("privileged", False), name)

    def test_wattcycle(self):
        self.check_common("driver-wattcycle")
        mounts = SERVICES["driver-wattcycle"]["volumes"]
        dbus = [m for m in mounts if m.startswith("/var/run/dbus")]
        self.assertEqual(dbus, ["/var/run/dbus:/var/run/dbus"], "only the D-Bus socket dir")
        self.assertFalse([m for m in mounts if m.startswith(("/dev", "/:", "/var/run/docker"))])

    def test_wattcycle_uid_matches_the_host_dbus_policy(self):
        uid = str(SERVICES["driver-wattcycle"]["user"])
        policy = (ROOT / "bin" / "bootstrap-pi.sh").read_text()
        self.assertIn(f'<policy user="{uid}">', policy)
        self.assertIn('send_destination="org.bluez"', policy)
        self.assertIn('<deny send_destination="org.freedesktop.systemd1"/>', policy)

    def test_vedirect(self):
        self.check_common("driver-vedirect")
        mounts = SERVICES["driver-vedirect"]["volumes"]
        self.assertIn("/dev:/dev:ro", mounts)
        self.assertFalse([m for m in mounts if m.startswith("/dev:") and not m.endswith(":ro")])
        self.assertEqual(SERVICES["driver-vedirect"]["group_add"], ["20"], "dialout")
        self.assertEqual(
            SERVICES["driver-vedirect"]["device_cgroup_rules"], ["c 188:* rmw", "c 166:* rmw"]
        )


if __name__ == "__main__":
    unittest.main()
