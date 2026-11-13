import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

import login


class Clock:
    now = 1_000_000.0

    def __call__(self):
        return self.now


class LoginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clock = Clock()
        cls.hash = login.hash_password("s3cret", n=2**4)
        cls.auth = login.Auth("renewvan", cls.hash, "signing-secret", cls.clock)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), login.make_handler(cls.auth))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.auth.failures.clear()

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        raw = json.dumps(body) if body is not None else None
        conn.request(method, path, raw, {"X-Forwarded-For": "10.0.0.9", **(headers or {})})
        res = conn.getresponse()
        data = res.read()
        return res, data

    def sign_in(self, password="s3cret", user="renewvan"):
        return self.call(
            "POST",
            "/login",
            {"user": user, "password": password},
            {"Content-Length": str(len(json.dumps({"user": user, "password": password})))},
        )

    def cookie(self):
        res, _ = self.sign_in()
        self.assertEqual(res.status, 200)
        return res.getheader("Set-Cookie").split(";")[0]

    def test_cookie_is_httponly_secure_samesite_strict(self):
        res, _ = self.sign_in()
        header = res.getheader("Set-Cookie")
        for flag in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, header)

    def test_good_cookie_passes_auth(self):
        res, _ = self.call("GET", "/auth", headers={"Cookie": self.cookie()})
        self.assertEqual(res.status, 204)

    def test_auth_ignores_the_forwarded_query_string(self):
        # Caddy's forward_auth keeps the original query, so a chart's
        # POST /influx/api/v2/query?org=... arrives as GET /auth?org=...:
        # it must get the session check, not a 404 that blocks it.
        res, _ = self.call("GET", "/auth?org=renewvan", headers={"Cookie": self.cookie()})
        self.assertEqual(res.status, 204)
        res, _ = self.call("GET", "/auth?org=renewvan", headers={"Accept": "*/*"})
        self.assertEqual(res.status, 401)
        res, _ = self.call("GET", "/session?x=1", headers={"Cookie": self.cookie()})
        self.assertEqual(res.status, 204)

    def test_no_cookie_is_401_for_scripts_and_websockets(self):
        res, _ = self.call("GET", "/auth", headers={"Accept": "*/*"})
        self.assertEqual(res.status, 401)
        res, _ = self.call("GET", "/auth", headers={"Accept": "text/html", "Upgrade": "websocket"})
        self.assertEqual(res.status, 401)

    def test_page_navigation_redirects_to_login_keeping_the_destination(self):
        res, _ = self.call(
            "GET", "/auth", headers={"Accept": "text/html", "X-Forwarded-Uri": "/relays"}
        )
        self.assertEqual(res.status, 302)
        self.assertEqual(res.getheader("Location"), "/login?next=/relays")

    def test_open_redirect_target_is_not_followed(self):
        res, _ = self.call(
            "GET", "/auth", headers={"Accept": "text/html", "X-Forwarded-Uri": "//evil.test"}
        )
        self.assertEqual(res.getheader("Location"), "/login?next=/")

    def test_session_endpoint_never_redirects(self):
        res, _ = self.call("GET", "/session", headers={"Accept": "text/html"})
        self.assertEqual(res.status, 401)
        res, _ = self.call("GET", "/session", headers={"Cookie": self.cookie()})
        self.assertEqual(res.status, 204)

    def test_wrong_password_and_wrong_user_are_refused_alike(self):
        for password, user in (("nope", "renewvan"), ("s3cret", "someone")):
            res, data = self.sign_in(password, user)
            self.assertEqual(res.status, 401)
            self.assertIsNone(res.getheader("Set-Cookie"))
            self.assertEqual(json.loads(data)["error"], "Wrong user or password.")

    def test_five_failures_lock_the_address_for_a_minute(self):
        for _ in range(5):
            self.assertEqual(self.sign_in("nope")[0].status, 401)
        self.assertEqual(self.sign_in()[0].status, 429)  # even the right password
        self.clock.now += 61
        self.assertEqual(self.sign_in()[0].status, 200)
        self.clock.now -= 61

    def test_expired_cookie_is_refused(self):
        cookie = self.cookie()
        self.clock.now += login.SESSION_SECONDS + 1
        res, _ = self.call("GET", "/auth", headers={"Cookie": cookie})
        self.clock.now -= login.SESSION_SECONDS + 1
        self.assertEqual(res.status, 401)

    def test_tampered_cookie_is_refused(self):
        expiry, _, _ = self.cookie().partition(".")
        res, _ = self.call("GET", "/auth", headers={"Cookie": f"{expiry}.{'0' * 64}"})
        self.assertEqual(res.status, 401)

    def test_rotating_the_password_signs_everyone_out(self):
        cookie = self.cookie()
        other = login.Auth(
            "renewvan", login.hash_password("changed", n=2**4), "signing-secret", self.clock
        )
        self.assertTrue(self.auth.valid(cookie.split("=", 1)[1]))
        self.assertFalse(other.valid(cookie.split("=", 1)[1]))

    def test_logout_clears_the_cookie(self):
        res, _ = self.call("POST", "/logout")
        self.assertEqual(res.status, 204)
        self.assertIn("Max-Age=0", res.getheader("Set-Cookie"))

    def test_hash_never_contains_the_password_and_rejects_garbage(self):
        self.assertNotIn("s3cret", self.hash)
        self.assertFalse(login.verify_password("x", "not-a-hash"))
        self.assertFalse(login.verify_password("x", "bcrypt:1:2:3:aa:bb"))


if __name__ == "__main__":
    unittest.main()
