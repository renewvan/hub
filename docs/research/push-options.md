# Research: Cloud-free push options for the mobile app

Ticket: #231 (part of #222 wayfinder map)
Question: How can the Renewvan mobile app receive camera alerts while backgrounded and off-LAN, entirely cloud-free (no FCM, no APNs, no public ntfy.sh)?

Context: apps monorepo (renewvan/apps) has no notification plumbing today; the phone's only off-LAN route to the hub is Tailscale (Pi on the tailnet); hub is a Pi 4 4 GB running docker compose (15 services, a new small service is possible).

## TL;DR

- **Android is a solved problem**: a persistent connection (websocket) held by a foreground service delivers instant alerts fully self-hosted. ntfy has already built and battle-tested exactly this; we do not need to reinvent it.
- **iOS has no cloud-free background push, full stop.** APNs is the only channel iOS grants background delivery to; without it an iOS app is foreground/manual-refresh plus opportunistic ~30 s `BGAppRefreshTask` windows. This is an Apple architectural constraint, not a missing feature in any library.
- **WebPush (PWA) is not cloud-free either**: browser Push API background delivery always routes through the browser vendor's push service (FCM endpoints in Chrome, Mozilla's service in Firefox, APNs for installed iOS PWAs). VAPID keys only authenticate the app server to that vendor service — they do not let you self-host the push service.
- Recommendation: **run a self-hosted ntfy server on the Pi as a new small compose service**, and (ranked candidates below) treat it as the hub's alert fan-out bus. Start with the official ntfy Android app over Tailscale (zero app code), then embed the ntfy JSON-stream/websocket subscription in the Renewvan app for first-class UX.

## Per-platform reality table

| Option | Android backgrounded, off-LAN, no FCM | iOS backgrounded, no APNs | Pi 4 footprint | Integration effort in apps monorepo |
|---|---|---|---|---|
| **1. Self-hosted ntfy (server on hub) + official ntfy app** | ✅ Works: "instant delivery" = foreground service + constant connection, documented to work in Doze; ~0–1% battery per 17 h. Play flavor never uses FCM for self-hosted servers; F-Droid flavor has no Firebase at all. | ❌ No background delivery. iOS app is bare-bones; the only wake path relays a contentless `poll_request` through **public ntfy.sh's APNs** (`upstream-base-url`) — violates the no-cloud constraint. Foreground/manual refresh only. | ~13 MiB idle measured (selfhostatlas), ~52–55 MiB under 100 concurrent requests on a Pi 5 (peppe8o); SQLite cache; arm64 image. Negligible next to 15 existing services. | **Zero app code**: user installs ntfy app, subscribes to `http(s)://<tailnet-host>/topic`. Hub side = one HTTP POST per alert. |
| **2. Same ntfy server, subscribe from our own app (embedded client)** | ✅ Same transport (websocket / JSON stream `GET /topic/json`), our own foreground service + persistent notification. Reuses ntfy's server; we own the client code path. | ❌ Same iOS physics: our app would be suspended; sockets torn down. | Same as #1. | Small–medium: websocket/JSON-stream client + FGS + notification plumbing in the Android app. |
| **3. UnifiedPush (ntfy as self-hosted distributor + UP library in our app)** | ✅ Works: distributor app (ntfy) holds the connection, wakes our app via broadcast. Adopted by Element/Molly/Tusky/FluffyChat; active project (FOSDEM 2026 talk). Adds a user-side dependency: a distributor app must be installed and chosen. | ❌ **No UnifiedPush on iOS, and there will not be one** — iOS gives background network no carve-out for third-party apps; APNs is the only channel. | Same as #1 (ntfy is the reference distributor/server). | Medium: UP library registration, receiver for messages, endpoint handed to the hub, POST delivery. Over #2 it buys "user picks distributor/server" — low value for a first-party app + first-party server. |
| **4. WebPush from a self-hosted server (PWA + service worker)** | ❌ Not cloud-free: Chrome/Opera/Samsung push subscriptions point at **FCM endpoints**; Firefox at Mozilla's push service. Background delivery dies without them. A PWA *open in the foreground* could websocket to the hub, but that's not "backgrounded". | ❌ Not cloud-free: iOS 16.4+ PWA push rides APNs exclusively. | n/a | n/a — fails the hard constraint on both platforms. |
| **5. MQTT-over-Tailscale, connection inside our app** | ⚠️ Feasible but you rebuild ntfy's hard parts: Android 6+ Doze suspends network for all apps (Google's own docs recommend FCM for persistent connections); a foreground service protects process priority but does **not** by itself exempt you from Doze's network cut — you additionally need the battery-optimization whitelist prompt, keepalive tuning, and reconnect logic. OEM task killers add vendor-specific failure modes. | ❌ Not sustainable: iOS suspends backgrounded apps, may close their sockets, and `BGAppRefreshTask` gives ~30 s of *opportunistic, system-scheduled* runtime. No channel sustains a broker connection. | Mosquitto is tiny (~10–30 MiB), and the hub is already MQTT-centric. | High: everything in #2 plus connection lifecycle, keepalive/reconnect, QoS, and notification plumbing — i.e., re-implementing what ntfy already ships. |

