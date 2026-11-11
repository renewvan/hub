"""Lint for the pinning policy decided in
.scratch/security-audit/issues/06-supply-chain-pinning-policy.md.

Third-party infra images are `tag@sha256:digest`, renewvan/* images an exact version tag, every
GitHub Action a full commit SHA, `npx` an exact version, and no script pipes `curl` into a shell.
Renovate keeps the pins fresh; this stops regressions."""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
INFRA = {"eclipse-mosquitto", "influxdb", "caddy", "grafana/grafana", "python"}
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))


def shell_scripts():
    paths = [*(ROOT / "bin").glob("*.sh"), *(ROOT / "plugins").rglob("*.sh")]
    # The bootstrap scripts embed their remote script in a heredoc, so scanning the file covers it.
    return [p for p in paths if "node_modules" not in p.parts]


class ImagePinningTest(unittest.TestCase):
    def images(self):
        return {name: svc["image"] for name, svc in COMPOSE["services"].items()}

    def test_infra_images_are_tag_and_digest(self):
        found = set()
        for name, image in self.images().items():
            repo = image.split("@")[0].rsplit(":", 1)[0]
            if repo not in INFRA:
                continue
            found.add(repo)
            self.assertRegex(image, r"^[\w./-]+:\d+\.\d+\.\d+(-[a-z]+)?@sha256:[0-9a-f]{64}$", name)
        self.assertEqual(found, INFRA, "every infra image is checked")

    def test_renewvan_images_are_exact_version_tags(self):
        count = 0
        for name, image in self.images().items():
            if image.startswith("ghcr.io/renewvan/"):
                count += 1
                self.assertRegex(image, r"^ghcr\.io/renewvan/[\w-]+:v\d+\.\d+\.\d+$", name)
        self.assertGreater(count, 5)

    def test_no_floating_tags(self):
        for name, image in self.images().items():
            self.assertNotRegex(image, r":(latest|\d+|\d+\.\d+)(@|$)", name)
            self.assertIn(":", image, name)


class WorkflowPinningTest(unittest.TestCase):
    def test_every_uses_is_a_full_sha_with_a_version_comment(self):
        count = 0
        for wf in WORKFLOWS:
            for lineno, line in enumerate(wf.read_text().splitlines(), 1):
                m = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)(.*)$", line)
                if not m or m.group(1).startswith("./"):
                    continue
                count += 1
                where = f"{wf.name}:{lineno}"
                self.assertRegex(m.group(1), r"^[\w./-]+@[0-9a-f]{40}$", where)
                self.assertRegex(
                    m.group(2), r"^\s+#\s*v\d+(\.\d+)*", f"{where} needs a '# vX.Y.Z' comment"
                )
        self.assertGreater(count, 8)

    def test_npx_packages_have_exact_versions(self):
        for wf in WORKFLOWS:
            for m in re.finditer(r"npx\s+(?:--?[\w-]+\s+)*([^\s-][^\s]*)", wf.read_text()):
                self.assertRegex(m.group(1), r"@\d+\.\d+\.\d+$", f"{wf.name}: {m.group(1)}")


class WorkflowInjectionTest(unittest.TestCase):
    def test_no_expression_inside_run_bodies(self):
        for wf in WORKFLOWS:
            doc = yaml.safe_load(wf.read_text())
            for job_name, job in doc["jobs"].items():
                for step in job.get("steps", []):
                    self.assertNotIn("${{", step.get("run", ""), f"{wf.name}:{job_name}")

    def test_test_job_has_no_write_permissions(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        self.assertEqual(doc["jobs"]["test"]["permissions"], {"contents": "read"})
        for step in doc["jobs"]["publish-image"]["steps"]:
            self.assertNotIn("TEST_COMMAND", str(step.get("env", "")))


class InstallerPinningTest(unittest.TestCase):
    def test_no_curl_piped_into_a_shell(self):
        for script in shell_scripts():
            code = "\n".join(
                line
                for line in script.read_text().splitlines()
                if not line.lstrip().startswith("#")
            )
            self.assertNotRegex(
                code, r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b", f"{script.relative_to(ROOT)}"
            )

    def test_apt_sources_are_bound_to_a_keyring(self):
        for rel in ("bin/bootstrap-pi.sh", "plugins/tailscale/install.sh"):
            text = (ROOT / rel).read_text()
            self.assertIn("signed-by=/etc/apt/keyrings/", text, rel)
            self.assertNotIn("apt-key", text, rel)


if __name__ == "__main__":
    unittest.main()
