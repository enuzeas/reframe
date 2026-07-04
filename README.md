# reframe

Live 4K → 4×HD AI auto-reframing for OBS. One 4K camera in; up to four independently
cropped/auto-tracked HD channels out (RTSP + NDI), edited live from a browser console.

- **`reframe`** — CLI: 4K → HD crop with a fixed 3-mode layout, cv2 preview, optional 1-channel publish.
- **`reframe-server`** — control server: dynamic channels (max 4), per-channel RTSP/NDI, live
  preview + edit console, tracking bind, zoom presets, input switching.

Person detection is YOLO26n + ByteTrack; crop smoothing is a One Euro filter with a
median pre-filter. macOS / Apple Silicon (MPS), single process.

## Requirements

- macOS on Apple Silicon (uses MPS; falls back to CPU)
- Python ≥ 3.9
- [Homebrew](https://brew.sh) — for `ffmpeg` and `mediamtx`
- A UVC 4K capture source (e.g. Elgato Cam Link 4K) or any camera OpenCV can open
- **NDI output only:** NDI SDK runtime (`libndi.dylib`) via [NDI Tools](https://ndi.video/tools/)
  — proprietary, not brew-installable. RTSP output does not need it.

## Install

```bash
git clone https://github.com/enuzeas/reframe.git
cd reframe

brew bundle                       # ffmpeg + mediamtx (see Brewfile)

python3 -m venv .venv             # a venv is expected (service scripts look for .venv/bin)
source .venv/bin/activate
pip install -e .                  # registers the `reframe` and `reframe-server` commands
```

The YOLO26n model weight (`yolo26n.pt`) is downloaded automatically on first run.

> Added a new module later? Re-run `pip install -e .` — the editable install bakes in the
> module list, so a new file otherwise fails to import.

Verify without a camera:

```bash
reframe --self-test
reframe-server --self-test
```

## Usage

### A. Control server + console (the normal way)

```bash
mediamtx mediamtx.yml             # RTSP relay, TCP-only (see mediamtx.yml). Leave running.

reframe-server --src 0 \
  --rtsp-out-base rtsp://localhost:8554/out \
  --ndi-out-base reframe-out
```

Then open **http://localhost:8000** — the live preview with detection boxes and channel
crop rectangles. In the console you can:

- **Select** a channel (its card, or its box in the preview) — selection sticks until you
  pick another or press `Esc`.
- **Move / resize** the selected channel by dragging its box / corner (manual channels).
- **Bind tracking:** select a tracking channel, then click a detected person to follow them.
- **Zoom preset:** 수동(manual) / 풀샷(full) / 웨이스트업(waist) / 페이스(face).
- **Smoothing** slider (One Euro), **add/delete** channels, **presets** (MULTI / QUAD / SINGLE).
- **Input source:** pick a camera by thumbnail + resolution, apply.

Channels publish independently:

| Channel | RTSP | NDI |
|---|---|---|
| 1..4 | `rtsp://localhost:8554/out1` … `out4` | `reframe-out1` … `reframe-out4` |

Add each as a source in OBS. **Prefer NDI** — lower and steadier latency (~400ms vs RTSP's
larger, jittery buffering).

`--src` takes a camera index or a video file. `--mode 1|2|3` sets the startup preset only
(change live via the console); it's ignored once a saved layout exists (see Persistence).

### B. CLI (quick local test, no server)

```bash
reframe --src 0 --mode 1          # 2×2 cv2 preview window; 1=multi 2=quad 3=single
reframe --src 0 --mode 3 --rtsp-out rtsp://localhost:8554/out1   # publish tile 0
```

## Audio

Don't mux audio into the reframe streams — add it as a **separate track in OBS**. reframe
emits 4 independent video channels and OBS switches between them, so audio must be one
continuous track (muxing would duplicate it 4× and cut on every switch).

1. Add the mic as an **Audio Input Capture** source in OBS.
2. The reframed video arrives late (pipeline latency), so delay the audio to match:
   **Advanced Audio Properties → Sync Offset**, a positive value in ms (~400ms is a typical
   starting point with an NDI video source).
3. Verify by **playing back a recording**, not by monitoring — Sync Offset applies to the
   recorded/streamed mix only, not to live monitoring.

## Run as a service (launchd)

Auto-start on login, auto-restart on crash. Single process → two services (mediamtx +
reframe-server).

```bash
REFRAME_SRC=0 scripts/install-services.sh      # override args via env; default --src 0
```

Overridable: `REFRAME_SRC`, `REFRAME_RTSP_BASE`, `REFRAME_NDI_BASE`, `REFRAME_HOST`, `REFRAME_PORT`.
Plists are generated (not committed) with this machine's absolute paths.

```bash
launchctl print gui/$(id -u)/com.reframe.server | grep -i state   # status
scripts/uninstall-services.sh                                     # stop + remove
```

Logs: `~/Library/Logs/reframe/com.reframe.*.log`.

### Persistence

The channel layout (geometry, tracking on/off, zoom preset, smoothing) is saved to
`~/Library/Application Support/reframe/state.json` on every edit and restored on restart.
The tracked `target_id` and the input source are **not** restored (track IDs and camera
indices aren't stable across sessions). Delete that file to reset to the startup preset.

## Project layout

| File | What |
|---|---|
| `reframe.py` | CLI entry (3-mode crop) — splits into `smoothing`/`geometry`/`detection`/`tracking`/`modes`/`display`/`output` |
| `server.py` + `channels.py` | `reframe-server` — FastAPI control server, dynamic channels, RTSP/NDI, preview, WS overlay |
| `console/index.html` | The editing console (served at `/`) |
| `persistence.py` | Channel-layout save/restore |
| `sources.py` | Camera discovery (thumbnail-based; indices aren't trustworthy across sessions) |
| `output.py` | `RTSPPublisher` (ffmpeg/MediaMTX) + `NDIPublisher` (cyndilib) |
| `mediamtx.yml` | MediaMTX config — RTSP over TCP (UDP reproduces a stuck-frame bug here) |
| `scripts/` | `install-services.sh` / `uninstall-services.sh` |
| `docs/` | Design + rollout: `ROADMAP`, `PLAN`, `UI-PLAN`, `INFRA-PLAN`, `next`, `walkthrough` |

Start at [docs/walkthrough.md](docs/walkthrough.md) for a runnable tour, or
[docs/next.md](docs/next.md) for the current state and every fix's rationale.

## Notes

- **Trusted-LAN only.** No auth on the console (8000) or media ports — don't expose to the internet.
- Losing the tracked target falls back to the full frame (never a gray placeholder — that's a
  broadcast accident), so a dropped track degrades safely on air.