## What actually works, per platform (sources)

### Android without FCM

- ntfy's "instant delivery": foreground service with a permanent notification maintains a constant connection to the server and delivers "even when the phone is in doze mode" ([docs.ntfy.sh/subscribe/phone](https://docs.ntfy.sh/subscribe/phone/)). The Play flavor uses FCM **only** for ntfy.sh and never for self-hosted servers; the F-Droid flavor contains no Firebase at all.
- Battery: constant connection costs ~0–1% of battery in 17 h of use ([docs.ntfy.sh/faq](https://docs.ntfy.sh/faq)).
- Doze baseline: network access is suspended, wakelocks ignored, jobs/alarms deferred; Google explicitly recommends FCM for apps that need persistent connections, which is exactly the constraint we're rejecting ([developer.android.com: Doze](https://developer.android.com/training/monitoring-device-state/doze-standby)).
- Practitioner reality check: a foreground service protects process priority but does not exempt from Doze's network cut; the reliable recipe is FGS + battery-optimization-whitelist prompt + ~30 s keepalive ([xckevin.com MQTT/Doze write-up](https://xckevin.com/en/blog/android-mqtt-qos-doze-keepalive/)); same story in [eclipse-paho #195](https://github.com/eclipse-paho/paho.mqtt.android/issues/195) and [#226](https://github.com/eclipse-paho/paho.mqtt.android/issues/226) (connections lost after ~15 min in standby). ntfy is the packaged, tuned version of this recipe.
- Over Tailscale specifically: the ntfy Android app supports custom HTTP headers (auth/proxy fronting), self-signed-cert trust and mTLS client certs ([docs — advanced settings](https://docs.ntfy.sh/subscribe/phone/)), and `ntfy://<host>/<topic>?secure=false` for plain HTTP. So a tailnet-only, un-exposed ntfy instance is fully usable.

### iOS without APNs

- Apple: backgrounded apps are suspended and "can no longer handle network traffic; in some cases, existing connections may even close" ([Apple: Platform-Specific Networking](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/NetworkingOverview/Platform-SpecificNetworkingTechnologies/Platform-SpecificNetworkingTechnologies.html)).
- The only generic background runtime is `BGAppRefreshTask`/`BGProcessingTask`: "the system decides" when to launch, ≤30 s of runtime, opportunistic, user-controllable via Background App Refresh ([Apple: Choosing Background Strategies](https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app), [WWDC25 session 227](https://developer.apple.com/videos/play/wwdc2025/227/)). A "poll the hub every 20 minutes" cadence is not something iOS lets you schedule reliably.
- ntfy's own iOS maintainer: "iOS does not allow scheduling regular background tasks at all" ([binwiederhier/ntfy #363](https://github.com/binwiederhier/ntfy/issues/363)); the docs' only instant-ish iOS path for self-hosted servers is `upstream-base-url` → a contentless `poll_request` relayed **through ntfy.sh's APNs** ([docs.ntfy.sh/config — iOS instant notifications](https://docs.ntfy.sh/config/)). That leaks delivery metadata to a third party and depends on public ntfy.sh → excluded by our constraint.
- UnifiedPush on iOS: "there is no UnifiedPush on iOS, and there will not be one… APNs is the only channel iOS grants that privilege to" ([bigiron.cc UnifiedPush explainer](https://bigiron.cc/guides/unifiedpush-self-hosted-push-for-android-explained)). Consistent with Apple's architecture.

**Honest iOS conclusion**: if/when the apps monorepo targets iOS, a cloud-free build can only offer alerts-on-open (pull when foregrounded) plus opportunistic `BGAppRefresh` refreshes — camera alerts will be delayed minutes-to-hours. Making iOS instant requires accepting APNs or the ntfy.sh upstream relay; that is a product decision for the decision ticket, not something engineering can work around.

### WebPush / PWA

- The Push API's `PushSubscription` endpoint is a **push service run by the browser vendor**; the service worker is woken by that service ([MDN: Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)).
- Chrome's push service is FCM; Firefox's is Mozilla's ([Chrome Developers: Web Push Interop](https://developer.chrome.com/blog/web-push-interop-wins)). VAPID ("Voluntary Application Server Identification") authenticates the *app server* to the *push service* — it removes the Firebase project/account dependency, not the vendor's servers, from the delivery path ([Firebase docs — VAPID for web](https://firebase.google.com/docs/cloud-messaging/web/get-started)).
- Even self-hosted ntfy's browser-push feature (VAPID keys via `ntfy webpush keys`, [docs.ntfy.sh/config](https://docs.ntfy.sh/config/)) ultimately hands messages to the browser's push service — same vendor transit.
- Verdict: **WebPush fails the no-vendor-cloud constraint on both Android and iOS** for backgrounded delivery. Only useful as a foreground/open-tab channel, where a plain websocket is simpler anyway.

### UnifiedPush maturity

- Protocol/projects actively maintained; used by Element, Molly, Tusky, FluffyChat; ntfy is the reference distributor and can point at a self-hosted server ([unifiedpush.org](https://unifiedpush.org/news/20221218_unifiedpush), [FOSDEM 2026 talk](https://fosdem.org/2026/events/attachments/7HJJS7-unifiedpush_-_push_notifications_decentralized_and_open_source/slides/267010/unifiedpu_vfjgzcb.pdf), [unifiedpush FAQ](https://unifiedpush.org/users/faq)).
- Integration shape: app broadcasts the `org.unifiedpush.android.distributor.REGISTER` intent, receives an endpoint URL, hands it to the app server; server POSTs to the endpoint; distributor wakes the app. For Renewvan this is extra machinery whose benefit (user-selectable distributor) doesn't apply — we own both ends. `embedded-fcm-distributor` exists but rides FCM → excluded.
- Android-only by construction (see iOS section).

## Server footprint on the Pi

- ntfy: single Go binary / arm64 docker image, ~13 MiB RAM idle measured ([selfhostatlas](https://selfhostatlas.com/self-host/ntfy), lists Pi 4 4 GB as suitable), ~52–55 MiB peak under a 100-request burst on Pi 5 ([peppe8o benchmarks](https://peppe8o.com/install-ntfy-raspberry-pi-benchmarks)); SQLite for the message cache (~KBs–MBs for text-only alerts). A 16th compose service at this size is noise on a 4 GB Pi.
- Mosquitto (for the MQTT option) is comparable or smaller, but the footprint is not the differentiator — client-side reliability engineering is.

## Reliability when the van is away from home

- Both ntfy and raw MQTT rely on the same thing off-LAN: the phone's Tailscale connection staying up on mobile data. Tailscale on Android is itself a foreground VPN service, which is exactly the mechanism ntfy uses — the two coexist fine, and the ntfy connection rides the tailnet like any TCP.
- Message-loss window: if the phone's tailnet path drops (no coverage, hub offline), ntfy's server-side cache (default 12 h, SQLite) re-delivers on reconnect; raw MQTT needs QoS 1 + broker session state to match ([docs.ntfy.sh/publish — message caching](https://docs.ntfy.sh/publish/)).
- Hub restarts: cache-file persists messages; nothing to build.

## Ranked candidates

1. **Self-hosted ntfy on the Pi (new compose service) — recommended.**
   - Phase 0 (today, zero app code): hub alert producers `POST` to `http://hub-tailnet-host:PORT/camera-alerts`; the official ntfy Android app (Play or F-Droid flavor) subscribes with instant delivery. Fully cloud-free, Doze-surviving, ~0–1% battery, sub-second delivery.
   - Phase 1: embed the ntfy subscribe protocol (JSON stream / websocket `GET /<topic>/json`) in the Renewvan Android app behind our own foreground service, so alerts render as first-class app notifications. ntfy server stays as-is.
   - iOS honestly: foreground-pull + opportunistic refresh only; decide separately whether iOS eventually accepts APNs or the ntfy.sh relay.
2. **MQTT-over-Tailscale embedded in the app — runner-up.**
   - Attractive because the hub is already MQTT-centric (no new service, no new protocol on the server). But client-side we would re-implement ntfy's FGS + Doze/whitelist + reconnect + notification stack, with known failure modes (paho #195/#226). Choose only if "no new compose service" outweighs client robustness.
3. **UnifiedPush — viable but not worth it here.** Adds a mandatory distributor app on the user's phone and protocol machinery whose main benefit (user choice of push provider) is irrelevant for a first-party app talking to a first-party server. Android-only, same as everything else.
4. **WebPush/PWA — rejected.** Background delivery transits vendor push services (FCM/Mozilla/APNs) by design; VAPID does not remove them. Fails the hard constraint on both platforms.

## Grill questions for the decision ticket

- Is "iOS instant alerts" a requirement, and if so who blinks first — the no-APNs constraint or the iOS requirement? (No engineering path satisfies both.)
- Do we ship Phase 0 with the stock ntfy app as the interim alert surface, or hold alerts until the app has embedded notifications?
- Expose ntfy tailnet-only (plus per-user access tokens) vs. also via a reverse proxy for sharing alerts with non-tailnet users (e.g. family)?
- If MQTT runner-up is chosen: are we ready to own Doze/whitelisting/OEM-killer support for a hand-rolled client forever?

## Sources

- ntfy docs: [FAQ](https://docs.ntfy.sh/faq) (battery, Firebase, self-hosting), [Subscribe from phone](https://docs.ntfy.sh/subscribe/phone/) (instant delivery, FGS, certs/headers, F-Droid), [Config](https://docs.ntfy.sh/config/) (iOS upstream relay, VAPID/webpush, footprint options), [Publish](https://docs.ntfy.sh/publish/) (message caching)
- ntfy issues: [ntfy #363](https://github.com/binwiederhier/ntfy/issues/363) (iOS background task limits), [ntfy-ios repo](https://github.com/binwiederhier/ntfy-ios) (bare-bones status, NSE/APNs architecture)
- Android: [Doze & App Standby](https://developer.android.com/training/monitoring-device-state/doze-standby), [AOSP power management](https://source.android.com/docs/core/power/platform_mgmt), [paho.mqtt.android #195](https://github.com/eclipse-paho/paho.mqtt.android/issues/195) / [#226](https://github.com/eclipse-paho/paho.mqtt.android/issues/226), [xckevin.com MQTT Doze keepalive](https://xckevin.com/en/blog/android-mqtt-qos-doze-keepalive/)
- iOS: [Choosing Background Strategies](https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app), [WWDC25 227](https://developer.apple.com/videos/play/wwdc2025/227/), [NetworkingOverview backgrounding](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/NetworkingOverview/Platform-SpecificNetworkingTechnologies/Platform-SpecificNetworkingTechnologies.html)
- UnifiedPush: [unifiedpush.org intro](https://unifiedpush.org/news/20221218_unifiedpush), [FAQ](https://unifiedpush.org/users/faq), [FOSDEM 2026 slides](https://fosdem.org/2026/events/attachments/7HJJS7-unifiedpush_-_push_notifications_decentralized_and_open_source/slides/267010/unifiedpu_vfjgzcb.pdf), [bigiron.cc explainer](https://bigiron.cc/guides/unifiedpush-self-hosted-push-for-android-explained)
- WebPush: [MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API), [Chrome Developers: Web Push Interop](https://developer.chrome.com/blog/web-push-interop-wins), [Firebase web/VAPID docs](https://firebase.google.com/docs/cloud-messaging/web/get-started)
- Footprint: [selfhostatlas ntfy](https://selfhostatlas.com/self-host/ntfy), [peppe8o Pi benchmarks](https://peppe8o.com/install-ntfy-raspberry-pi-benchmarks), [selfhosting.sh ntfy](https://selfhosting.sh/apps/ntfy)
