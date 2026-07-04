#!/usr/bin/env python3
"""reframe-server: FastAPI control server for the reframe pipeline.

Editable console (M5): live preview (MJPEG, raw downscaled feed) + detection/
crop overlay data (WebSocket) + input source/resolution switching + channel
CRUD (crop move/resize/delete/add, tracking bind, zoom preset, smoothing) +
MULTI/QUAD/SINGLE presets. Matches the interaction mockup/index.html already
validated with simulated data (UI-PLAN.md §2-3) - channels.py is the real
version of that model.

Single process: the capture/detect/track/render loop runs in a background
thread, FastAPI/uvicorn runs the asyncio side. docs/INFRA-PLAN.md §2 originally
called for reframe-pipeline/reframe-api as separate OS processes (API crash
shouldn't kill the broadcast) - deferred to M6, noted there as a footnote.

Usage:
  reframe-server --src 0 --mode 1 --rtsp-out-base rtsp://localhost:8554/out --ndi-out-base reframe-out
  reframe-server --self-test
"""
import argparse
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import sources
from channels import PRESETS, Channel, next_channel_id, render_channels
from detection import detect_people
from output import NDIPublisher, RTSPPublisher, rtsp_cmd
from persistence import load_channels, save_channels
from state import CommandQueue, PipelineState

CONSOLE_DIR = Path(__file__).parent / "console"
DETECT_EVERY = 2
MJPEG_FPS = 12
OVERLAY_HZ = 10
PREVIEW_MAX_WIDTH = 1280
MODE_TO_PRESET = {1: "multi", 2: "quad", 3: "single"}
SAVE_DEBOUNCE_S = 2.0  # persist channel layout at most this often (INFRA-PLAN §8)


def mjpeg_chunk(state: PipelineState):
    """One multipart chunk for the preview stream, or None if no frame yet.

    Standalone so self_test() can call it directly - the endpoint's actual
    generator loops forever via time.sleep(), which a threadpool-backed test
    client can't cancel cleanly (the worker thread just keeps sleeping after
    the test's client disconnects, hanging process exit).
    """
    frame, _, _ = state.snapshot()
    if frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"


