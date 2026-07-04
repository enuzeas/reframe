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
from pathlib import Path

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import sources
from channels import PRESETS, Channel, next_channel_id, render_channels
from detection import detect_people
from output import NDIPublisher, RTSPPublisher, rtsp_cmd
from state import CommandQueue, PipelineState

CONSOLE_DIR = Path(__file__).parent / "console"
DETECT_EVERY = 2
MJPEG_FPS = 12
OVERLAY_HZ = 10
PREVIEW_MAX_WIDTH = 1280
MODE_TO_PRESET = {1: "multi", 2: "quad", 3: "single"}


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


def make_app(state: PipelineState, cmdq: CommandQueue) -> FastAPI:
    app = FastAPI()

    @app.get("/api/state")
    def get_state():
        _, overlay, source_id = state.snapshot()
        return {"fps": overlay.get("fps"), "source_id": source_id}

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
    """Channels waiting for a tracking target never hit clamp_window (render_channel
    returns early with a placeholder), so their x/y/w/h stay frozen in whatever pixel
    units they had at creation time. If the frame size drifts afterward - confirmed
    this session with a webcam (J0Sunvail) whose frames aren't even consistently
    sized frame-to-frame, per ultralytics' own GMC size-mismatch warnings - those
    stale pixel values can normalize to outside 0..1. Clamp defensively rather than
    let the client draw a rect off the edge of the canvas."""
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
        for cid in list(self.by_id):
            if cid not in channel_ids:
                for pub in self.by_id.pop(cid):
                    pub.close()
        for cid in channel_ids:
            if cid not in self.by_id:
                pubs = []
                if self.rtsp_base:
                    pubs.append(RTSPPublisher(f"{self.rtsp_base}{cid}", fps=self.fps,
                                               audio_src=self.audio_src))
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


def pipeline_loop(args, state: PipelineState, cmdq: CommandQueue, stop_event: threading.Event):
    import torch
    from ultralytics import YOLO

    model = YOLO(args.model)
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    def open_capture(src, width=None, height=None):
        cap = cv2.VideoCapture(src)
        if width and height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        return cap

    src = int(args.src) if args.src.isdigit() else args.src
    cap = open_capture(src)
    state.update(source_id=src if isinstance(src, int) else None)

    out_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    outputs = ChannelOutputs(args.rtsp_out_base, args.ndi_out_base, out_fps, audio_src=args.audio_src)

    channel_list = None  # built from the startup preset once we know the frame size
    fps, t0 = 0.0, time.time()
    people, frame_idx = [], 0

    while not stop_event.is_set():
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue
        fh, fw = frame.shape[:2]

        if channel_list is None:
            channel_list = PRESETS[MODE_TO_PRESET[args.mode]]([], fw, fh)

        for cmd in cmdq.drain():
            if cmd["type"] == "switch_input":
                cap.release()
                cap = open_capture(cmd["source_id"], cmd.get("width"), cmd.get("height"))
                state.update(source_id=cmd["source_id"])
            else:
                _apply_command(cmd, channel_list, people, fw, fh)

        if frame_idx % args.detect_every == 0:
            people = detect_people(model, frame, device=device)
        frame_idx += 1

        tiles = render_channels(frame, people, channel_list)
        outputs.sync([c.id for c in channel_list])
        for c in channel_list:
            outputs.write(c.id, tiles[c.id])

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

    cap.release()
    outputs.close_all()


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
    assert ch.render_channel(frame, {}, waiting) is not None  # placeholder image, doesn't crash
    assert ch._status(waiting, {}) == "waiting"

    bbox = (7, 1000, 200, 1600, 1800)  # tid, x1, y1, x2, y2 - tall bbox
    tracked = ch.Channel(3, 0, 0, 640, 360, tracking=True, target_id=7, zoom="face")
    ch.render_channel(frame, {7: bbox}, tracked)
    assert tracked.h < (1800 - 200)  # face preset zooms in tighter than the raw bbox
    assert ch._status(tracked, {7: bbox}) == "live"
    assert ch._status(tracked, {}) == "lost"  # target currently undetected

    state, cmdq = PipelineState(), CommandQueue()
    state.update(
        frame=np.zeros((1080, 1920, 3), np.uint8),
        overlay={"boxes": [], "channels": [{"id": 1, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3,
                                             "tracking": False, "target_id": None,
                                             "zoom": "manual", "smoothing": 50, "status": "live"}],
                 "fps": 30.0},
        source_id=0,
    )
    app = make_app(state, cmdq)
    client = TestClient(app)

    r = client.get("/api/state")
    assert r.status_code == 200 and r.json() == {"fps": 30.0, "source_id": 0}

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

    # audio mux: video-only cmd unchanged (no regression), audio_src adds a second
    # avfoundation input + Opus encode + wallclock timestamps on both (A/V sync)
    plain = rtsp_cmd("rtsp://x/out1", fps=30)
    assert "-use_wallclock_as_timestamps" not in plain and "-c:a" not in plain
    muxed = rtsp_cmd("rtsp://x/out1", fps=30, audio_src=2)
    assert muxed.count("-use_wallclock_as_timestamps") == 1  # video input only, not audio
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

    app = make_app(state, cmdq)
    import uvicorn
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        stop_event.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
