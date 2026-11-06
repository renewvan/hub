---
status: accepted
---

# Anonymous clients are refused after the cutover; a read-only `reader` account replaces anonymous reads

Supersedes the anonymous-read part of [ADR 0025](0025-broker-has-per-client-accounts-and-anonymous-read-only.md). The per-client accounts, the `command` account and the Caddy gate stand.

At the end of the migration `allow_anonymous` is `false` on both listeners. Every client needs an account, including people and tools that only read: MQTT Explorer and a development dashboard use the new `reader` account, which may read `renewvan/#` and write nothing. During the migration `allow_anonymous` stays `true` together with the temporary `topic readwrite renewvan/#` line (anonymous clients only, so every account is already scoped), and both go away at the same step: `BROKER_ACL_OPEN=0`, applied by `bin/cutover-broker-acl.sh`, which restarts the broker.

## Why

- **Nothing on the van network reads telemetry without a credential.** The goal of the audit was a broker that only accounts can use; leaving reads open kept a hole the owner did not want.
- **One switch, not two.** `BROKER_ACL_OPEN` already marks "migration versus cut over", so the open ACL line and `allow_anonymous` can never disagree.
- **A restart applies it, a reload does not.** On the Pi, SIGHUP reloaded the new ACL and passwords but the broker kept accepting anonymous clients after `allow_anonymous false` was in the file; a restart refused them (`rc=5`) while accounts kept working. The cutover therefore restarts the broker and waits for every client to reconnect before it judges the result. Plugins must handle the reconnect: the tailscale and kiosk plugins had a broken `on_disconnect` (paho v1 signature under callback API v2) that stopped them reconnecting at all, and were fixed.

## Consequences and rules

- **The ACL grants reads with `pattern read renewvan/#`, not `topic read`.** A `topic` line before the first `user` line applies to anonymous clients only. With it, every authenticated driver, the logger and the dashboard read nothing (found on the Pi, not in a unit test). `pattern` applies to every client.
- **Every reader needs a credential, so the router needs one too.** The RUT360 publishes `renewvan/router/<id>/raw` anonymously today; it must be given the `router` account before the cutover or its data stops arriving silently.
- The `command` password is still public through `/env.js`; the dashboard keeps working for anyone who can open it. `reader` is not public: hand it to people.
- A client that is refused (`rc=5`) is not retried by most MQTT libraries: it needs a restart, and a browser tab needs a reload. The ESP32 reboots after 15 minutes of failed authentication, so it must be flashed (ticket 58) before the cutover.
- `bin/check-broker-auth.sh` subscribes as `reader`, so it works in both states, and after the cutover it fails if an anonymous client can still connect.

## Rejected alternatives

- **Keep anonymous read-only (ADR 0025 as first written).** Simpler for tools, but leaves telemetry readable by anyone on the LAN or tailnet.
- **Two switches (`allow_anonymous` set by hand).** The ACL line and the setting could drift apart and lock out or open up the broker.

## Why this is an ADR

Hard to reverse: after the cutover every reader and writer needs a credential, and anything missed fails silently or with `rc=5`. Surprising without context: a reader will wonder why the ACL uses `pattern` and why the read-only account exists when the dashboard's `command` password is public.
