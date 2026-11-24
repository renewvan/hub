"""Static checks on docker-compose.yml for the security hardening: exposure and
credentials (docs/adr/0024), D-Bus and /dev scope.

CI is the only automatic gate; the Pi-side runtime checks live in bin/check-hardening.sh."""

import re
import unittest
from pathlib import Path

import yaml

import broker_clients

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
    """ADR 0025: each client connects with its own account, never one shared credential. The
    clients are bin/broker-clients.conf; what compose, .env.example and the plugins say is
    derived from it here."""

    CLIENTS, _ = broker_clients.load()
    # a client whose credential reaches its compose service, by service
    BY_SERVICE = {c.target: c for c in CLIENTS if c.kind in ("config.ini", "compose")}

    @staticmethod
    def account_env(key):
        var = broker_clients.password_var(key)
        return f"${{{var}:+{key}}}", f"${{{var}:-}}"

    def test_every_service_that_connects_uses_its_own_account(self):
        connecting = {
            name
            for name in SERVICES
            if {"MQTT_HOST", "VITE_MQTT_WS_URL"} & env_of(name).keys()
            or any(k.endswith("MQTT_USERNAME") for k in env_of(name))
        }
        self.assertIn("logger", connecting)  # the check finds something
        for service in connecting:
            client = self.BY_SERVICE.get(service)
            self.assertIsNotNone(client, f"{service} connects but is no client's target")
            env = env_of(service)
            prefix = "VITE_" if "VITE_MQTT_WS_URL" in env else ""
            user, password = self.account_env(client.key)
            self.assertEqual(env[f"{prefix}MQTT_USERNAME"], user, service)
            self.assertEqual(env[f"{prefix}MQTT_PASSWORD"], password, service)

    def test_every_compose_client_has_its_account_in_compose(self):
        # driver-venus ships commented out (kept for vans with Venus OS): uncommented, it must
        # still use its own account, and the account stays provisioned so enabling it needs no
        # secrets work.
        text = (ROOT / "docker-compose.yml").read_text()
        for client in self.CLIENTS:
            if client.kind != "compose":
                continue
            user, password = self.account_env(client.key)
            if client.target in SERVICES:
                prefix = "VITE_" if client.target == "dashboard" else ""
                env = env_of(client.target)
                self.assertEqual(env[f"{prefix}MQTT_USERNAME"], user, client.key)
                self.assertEqual(env[f"{prefix}MQTT_PASSWORD"], password, client.key)
            else:
                self.assertIn(f"  # {client.target}:", text, client.key)
                self.assertIn(f"#     MQTT_USERNAME: {user}", text, client.key)
                self.assertIn(f"#     MQTT_PASSWORD: {password}", text, client.key)

    def test_dashboard_uses_the_command_account(self):
        self.assertEqual(self.BY_SERVICE["dashboard"].key, "command")

    def test_env_example_lists_one_password_per_client_in_registry_order(self):
        listed = re.findall(r"(?m)^(MQTT_[A-Z0-9_]+_PASSWORD)=$", ENV_EXAMPLE)
        self.assertEqual(listed, [c.var for c in self.CLIENTS])
        # and the comment above them names the same clients
        names = re.search(
            r"one per client \(([^)]*), plus one esp32-<name> per board\)", ENV_EXAMPLE
        )
        self.assertIsNotNone(names)
        named = [n.strip(" #\n") for n in names.group(1).split(",")]
        self.assertEqual(named, [c.key.upper() for c in self.CLIENTS])
        self.assertRegex(ENV_EXAMPLE, r"(?m)^BROKER_ESP32_BOARDS=$")  # board accounts, hub#162

    def test_host_plugins_read_their_registry_env_file(self):
        for client in self.CLIENTS:
            if client.kind != "envfile":
                continue
            units = list((ROOT / "plugins").glob(f"*/{client.unit}.service"))
            self.assertEqual(len(units), 1, client.unit)
            self.assertIn(
                f"EnvironmentFile=-/etc/renewvan/{client.target}\n",
                units[0].read_text(),
                client.key,
            )

    def test_no_shared_broker_credential_remains(self):
        text = (ROOT / "docker-compose.yml").read_text()
        self.assertNotIn("RENEWVAN_USERNAME", text)
        self.assertNotIn("RENEWVAN_PASSWORD", text)
        self.assertNotIn("RENEWVAN_USERNAME", ENV_EXAMPLE)

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

    def test_caddyfile_gates_the_whole_site(self):
        text = (ROOT / "docker" / "caddy" / "Caddyfile").read_text()
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

    def test_compose_mounts_the_in_repo_caddyfile(self):
        # The Caddyfile describes hub routes, so the hub owns it (docs/adr/0032): deploy ships it
        # with docker/, and needs no sibling app checkout.
        mount = "./docker/caddy/Caddyfile:/etc/caddy/Caddyfile:ro"
        self.assertIn(mount, SERVICES["caddy"]["volumes"])
        deploy = (ROOT / "bin" / "deploy.sh").read_text()
        self.assertIn('rsync -az --delete docker/ "${HOST}:${REMOTE_DIR}/docker/"', deploy)
        self.assertNotIn("${REPO_ROOT}/..", deploy)
        self.assertNotRegex(deploy, r"(rsync|mkdir)[^\n]*/opt/renewvan/(apps|dashboard)/")

    def test_deploy_restarts_caddy_when_its_mounted_caddyfile_changed(self):
        # A single-file bind mount keeps the old inode after rsync swaps the file, so up -d never
        # reloads it: deploy compares the running copy with the synced one and restarts on a change.
        deploy = (ROOT / "bin" / "deploy.sh").read_text()
        self.assertIn("docker compose exec -T caddy md5sum /etc/caddy/Caddyfile", deploy)
        self.assertIn("md5sum docker/caddy/Caddyfile", deploy)
        self.assertIn("docker compose restart caddy", deploy)

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


class LanFacingHardeningTest(unittest.TestCase):
    """mosquitto and caddy are the only containers reachable from the LAN/Tailnet, so they
    carry the same cap_drop/no-new-privileges/read-only hardening as the drivers (the image
    entrypoints permit it: neither writes outside its named volumes, and mosquitto runs as
    the image's own uid instead of setuid-ing from root)."""

    def test_mosquitto(self):
        svc = SERVICES["mosquitto"]
        self.assertEqual(str(svc.get("user")), "1883:1883", "the image's mosquitto uid: no setuid")
        self.assertEqual(svc.get("cap_drop"), ["ALL"])
        self.assertIn("no-new-privileges:true", svc.get("security_opt", []))
        self.assertTrue(svc.get("read_only"), "config is a mount; data/log are named volumes")
        self.assertIn(
            "/tmp", svc.get("tmpfs", []), "exec'd probe tooling and POSIX need a writable /tmp"
        )

    def test_caddy(self):
        svc = SERVICES["caddy"]
        self.assertEqual(svc.get("cap_drop"), ["ALL"])
        self.assertIn("no-new-privileges:true", svc.get("security_opt", []))
        self.assertTrue(svc.get("read_only"), "writes only /config and /data (named volumes)")


if __name__ == "__main__":
    unittest.main()