def make_app(state: PipelineState, cmdq: CommandQueue, rtsp_out_base=None, ndi_out_base=None) -> FastAPI:
    app = FastAPI()

    @app.get("/api/state")
    def get_state():
        _, overlay, source_id = state.snapshot()
        return {"fps": overlay.get("fps"), "source_id": source_id,
                "rtsp_out_base": rtsp_out_base, "ndi_out_base": ndi_out_base}

    @app.get("/api/sources")
    def get_sources():
        _, _, source_id = state.snapshot()
        return sources.probe_devices(skip_index=source_id)

    @app.get("/api/sources/{index}/thumbnail.jpg")
    def get_thumbnail(index: int):
        frame, _, source_id = state.snapshot()
        jpg = sources.thumbnail_jpeg(index, latest_frame=frame if index == source_id else None)
        if jpg is None:
            return Response(status_code=404)
        return Response(content=jpg, media_type="image/jpeg")

    @app.get("/api/sources/{index}/resolutions")
    def get_resolutions(index: int):
        """probe_resolutions() opens its own cv2.VideoCapture(index) - which fails
        (device already held open) for whichever camera the pipeline is currently
        running, always returning []. That silently hid the active camera's own
        resolution (4K included) from its own dropdown. Report what it's actually
        running at instead of re-probing in that one case."""
        _, overlay, source_id = state.snapshot()
        if index == source_id:
            fw, fh = overlay.get("frame_w"), overlay.get("frame_h")
            return [{"width": fw, "height": fh}] if fw and fh else []
        return [{"width": w, "height": h} for w, h in sources.probe_resolutions(index)]

    @app.post("/api/input")
    async def post_input(body: dict):
        cmdq.put({
            "type": "switch_input",
            "source_id": body["source_id"],
            "width": body.get("width"),
            "height": body.get("height"),
        })
        return {"ok": True}

    @app.get("/api/channels")
    def get_channels():
        _, overlay, _ = state.snapshot()
        return overlay.get("channels", [])

    @app.post("/api/channels")
    async def post_channel(body: dict):
        cmdq.put({"type": "add_channel", "x": body.get("x", 0), "y": body.get("y", 0),
                   "w": body.get("w", 1440), "h": body.get("h", 1440 * 9 / 16)})
        return {"ok": True}

    @app.patch("/api/channels/{channel_id}")
    async def patch_channel(channel_id: int, body: dict):
        cmdq.put({"type": "update_channel", "id": channel_id, **body})
        return {"ok": True}

    @app.delete("/api/channels/{channel_id}")
    async def delete_channel(channel_id: int):
        cmdq.put({"type": "delete_channel", "id": channel_id})
        return {"ok": True}

    @app.post("/api/preset/{name}")
    async def post_preset(name: str):
        if name not in PRESETS:
            return Response(status_code=404)
        cmdq.put({"type": "preset", "name": name})
        return {"ok": True}

    @app.get("/api/preview.mjpg")
    def get_preview():
        def gen():
            while True:
                chunk = mjpeg_chunk(state)
                if chunk is not None:
                    yield chunk
                time.sleep(1 / MJPEG_FPS)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/ws")
    async def ws_overlay(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                _, overlay, _ = state.snapshot()
                await ws.send_json(overlay)
                await asyncio.sleep(1 / OVERLAY_HZ)
        except WebSocketDisconnect:
            pass

    # Mounted last: console/index.html uses relative fetch("/api/...") and
    # ws://location.host/ws, so it has to be served from this same origin
    # rather than opened as a local file:// page (those calls would 404/fail).
    if CONSOLE_DIR.exists():
        app.mount("/", StaticFiles(directory=str(CONSOLE_DIR), html=True), name="console")

    return app


def _downscale(frame, max_width=PREVIEW_MAX_WIDTH):
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    return cv2.resize(frame, (max_width, int(h * max_width / w)))


def _normalized_channel(c, fw, fh):
    """render_channel() now updates x/y/w/h to the full frame while waiting/lost
    (falls back to showing the whole frame rather than a placeholder), so this no
    longer has stale frozen coordinates to worry about in that specific case - kept
    as a defensive clamp regardless, since a frame-size drift mid-flight (confirmed
    this session with a webcam whose frames weren't even consistently sized
    frame-to-frame, per ultralytics' own GMC warnings) could still normalize
    something to outside 0..1 in principle. Cheap insurance against the client
    drawing a rect off the edge of the canvas."""
    d = c.to_dict()
    d["x"] = max(0.0, min(1.0, d["x"] / fw))
    d["y"] = max(0.0, min(1.0, d["y"] / fh))
    d["w"] = max(0.0, min(1.0, d["w"] / fw))
    d["h"] = max(0.0, min(1.0, d["h"] / fh))
    return d


class ChannelOutputs:
    """Opens/closes RTSP/NDI publishers per channel id as channels are added/removed."""

    def __init__(self, rtsp_base, ndi_base, fps, audio_src=None):
        self.rtsp_base, self.ndi_base, self.fps = rtsp_base, ndi_base, fps
        self.audio_src = audio_src
        self.by_id = {}

    def sync(self, channel_ids):
        """Closing a publisher waits on its ffmpeg subprocess to exit (up to ~10s
        with the terminate()/kill() fallback) - doing that inline here blocks this
        method's caller, the single-threaded render loop, so *every* surviving
        channel's frames stall for that whole time whenever one channel is deleted.
        Confirmed live: deleting 3 channels produced multi-second gaps (up to 10.5s)
        in the render loop, which looked exactly like the frozen-video reports this
        was supposed to help diagnose. Close in the background instead."""
        for cid in list(self.by_id):
            if cid not in channel_ids:
                pubs = self.by_id.pop(cid)
                threading.Thread(target=lambda ps=pubs: [p.close() for p in ps], daemon=True).start()
        # Audio only goes to the lowest-id channel (the full-shot/original in every
        # preset) rather than all of them: four OBS sources would each carry their own
        # copy of the same mic, needing three muted manually, and opening the same
        # avfoundation device four times concurrently is unnecessary contention for no
        # benefit - one channel's audio is all a scene actually plays at once anyway.
        audio_channel_id = min(channel_ids) if channel_ids else None
        for cid in channel_ids:
            if cid not in self.by_id:
                pubs = []
                if self.rtsp_base:
                    pubs.append(RTSPPublisher(f"{self.rtsp_base}{cid}", fps=self.fps,
                                               audio_src=self.audio_src if cid == audio_channel_id else None))
                if self.ndi_base:
                    pubs.append(NDIPublisher(f"{self.ndi_base}{cid}", fps=self.fps))
                self.by_id[cid] = pubs

    def write(self, channel_id, tile):
        """A dead publisher (ffmpeg exited - mediamtx restart, network hiccup) must
        not kill the whole pipeline thread: drop it and let the next sync() reopen
        it fresh instead of raising out of the render loop."""
        try:
            for pub in self.by_id.get(channel_id, []):
                pub.write(tile)
        except OSError as e:
            print(f"reframe-server: channel {channel_id} output died ({e}), reopening next frame")
            for pub in self.by_id.pop(channel_id, []):
                try:
                    pub.close()
                except OSError:
                    pass

    def close_all(self):
        for pubs in self.by_id.values():
            for pub in pubs:
                pub.close()


def _apply_command(cmd, channel_list, people, fw, fh):
    """Mutates channel_list in place per command; returns nothing."""
    if cmd["type"] == "add_channel":
        cid = next_channel_id(channel_list)
        if cid is None:
            return
        channel_list.append(Channel(cid, cmd["x"], cmd["y"], cmd["w"], cmd["h"]))
    elif cmd["type"] == "update_channel":
        c = next((c for c in channel_list if c.id == cmd["id"]), None)
        if c is None:
            return
        for field in ("x", "y", "w", "h", "tracking", "target_id", "zoom"):
            if field in cmd:
                setattr(c, field, cmd[field])
        if "smoothing" in cmd:
            c.set_smoothing(cmd["smoothing"])
    elif cmd["type"] == "delete_channel":
        channel_list[:] = [c for c in channel_list if c.id != cmd["id"]]
    elif cmd["type"] == "preset":
        channel_list[:] = PRESETS[cmd["name"]](people, fw, fh)


def _channels_from_saved(saved):
    """Rebuild Channel objects from persisted dicts, or None if any are unusable (caller
    falls back to the startup preset). target_id is intentionally not restored - track IDs
    are session-scoped, so a tracking channel comes back waiting for a fresh bind."""
    try:
        return [Channel(d["id"], d["x"], d["y"], d["w"], d["h"],
                        tracking=d.get("tracking", False), zoom=d.get("zoom", "manual"),
                        smoothing=d.get("smoothing", 50)) for d in saved]
    except (KeyError, TypeError):
        return None


def pipeline_loop(args, state: PipelineState, cmdq: CommandQueue, stop_event: threading.Event):
    import torch
    from ultralytics import YOLO

    model = YOLO(args.model)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    # Writing each channel's tile to its own ffmpeg subprocess is blocking I/O on an
    # independent OS pipe per channel - no data dependency between them, and a
    # blocking write() releases the GIL, so threads give real parallelism here despite
    # Python's GIL. Measured: 4 channels written sequentially cost ~12ms/frame; this
    # write() is the second-biggest chunk of frame time after detection, and unlike
    # detection there's no accuracy/responsiveness to trade away to speed it up.
    write_pool = ThreadPoolExecutor(max_workers=4)

    def open_capture(src, width=None, height=None):
        # Blindly requesting 4K (the old default) isn't safe on every camera: confirmed
        # live that a camera whose native default is already a clean 1920x1080 (16:9)
        # degraded to 1920x1440 (4:3, same width, wrong aspect) the moment 3840x2160 was
        # explicitly requested and not supported - AVFoundation's fallback for an
        # unsupported request isn't "reject and keep the current mode," it's "pick some
        # other mode," and that pick isn't necessarily better than what the camera
        # already had. Probe actual confirmed candidates (highest first) instead of
        # asking for 4K and hoping.
        candidates = [(width, height)] if width and height else (
            sources.probe_resolutions(src) if isinstance(src, int) else []
        )
        cap = cv2.VideoCapture(src)
        # Default internal buffer holds a few frames - fine for playback, but for a live
        # pipeline it just adds latency (each read() can return an already-stale buffered
        # frame instead of the newest one). Not all backends honor this, but AVFoundation
        # does.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Setting the resolution on *this* capture isn't guaranteed to reproduce what
        # probe_resolutions() just confirmed on its own short-lived captures - confirmed
        # live that under real pipeline load (YOLO inference + 4 ffmpeg/NDI publishers
        # all running) the same camera that cleanly probes at 1920x1080 can still land
        # on a lower fallback here, apparently a timing/negotiation race rather than a
        # hard capability limit. Verify by actually reading a frame and retry with a
        # short delay (to give the driver time to finish negotiating under load) before
        # falling through to the next-best confirmed candidate.
        for w, h in candidates:
            for _ in range(4):
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                ok, frame = cap.read()
                if ok and (frame.shape[1], frame.shape[0]) == (w, h):
                    return cap
                time.sleep(0.15)
        return cap

    src = int(args.src) if args.src.isdigit() else args.src
    cap = open_capture(src)
    state.update(source_id=src if isinstance(src, int) else None)

    out_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    outputs = ChannelOutputs(args.rtsp_out_base, args.ndi_out_base, out_fps, audio_src=args.audio_src)

    channel_list = None  # restored from state.json, or the startup preset (below)
    fps, t0 = 0.0, time.time()
    people, frame_idx = [], 0
    layout_dirty, last_save = False, 0.0

    while not stop_event.is_set():
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue
        fh, fw = frame.shape[:2]

        if channel_list is None:
            # Restore the last saved layout if there is one (a service that auto-restarts
            # would otherwise lose the operator's channel setup on every reboot/crash);
            # --mode's preset is only the first-run default.
            saved = load_channels()
            channel_list = (_channels_from_saved(saved) if saved else None) \
                or PRESETS[MODE_TO_PRESET[args.mode]]([], fw, fh)

        for cmd in cmdq.drain():
            if cmd["type"] == "switch_input":
                cap.release()
                cap = open_capture(cmd["source_id"], cmd.get("width"), cmd.get("height"))
                state.update(source_id=cmd["source_id"])
            else:
                _apply_command(cmd, channel_list, people, fw, fh)
                layout_dirty = True  # source isn't persisted, only channel edits are

        if frame_idx % args.detect_every == 0:
            people = detect_people(model, frame, device=device)
        frame_idx += 1

        tiles = render_channels(frame, people, channel_list)
        outputs.sync([c.id for c in channel_list])
        futures = [write_pool.submit(outputs.write, c.id, tiles[c.id]) for c in channel_list]
        for f in futures:
            f.result()

        dt, t0 = time.time() - t0, time.time()
        fps = 0.9 * fps + 0.1 * (1 / dt) if dt > 0 else fps

        # detect_people() returns numpy float32 coords - json.dumps chokes on those,
        # so cast to plain Python types explicitly (only surfaces once someone's
        # actually detected, hence not caught in M4 where test frames had no people)
        boxes = [
            {"track_id": int(tid), "x1": float(x1) / fw, "y1": float(y1) / fh,
             "x2": float(x2) / fw, "y2": float(y2) / fh}
            for tid, x1, y1, x2, y2 in people
        ]
        state.update(
            frame=_downscale(frame),
            overlay={
                "boxes": boxes,
                "channels": [_normalized_channel(c, fw, fh) for c in channel_list],
                "fps": round(fps, 1),
                "frame_w": fw,
                "frame_h": fh,
            },
        )

        # ponytail: a debounced, atomic ~sub-ms JSON write every couple seconds - no
        # separate thread/timer needed. Runs in the render loop only when something
        # actually changed.
        if layout_dirty and time.time() - last_save >= SAVE_DEBOUNCE_S:
            save_channels(channel_list)
            layout_dirty, last_save = False, time.time()

    if channel_list is not None and layout_dirty:
        save_channels(channel_list)  # flush edits made inside the last debounce window
    cap.release()
    outputs.close_all()
    write_pool.shutdown(wait=False)


def self_test():
    import numpy as np
    from fastapi.testclient import TestClient

    import channels as ch

    # channels.py core logic, independent of the server/HTTP layer
    frame = np.zeros((2160, 3840, 3), np.uint8)
    fixed = ch.Channel(1, 100, 100, 640, 360, tracking=False)
    tile = ch.render_channel(frame, {}, fixed)
    assert tile.shape == (1080, 1920, 3)
    assert (fixed.x, fixed.y, fixed.w, fixed.h) == (100, 100, 640, 360)  # unclamped, in bounds

    waiting = ch.Channel(2, 0, 0, 640, 360, tracking=True, target_id=None)
    tile = ch.render_channel(frame, {}, waiting)
    assert tile is not None and tile.shape == (1080, 1920, 3)  # full-frame fallback, not a placeholder
    assert (waiting.x, waiting.y, waiting.w, waiting.h) == (0, 0, 3840, 2160)  # reflects the fallback, not frozen
    assert ch._status(waiting, {}) == "waiting"

    bbox = (7, 1000, 200, 1600, 1800)  # tid, x1, y1, x2, y2 - tall bbox
    tracked = ch.Channel(3, 0, 0, 640, 360, tracking=True, target_id=7, zoom="face")
    ch.render_channel(frame, {7: bbox}, tracked)
    assert tracked.h < (1800 - 200)  # face preset zooms in tighter than the raw bbox
    assert tracked.y <= 200  # crop top stays above the head (y1=200), anchor-based framing
    assert ch._status(tracked, {7: bbox}) == "live"
    assert ch._status(tracked, {}) == "lost"  # target currently undetected

    # waist and face must not collapse to the same size for an ordinary (non-distant)
    # bbox - previously both landed on the same shared MIN_CROP_FRACTION floor
    waist_ch = ch.Channel(4, 0, 0, 640, 360, tracking=True, target_id=7, zoom="waist")
    ch.render_channel(frame, {7: bbox}, waist_ch)
    face_ch = ch.Channel(5, 0, 0, 640, 360, tracking=True, target_id=7, zoom="face")
    ch.render_channel(frame, {7: bbox}, face_ch)
    assert waist_ch.h > face_ch.h

    # restore round-trip: saved layout rebuilds with geometry/zoom kept but target_id
    # dropped (stale track id), and a malformed record falls back to preset (None)
    restored = _channels_from_saved([tracked.to_dict()])
    assert restored[0].zoom == "face" and restored[0].target_id is None
    assert _channels_from_saved([{"id": 1}]) is None  # missing geometry -> preset fallback

    state, cmdq = PipelineState(), CommandQueue()
    state.update(
        frame=np.zeros((1080, 1920, 3), np.uint8),
        overlay={"boxes": [], "channels": [{"id": 1, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3,
                                             "tracking": False, "target_id": None,
                                             "zoom": "manual", "smoothing": 50, "status": "live"}],
                 "fps": 30.0},
        source_id=0,
    )
    app = make_app(state, cmdq, rtsp_out_base="rtsp://localhost:8554/out", ndi_out_base="reframe-out")
    client = TestClient(app)

    r = client.get("/api/state")
    assert r.status_code == 200 and r.json() == {
        "fps": 30.0, "source_id": 0,
        "rtsp_out_base": "rtsp://localhost:8554/out", "ndi_out_base": "reframe-out",
    }

    r = client.get("/api/channels")
    assert r.status_code == 200 and r.json()[0]["id"] == 1

    r = client.post("/api/channels", json={"x": 0, "y": 0, "w": 640, "h": 360})
    assert r.status_code == 200
    assert cmdq.drain() == [{"type": "add_channel", "x": 0, "y": 0, "w": 640, "h": 360}]

    r = client.patch("/api/channels/1", json={"tracking": True, "target_id": 7})
    assert r.status_code == 200
    assert cmdq.drain() == [{"type": "update_channel", "id": 1, "tracking": True, "target_id": 7}]

    r = client.delete("/api/channels/1")
    assert r.status_code == 200
    assert cmdq.drain() == [{"type": "delete_channel", "id": 1}]

    r = client.post("/api/preset/multi")
    assert r.status_code == 200
    assert cmdq.drain() == [{"type": "preset", "name": "multi"}]

    r = client.post("/api/preset/nonsense")
    assert r.status_code == 404

    r = client.post("/api/input", json={"source_id": 1, "width": 1920, "height": 1080})
    assert r.status_code == 200
    assert cmdq.drain() == [{"type": "switch_input", "source_id": 1, "width": 1920, "height": 1080}]

    # video always gets wallclock timestamps (declared fps is the camera's nominal rate,
    # not the pipeline's actual throughput - without this the encoder's PTS runs ahead
    # of real time and live players stall after the first frame). audio_src adds a
    # second avfoundation input + Opus encode - also wallclock, so both streams share the
    # same epoch-based time domain (mixing wallclock video with audio's own native/uptime
    # clock desynced the two enough that readers computed a negative start offset and
    # video decoding stalled after frame 0).
    plain = rtsp_cmd("rtsp://x/out1", fps=30)
    assert plain.count("-use_wallclock_as_timestamps") == 1 and "-c:a" not in plain
    muxed = rtsp_cmd("rtsp://x/out1", fps=30, audio_src=2)
    assert muxed.count("-use_wallclock_as_timestamps") == 2  # both inputs, same time domain
    assert "avfoundation" in muxed and ":2" in muxed and "libopus" in muxed

    chunk = mjpeg_chunk(state)
    assert chunk is not None and b"--frame" in chunk and b"image/jpeg" in chunk

    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()
        assert data["fps"] == 30.0

    print("server self-test OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="0", help="camera index or video file")
    ap.add_argument("--mode", type=int, default=1, choices=[1, 2, 3],
                     help="startup preset only (1=multi/2=quad/3=single); change live via /api/preset")
    ap.add_argument("--model", default="yolo26n.pt")
    ap.add_argument("--detect-every", type=int, default=DETECT_EVERY)
    ap.add_argument("--rtsp-out-base", help="e.g. rtsp://localhost:8554/out -> out1..out4")
    ap.add_argument("--ndi-out-base", help="e.g. reframe-out -> reframe-out1..reframe-out4")
    ap.add_argument("--audio-src", type=int, default=None,
                     help="avfoundation audio device index to mux into every RTSP channel "
                          "(see sources.probe_audio_devices(); NDI channels stay video-only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    state, cmdq = PipelineState(), CommandQueue()
    stop_event = threading.Event()
    thread = threading.Thread(target=pipeline_loop, args=(args, state, cmdq, stop_event), daemon=True)
    thread.start()

    app = make_app(state, cmdq, rtsp_out_base=args.rtsp_out_base, ndi_out_base=args.ndi_out_base)
    import uvicorn
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        stop_event.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
