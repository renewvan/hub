"""Session-cookie login for the dashboard site.

See docs/adr/0029-dashboard-login-is-a-session-cookie.md.

Caddy asks `GET /auth` (forward_auth) before it serves anything private; the dashboard's login page
POSTs to `/login`. Stdlib only, so the image is a pinned python:alpine with this file mounted in.

Environment: LOGIN_USER, LOGIN_PASSWORD_HASH (scrypt:N:r:p:salthex:hashhex), LOGIN_SESSION_SECRET.
The cookie is `<expiry>.<hmac>`; the HMAC key is derived from the secret AND the password hash, so
rotating either one signs every device out.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

COOKIE = "renewvan_session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
WINDOW_SECONDS = 60
MAX_BODY = 4096


def hash_password(password: str, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024)
    return f"scrypt:{n}:{r}:{p}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split(":")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(got.hex(), want)
    except ValueError:
        return False


class Auth:
    def __init__(self, user: str, password_hash: str, secret: str, clock=time.time):
        if not (user and password_hash and secret):
            raise ValueError(
                "LOGIN_USER, LOGIN_PASSWORD_HASH and LOGIN_SESSION_SECRET are required"
            )
        self.user = user
        self.password_hash = password_hash
        self.key = hmac.new(secret.encode(), password_hash.encode(), hashlib.sha256).digest()
        self.clock = clock
        self.failures: dict[str, deque] = defaultdict(deque)

    def _sign(self, expiry: int) -> str:
        return hmac.new(self.key, f"{self.user}:{expiry}".encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        expiry = int(self.clock()) + SESSION_SECONDS
        return f"{expiry}.{self._sign(expiry)}"

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        expiry, _, sig = token.partition(".")
        if not expiry.isdigit() or int(expiry) < self.clock():
            return False
        return hmac.compare_digest(sig, self._sign(int(expiry)))

    def locked(self, ip: str) -> bool:
        window = self.failures[ip]
        while window and window[0] < self.clock() - WINDOW_SECONDS:
            window.popleft()
        return len(window) >= MAX_FAILURES

    def attempt(self, ip: str, user: str, password: str) -> bool:
        # Always run scrypt, so a wrong user costs the same as a wrong password.
        password_ok = verify_password(password, self.password_hash)
        ok = password_ok and hmac.compare_digest(user.encode(), self.user.encode())
        if ok:
            self.failures.pop(ip, None)
        else:
            self.failures[ip].append(self.clock())
        return ok


def cookie_header(value: str, max_age: int) -> str:
    return f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Strict"


def make_handler(auth: Auth):
    class Handler(BaseHTTPRequestHandler):
        server_version = "renewvan-login"

        def log_message(
            self, fmt, *args
        ):  # never log request lines: they carry no secrets, but stay quiet
            sys.stderr.write("login: " + fmt % args + "\n")

        def client_ip(self) -> str:
            # Caddy is the only client and overwrites X-Forwarded-For with the real peer.
            return (
                self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[-1].strip()
            )

        def session(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
            return None

        def send(self, status: int, body: dict | None = None, headers: dict | None = None):
            payload = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def route(self):
            # Caddy's forward_auth keeps the original request's query string on /auth
            # (/influx/api/v2/query?org=renewvan arrives as /auth?org=renewvan):
            # route on the path alone.
            return urlsplit(self.path).path

        def do_GET(self):
            path = self.route()
            if path == "/session":  # the page asks "am I signed in?"; never redirects
                return self.send(204 if auth.valid(self.session()) else 401)
            if path != "/auth":
                return self.send(404)
            if auth.valid(self.session()):
                return self.send(204)
            wants_page = (
                "text/html" in self.headers.get("Accept", "")
                and self.headers.get("Sec-Fetch-Dest", "document") == "document"
                and not self.headers.get("Upgrade")
            )
            if wants_page:
                nxt = self.headers.get("X-Forwarded-Uri", "/")
                nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
                from urllib.parse import quote

                return self.send(302, headers={"Location": f"/login?next={quote(nxt, safe='/')}"})
            return self.send(401)

        def do_POST(self):
            path = self.route()
            if path == "/logout":
                return self.send(204, headers={"Set-Cookie": cookie_header("", 0)})
            if path != "/login":
                return self.send(404)
            ip = self.client_ip()
            if auth.locked(ip):
                return self.send(429, {"error": "Too many attempts. Wait a minute and try again."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError
                data = json.loads(self.rfile.read(length))
                user, password = data["user"], data["password"]
                if not isinstance(user, str) or not isinstance(password, str):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self.send(400, {"error": "Bad request."})
            if auth.attempt(ip, user, password):
                return self.send(
                    200, {"ok": True}, {"Set-Cookie": cookie_header(auth.issue(), SESSION_SECONDS)}
                )
            return self.send(401, {"error": "Wrong user or password."})

    return Handler


def main():
    auth = Auth(
        os.environ.get("LOGIN_USER", ""),
        os.environ.get("LOGIN_PASSWORD_HASH", ""),
        os.environ.get("LOGIN_SESSION_SECRET", ""),
    )
    ThreadingHTTPServer(("0.0.0.0", 8080), make_handler(auth)).serve_forever()


if __name__ == "__main__":
    if (
        len(sys.argv) == 2 and sys.argv[1] == "hash"
    ):  # used by bin/ensure-secrets.sh and rotate-secret.sh
        print(hash_password(os.environ["LOGIN_PASSWORD"]))
    else:
        main()
