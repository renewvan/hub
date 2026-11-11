---
status: accepted
---

# The dashboard login is a session cookie from a small login service, not Caddy's basic_auth popup

_(Supersedes the "shared Caddy `basic_auth`" gate in [ADR 0024](0024-history-reaches-browsers-through-a-caddy-influx-proxy.md) and [ADR 0025](0025-broker-has-per-client-accounts-and-anonymous-read-only.md). The rest of both stands: the `command` account is still public through `/env.js`, and the kiosk still bypasses the gate on `http://localhost:80`.)_

`basic_auth` made the browser show its own sign-in popup. A page cannot style, explain or retry it, and the credential it caches is invisible to the app. The dashboard now has its own login page (HeroUI, phone first), and a `login` service decides who is signed in.

- **`login`** is one stdlib Python file (`docker/login/login.py`) in a pinned `python:alpine` image, with no published port: only Caddy reaches it. `POST /login` checks the user and password, `GET /auth` answers Caddy, `GET /session` answers the page, `POST /logout` clears the cookie.
- **Caddy** asks `forward_auth` (`GET /auth`) before anything private. A valid cookie passes (204). A page navigation without one is sent to `/login?next=<path>` (302). Anything else, including scripts, `/influx` and the `/mqtt` WebSocket, gets 401. There is no `WWW-Authenticate` header, so no native popup. The browser sends the cookie on the WebSocket upgrade, so `/mqtt` is gated like the rest.
- **Public**: only the login page, its hashed build assets and service worker, the PWA manifest and icons, and `/api/*` (the service's own endpoints). `/env.js`, `/mqtt`, `/influx` and `/maps` stay behind the session. `test_compose_hardening` pins the list.
- **Cookie**: `<expiry>.<hmac>`, `HttpOnly; Secure; SameSite=Strict`, 30 days. The HMAC key comes from `LOGIN_SESSION_SECRET` and the password hash, so rotating either signs every device out. Stateless: there is no session store to lose when the Pi reboots.
- **Password**: scrypt (stdlib), `LOGIN_PASSWORD_HASH` in `.env`, shown once by `bin/ensure-secrets.sh`. It replaces the bcrypt `CADDY_BASIC_AUTH_HASH`, because bcrypt needs a third-party package. `bin/rotate-secret.sh login` rotates it.
- **Brute force**: 5 failed attempts per minute per address lock that address for the rest of the minute (429), and a wrong user costs the same scrypt run as a wrong password.

## Consequences

- Upgrading a van generates a new password (shown once on the next deploy); the old `CADDY_BASIC_AUTH_*` values in `.env` are unused and can be deleted.
- The login service must be up for anyone to use the site, because Caddy asks it on every private request.
- A signed-out device that opens the cached PWA shell is sent to `/login` by the page itself (it asks `/api/session` before it connects).
