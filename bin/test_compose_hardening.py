"""Static checks on docker-compose.yml for the security hardening: exposure and
credentials (docs/adr/0024), D-Bus and /dev scope.

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

    def test_no_service_publishes_on_all_interfaces(self):
        for name in SERVICES:
            self.assertNotIn("", published_hosts(name), f"{name} binds 0.0.0.0")

    def test_caddy_is_loopback_here_and_lan_plus_tailnet_in_the_pi_override(self):
        # 443 is the site; 80 is only Caddy's automatic redirect to https. Both on the same two
        # addresses, never every interface, and never loopback 80 (the dashboard's, for the kiosk).
        self.assertEqual(published_hosts("caddy"), ["127.0.0.1"])
        lan = yaml.safe_load((ROOT / "docker-compose.lan.yml").read_text())
        ports = [str(p) for p in lan["services"]["caddy"]["ports"]]
        self.assertEqual(len(ports), 4)
        for port in (443, 80):
            hosts = {
                p.rsplit(f":{port}:{port}", 1)[0] for p in ports if p.endswith(f":{port}:{port}")
            }
            self.assertEqual(
                {h.split(":?")[0] for h in hosts},
                {"${MQTT_BIND_ADDR", "${TAILSCALE_BIND_ADDR"},
                port,
            )
        self.assertRegex(ENV_EXAMPLE, r"(?m)^TAILSCALE_BIND_ADDR=$")

    def test_caddy_serves_its_site_on_the_tailnet_address_and_name(self):
        # Caddy routes by Host: a host matching no site address gets a blank 200. The Caddyfile
        # lists the tailnet address and MagicDNS name, so compose must pass both, with defaults
        # that keep local dev starting.
        env = env_of("caddy")
        self.assertEqual(env["TAILSCALE_BIND_ADDR"], "${TAILSCALE_BIND_ADDR:-127.0.0.1}")
        self.assertEqual(env["TAILSCALE_DNS_NAME"], "${TAILSCALE_DNS_NAME:-localhost}")
        self.assertRegex(ENV_EXAMPLE, r"(?m)^TAILSCALE_DNS_NAME=$")
        self.assertIn("TAILSCALE_DNS_NAME", (ROOT / "bin" / "deploy.sh").read_text())

    def test_check_exposure_covers_the_caddy_publish(self):
        script = (ROOT / "bin" / "check-exposure.sh").read_text()
        self.assertIn("--published", script)
        self.assertIn("renewvan-caddy", script)
        self.assertIn("for port in 443 80", script)
        # --published runs on the Pi, so deploy must ship the script there
        deploy = (ROOT / "bin" / "deploy.sh").read_text()
        self.assertRegex(deploy, r"rsync -az [^\n]*bin/check-exposure\.sh [^\n]*/bin/")


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


class BrokerAccountsTest(unittest.TestCase):
    """ADR 0025: each client connects with its own account, never one shared credential."""

    COMPOSE_CLIENTS = {
        "driver-autoterm": "autoterm",
        "driver-demo": "demo",
        "driver-gps": "gps",
        "driver-router": "router",
        "driver-tank": "tank",
        "driver-temperature": "temperature",
        "driver-tilt": "tilt",
        "driver-vedirect": "vedirect",
        "logger": "logger",
    }

    def test_commented_out_driver_venus_keeps_its_own_account(self):
        # driver-venus ships commented out in docker-compose.yml (kept for vans with Venus OS), so
        # it is not in COMPOSE_CLIENTS; uncommented, it must still use its own account, and the
        # account stays provisioned so enabling it needs no secrets work.
        text = (ROOT / "docker-compose.yml").read_text()
        self.assertNotIn("driver-venus", SERVICES)
        self.assertIn("  # driver-venus:", text)
        self.assertIn("#     MQTT_USERNAME: ${MQTT_VENUS_PASSWORD:+venus}", text)
        self.assertIn("#     MQTT_PASSWORD: ${MQTT_VENUS_PASSWORD:-}", text)
        accounts = (ROOT / "bin" / "broker-accounts.sh").read_text()
        self.assertIn('"venus:compose:driver-venus"', accounts)
        self.assertRegex(ENV_EXAMPLE, r"(?m)^MQTT_VENUS_PASSWORD=$")

    def test_no_shared_broker_credential_remains(self):
        text = (ROOT / "docker-compose.yml").read_text()
        self.assertNotIn("RENEWVAN_USERNAME", text)
        self.assertNotIn("RENEWVAN_PASSWORD", text)
        self.assertNotIn("RENEWVAN_USERNAME", ENV_EXAMPLE)

    def test_each_compose_client_gets_its_own_account(self):
        for service, key in self.COMPOSE_CLIENTS.items():
            env = env_of(service)
            var = f"MQTT_{key.upper()}_PASSWORD"
            self.assertEqual(env["MQTT_USERNAME"], f"${{{var}:+{key}}}", service)
            self.assertEqual(env["MQTT_PASSWORD"], f"${{{var}:-}}", service)
            self.assertRegex(ENV_EXAMPLE, rf"(?m)^{var}=$")

    def test_dashboard_uses_the_command_account(self):
        env = env_of("dashboard")
        self.assertEqual(env["VITE_MQTT_USERNAME"], "${MQTT_COMMAND_PASSWORD:+command}")
        self.assertEqual(env["VITE_MQTT_PASSWORD"], "${MQTT_COMMAND_PASSWORD:-}")

    def test_config_ini_drivers_and_plugins_are_covered_by_ensure_secrets(self):
        accounts = (ROOT / "bin" / "broker-accounts.sh").read_text()
        for key in ("wattcycle", "kiosk", "tailscale", "hub", "command"):
            self.assertIn(f'"{key}:', accounts)
        self.assertIn("BROKER_ESP32_BOARDS", accounts)  # board accounts esp32-<name>, hub#162
        self.assertRegex(ENV_EXAMPLE, r"(?m)^BROKER_ESP32_BOARDS=$")
        for service, key in self.COMPOSE_CLIENTS.items():
            self.assertIn(f'"{key}:', accounts)

    def test_login_service_gets_the_login_and_the_kiosk_bypasses_it(self):
        env = env_of("login")
        for name in ("LOGIN_USER", "LOGIN_PASSWORD_HASH", "LOGIN_SESSION_SECRET"):
            self.assertTrue(env[name].startswith(f"${{{name}:?"), name)
            self.assertRegex(ENV_EXAMPLE, rf"(?m)^{name}=$")
        # only Caddy reaches it: no published port, and Caddy waits for it
        self.assertNotIn("ports", SERVICES["login"])
        self.assertIn("login", SERVICES["caddy"]["depends_on"])
        self.assertNotIn("LOGIN_PASSWORD_HASH", env_of("caddy"))
        # the kiosk loads http://localhost:80, the dashboard container itself, not Caddy
        self.assertEqual(published_hosts("dashboard"), ["127.0.0.1"])
        self.assertTrue(any(str(p).endswith(":80:80") for p in SERVICES["dashboard"]["ports"]))

    def test_caddyfile_gates_the_whole_site_when_the_dashboard_checkout_is_present(self):
        caddyfile = ROOT.parent / "dashboard" / "docker" / "Caddyfile"
        if not caddyfile.exists():
            self.skipTest("no ../dashboard checkout")
        text = caddyfile.read_text()
        self.assertIn("{$DASHBOARD_HOST}, {$TAILSCALE_BIND_ADDR}, {$TAILSCALE_DNS_NAME} {", text)
        self.assertIn("forward_auth @private login:8080 {", text)
        self.assertNotIn("basic_auth", text.replace("no native", ""), "no browser Basic popup")
        # the gate comes before any handler, so /mqtt and /influx are both behind it
        self.assertLess(text.index("forward_auth @private"), text.index("handle_path /influx/*"))
        # Only the login page, its static assets, the PWA files and the login service's /api/* are
        # public. Pin the list: anything added here is reachable without a session. /env.js, /mqtt,
        # /influx and /maps must stay off it.
        exempt = re.search(r"(?m)^\s*@private not path (.+)$", text)
        self.assertIsNotNone(
            exempt, "the login exemption must stay a single `@private not path` line"
        )
        self.assertEqual(
            set(exempt.group(1).split()),
            {
                "/login",
                "/assets/*",
                "/sw.js",
                "/workbox-*.js",
                "/registerSW.js",
                "/favicon.ico",
                "/favicon.svg",
                "/apple-touch-icon.png",
                "/manifest.webmanifest",
                "/icon-192.png",
                "/icon-512.png",
                "/icon-maskable-512.png",
                "/api/*",
            },
        )

    def test_deploy_restarts_clients_whose_credential_file_changed(self):
        deploy = (ROOT / "bin" / "deploy.sh").read_text()
        self.assertIn(".env.changed-clients", deploy)
        self.assertIn("restart_client", deploy)

    def test_deploy_restarts_login_when_its_mounted_file_changed(self):
        # login.py is a single-file bind mount: up -d never reloads it, so deploy compares the
        # running copy with the synced one and restarts login when they differ.
        deploy = (ROOT / "bin" / "deploy.sh").read_text()
        self.assertIn("docker compose exec -T login md5sum /app/login.py", deploy)
        self.assertIn("md5sum docker/login/login.py", deploy)
        self.assertIn("docker compose restart login", deploy)
        mount = "./docker/login/login.py:/app/login.py:ro"
        self.assertIn(mount, SERVICES["login"]["volumes"])


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
        self.assertIn(f"--uid {uid} ", policy, "the host needs an account for the uid")
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
