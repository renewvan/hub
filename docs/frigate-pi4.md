# Frigate with CPU person detection on the Pi 4 hub — resource reality

Research ticket: #227 (part of #222). Written 2026-11-27.
Question: what does running Frigate with CPU person detection on the Pi 4 hub actually cost, and is Pi-local inference viable?

Environment assumed (per ticket, verified separately): Pi 4 Model B rev 1.5, 4 GB RAM with ~1.3 Gi already in use by 15 hub containers, ~100 GB free on `/dev/mmcblk0p2`, one camera (WS01) sub-stream 640x360 H.264 ~15 fps over RTSP via go2rtc, no Coral TPU, fully local / open-source only.

## Verdict

**Go — with hard limits, as a single-camera pilot.** CPU-only person detection of one 360p sub-stream is workable on the Pi 4 *if* detection is throttled to 1–2 fps, only `person` is tracked, recording is events-only, and the container is fenced with `mem_limit` / `cpus` so the worst case is a Frigate restart, never a host OOM that takes the 15 co-tenant services down. Explicit **no-go** on this host: 5 fps detection, more than ~1–2 cameras, continuous recording, and any enrichments / semantic search. Note this puts us *below* Frigate's own published floor: the docs' 4 GB "Basic Minimum" tier assumes a dedicated detection accelerator, which we don't have.

## 1. CPU cost of CPU-only detection at 360p

**Detector defaults.** If no detector is configured, "Frigate will use a single CPU detector" — a TensorFlow Lite model (SSD MobileNet, `/cpu_model.tflite`), `num_threads` default 3. The docs classify the CPU detector under "For Testing" as "not recommended for actual use", and suggest OpenVINO CPU mode instead — but OpenVINO on Arm64 CPU is itself "generally not recommended" in the hardware docs, so on a Pi 4 without an accelerator the TFLite CPU detector is the practical open-source choice. There is no lighter *drop-in* open-source detector for Pi 4: Hailo/DEEPX/MemryX hats are Pi 5–oriented hardware purchases (Coral is separate fog per #222), RKNN is Rockchip-only, and the default SSD-MobileNet is already among the lightest supported models. The real levers are config, not model: detect fps, `num_threads`, and person-only tracking.

**Real-world Pi 4 numbers (community, not docs):**

| Report | Setup | Result |
| --- | --- | --- |
| Frigate discussion #3361 (NickM-27, maintainer) | Pi 4, CPU detector | ~350–400 ms inference per frame in practice (docs' old 10–15 ms claim did not reproduce) |
| Seeed studio test | Pi 4, CPU detector, 1080p30 stream | 177.96 ms inference ≈ 5.6 fps detection ceiling |
| damianklima.com write-up | Pi 4, 720p sub-stream, `fps: 1`, 3 threads | 20–40% CPU during detection, 2–5% CPU idle, 1–3 s detection latency |
| HA community thread 724399 | Pi 4, CPU detector | ffmpeg uses as much or *more* CPU than detection; even with `h264_v4l2m2m` hwaccel, ≥ ~30% CPU (Pi 4 GPU is weak) |
| Frigate discussion #5742 | Pi 4 + USB Coral, 9 cams @ 720p | 40–50% CPU — i.e. decode/capture overhead alone approaches half the Pi |

**What this means for us.** Effective detection rate on CPU will be ~2–5 fps at best (inference 180–400 ms); the default `detect.fps: 5` will not be met and should be lowered to 1–2. Frigate only runs inference on frames with motion, so steady-state idle cost is small (2–5% CPU). Decode is the hidden cost: the *detect* input on the 640x360 sub-stream is cheap, and `record` on the main stream runs with `-c:v copy` (no transcode) — the expensive path is CPU-decoding the main stream for live view, which we avoid by letting go2rtc serve live view and keeping the record output as stream copy. Budget roughly **20–50% of total Pi CPU during motion events** for one camera, a few percent idle. Optional `gpu_mem` bump + `h264_v4l2m2m` hwaccel helps ffmpeg but is not transformative on Pi 4.

## 2. RAM on a 4 GB host with 1.3 Gi already committed

Published tiers (docs / planning_setup): 4 GB = "Basic Minimum… a few cameras **and a dedicated object detection accelerator**, without any enrichments. Performance might be tight." 8 GB minimum for enrichments, 16 GB recommended for many cameras. We have no accelerator and only ~2.7 GiB free — below the published floor, hence the hard limits.

Component budget for one 360p camera:

- Frigate main python process: ~450–490 MiB RSS typical; community reports of growth to 1.0–1.3 GiB over 8–12 h (discussion #25020, v0.18.2) and 1.4–1.8 GiB on a multi-camera Pi 4 (discussion #16581). One camera, person-only, no enrichments should sit near the low end, but the growth reports are why the `mem_limit` is non-negotiable.
- Shared memory (`shm_size`): docs formula `(width × height × 1.5 + 270480) ÷ 1048576 × 25 frames` → 640x360 ≈ **14.7 MB per camera**; ~**94 MB** for 1 camera + 2-camera headroom + ~50 MB logs/cache → set `shm_size: "128mb"` (Docker's default 64 MB is what produces "Bus error" crashes).
- `/tmp/cache` tmpfs: docs recommend 1 GB; it is memory-backed and counts against the container limit. On this host use ~500 MB, or omit and accept SD-card writes.
- ffmpeg/go2rtc: a few hundred MB across processes at one camera.

**Realistic total: ~0.8–1.5 GB.** Fits in the free ~2.7 GiB, but with little slack — protect the co-tenants:

```yaml
services:
  frigate:
    image: ghcr.io/blakeblackshear/frigate:stable-standard-arm64
    restart: unless-stopped
    shm_size: "128mb"
    mem_limit: 2g          # hard ceiling; OOM kills Frigate, not the hub
    memswap_limit: 2g      # no swap spill onto the SD card
    cpus: 2.0              # cap at half the 4 cores
    ulimits:
      nofile:
        soft: 65535
        hard: 65535
```

(A Pi 4 user in #16581 ran Frigate with a 3.5 GB limit precisely so that memory-pressure crashes restarted Frigate instead of hanging the Pi — same idea.)

## 3. Disk math — events-only, no continuous recording

Frigate records in 10-second segments and retains only segments overlapping tracked-object events (pre/post capture, default 5 s + 5 s). Storage is essentially *bitrate × time kept*; the camera's main-stream bitrate is what lands on disk.

- Rule of thumb: **GB/day = Mbit/s × 10.8** (continuous). Real measurement backing it: 16.4 GiB/day for a 1440p Tapo at 1.63 Mbit/s (helencam).
- Per event: a ~60 s clip at 2–4 Mbit/s main stream ≈ **15–30 MB**.

Events/day scenarios (60 s average clip) on the ~100 GB free:

| Events/day | @ 2 Mbit/s | @ 4 Mbit/s |
| --- | --- | --- |
| 20 | 0.3 GB/day → ~1 year | 0.6 GB/day → ~5.5 months |
| 50 | 0.75 GB/day → ~4.4 months | 1.5 GB/day → ~2 months |
| 100 | 1.5 GB/day → ~2 months | 3.0 GB/day → ~1 month |
| 200 | 3.0 GB/day → ~1 month | 6.0 GB/day → ~17 days |

**Disk is a non-issue for events-only retention of one camera.** A 14–30 day event retention plus snapshots fits comfortably even in the pessimistic rows; snapshots are negligible. Recommended: `retain: days: 0` for continuous (i.e. keep none) and events retained ~14–30 days, then verify against Frigate's *System > Storage* page, which shows real per-camera per-day figures. One caveat: `/dev/mmcblk0p2` is a microSD card — a few GB/day of event writes plus segment-cache churn is acceptable but argues for the `/tmp/cache` tmpfs and for not enabling continuous recording.

## 4. Config pattern — sub-stream detect + main-stream event clips, fed from go2rtc

Using Frigate's bundled go2rtc (canonical "With Sub Stream" pattern from the restream docs):

```yaml
# Frigate <= 0.15 syntax; in 0.17+ the CPU detector is the default when
# nothing is configured (models: devices: [cpu]).
detectors:
  cpu:
    type: cpu
    num_threads: 3

go2rtc:
  streams:
    ws01_main:
      - rtsp://user:pass@CAM_IP/main-stream-uri
    ws01_sub:
      - rtsp://user:pass@CAM_IP/sub-stream-uri

cameras:
  ws01:
    ffmpeg:
      output_args:
        record: preset-record-generic-audio-copy  # -c:v copy, no transcode
      inputs:
        - path: rtsp://127.0.0.1:8554/ws01_main
          input_args: preset-rtsp-restream
          roles: [record]
        - path: rtsp://127.0.0.1:8554/ws01_sub
          input_args: preset-rtsp-restream
          roles: [detect]
    detect:
      width: 640
      height: 360
      fps: 2              # CPU inference can't sustain the 5 fps default
    objects:
      track: [person]     # person-only: cheapest and all we need

record:
  enabled: true
  retain:
    days: 0               # no continuous retention — events only
  events:
    retain:
      default: 30         # tune to taste; see disk math above

mqtt:
  enabled: false          # or true if the hub broker should get events
```

Since go2rtc is already planned as its own service on the Pi: point the ffmpeg `inputs` directly at the standalone go2rtc (`rtsp://go2rtc-host:8554/ws01_sub`) and drop `preset-rtsp-restream` (that preset exists for Frigate's *bundled* go2rtc). Either way, one camera connection is maintained and both Frigate roles plus live view share it — that is the point of the restream.

## 5. Known failure modes / dealbreakers on Pi 4 4 GB

1. **Host OOM is the dealbreaker risk.** Only ~2.7 GiB free; documented memory-growth/OOM reports on shared hosts (#25020 main process growing to OOM; #16581 Pi 4; #20258 OOM kill → `frigate.db` corruption on a Pi 5). The kernel OOM killer picks the biggest process, which could be a co-tenant. Mitigation: `mem_limit: 2g` + `memswap_limit` + `restart: unless-stopped`, single camera, no enrichments.
2. **"Bus error" crashes** from Docker's default 64 MB shm — fixed by `shm_size: "128mb"` (docs: ~210 MB for 2 cams @ 720p; we need ~94 MB).
3. **ffmpeg overhead exceeding detector overhead** on Pi 4 — avoid by: sub-stream for detect, `-c:v copy` for record, go2rtc for live view. Optional `h264_v4l2m2m` + raised `gpu_mem` (config.txt) still leaves ~30% CPU for decode of a main stream; don't CPU-decode the main stream for live view.
4. **5 fps default detect not achievable on CPU** (180–400 ms inference). At 1–2 fps a fast crossing can be missed; 1–3 s detection latency is the accepted trade-off for property monitoring.
5. **Enrichments / semantic search: hard no** on this host — docs require ≥8 GB and state a Raspberry Pi "will not run Semantic Search reliably or at all".
6. **`/tmp/cache` on tmpfs counts against `mem_limit`** — size it deliberately (500 MB or omit).
7. **SD-card wear** from segment cache churn — tmpfs cache, events-only retention, and monitor with `docker stats` + Frigate *System > Health* (inference speed ms, detection fps, skipped frames).

## Sources

- Frigate docs — planning_setup (RAM tiers, AVX requirement): docs.frigate.video/frigate/planning_setup
- Frigate docs — installation (shm formula, storage paths, Pi 3/4 gpu_mem, pids/fd limits): docs.frigate.video/frigate/installation
- Frigate docs — recommended hardware (detector list, OpenVINO on Arm64 "generally not recommended", Coral deprecation note): docs.frigate.video/frigate/hardware (dev-branch source: blakeblackshear/frigate `docs/docs/frigate/hardware.md`)
- Frigate docs — object detectors (CPU detector = TFLite, "not recommended", num_threads default 3, single CPU detector as default, model-size guidance): blakeblackshear/frigate `docs/docs/configuration/object_detectors.md`
- Frigate docs — restream / live (go2rtc sub-stream config pattern, 640x360 detect example): docs.frigate.video/configuration/restream, docs.frigate.video/configuration/live
- Frigate discussion #3361 — Pi 4 CPU detector 350–400 ms inference (maintainer report)
- Frigate discussion #5742 — Pi 4 + Coral, 9 cams, 40–50% CPU (decode/capture overhead baseline)
- Frigate discussion #16581 — Pi 4, python 1.4–1.8 GiB, memory-limit workaround
- Frigate discussion #25020 — v0.18.2 main process grows 450 MiB → 1.0–1.3 GiB until OOM
- Frigate discussion #20258 — OOM kill → frigate.db corruption
- Home Assistant community 724399 — ffmpeg CPU ≥ detector CPU on Pi 4, ~30%+ even with v4l2m2m
- Seeed studio blog — Pi 4 CPU detector 177.96 ms ≈ 5.6 fps @ 1080p
- damianklima.com — Pi 4 CPU detector @ 1 fps: 20–40% CPU active, 2–5% idle, 1–3 s latency
- charleshartmann (dev.to) — retention math, GB/day = Mbit/s × 10.8
- helencam.com/frigate/retention — measured 16.4 GiB/day/cam continuous @ 1.63 Mbit/s

## Bottom line for #222 planning

Treat Frigate-on-Pi as a bounded pilot, not a platform: one camera, 360p detect @ 1–2 fps, person-only, events-only clips, container fenced to 2 GB RAM / 2 CPUs / 128 MB shm. Expected steady cost: ~1 GB RAM, 20–50% CPU during motion, <1–3 GB disk per day worst case. If it holds for a few weeks without starving the hub containers, it's viable; scaling to more cameras or richer models on this Pi is a no — that's what the (separately tracked) accelerator fog is for.
