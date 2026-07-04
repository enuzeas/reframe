#!/usr/bin/env python3
"""reframe-server: FastAPI control server for the reframe pipeline.

Read-only console (M4): live preview (MJPEG) + detection/overlay data (WebSocket)
+ input source/resolution switching. Crop editing and tracking rebinding stay
CLI/config-only until M5.

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

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

import sources
from detection import detect_people
from display import composite
from modes import render_multi, render_quad, render_single
from output import NDIPublisher, RTSPPublisher
from smoothing import Smoother
from state import CommandQueue, PipelineState
from tracking import Presence, SlotManager

DETECT_EVERY = 2
MJPEG_FPS = 12
OVERLAY_HZ = 10


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
        return {"mode": overlay.get("mode"), "fps": overlay.get("fps"), "source_id": source_id}

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

    return app


def _build_publishers(rtsp_base, ndi_base, fps):
    """One publisher list per output channel (1..4), URL/name suffixed by channel number."""
    channels = []
    for i in range(1, 5):
        pubs = []
        if rtsp_base:
            pubs.append(RTSPPublisher(f"{rtsp_base}{i}", fps=fps))
        if ndi_base:
            pubs.append(NDIPublisher(f"{ndi_base}{i}", fps=fps))
        channels.append(pubs)
    return channels


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
    channel_publishers = _build_publishers(args.rtsp_out_base, args.ndi_out_base, out_fps)

    smoother, slots, presence = Smoother(), SlotManager(), Presence()
    fps, t0 = 0.0, time.time()
    people, frame_idx = [], 0

    while not stop_event.is_set():
        for cmd in cmdq.drain():
            if cmd["type"] == "switch_input":
                cap.release()
                cap = open_capture(cmd["source_id"], cmd.get("width"), cmd.get("height"))
                state.update(source_id=cmd["source_id"])
                smoother, slots, presence = Smoother(), SlotManager(), Presence()

        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue

        if frame_idx % args.detect_every == 0:
            people = detect_people(model, frame, device=device)
        frame_idx += 1

        if args.mode == 1:
            tiles = render_multi(frame, people, smoother, slots)
        elif args.mode == 2:
            tiles = render_quad(frame, people, smoother, presence)
        else:
            tiles = render_single(frame, people, smoother, presence)

        for tile, pubs in zip(tiles, channel_publishers):
            for pub in pubs:
                pub.write(tile)

        dt, t0 = time.time() - t0, time.time()
        fps = 0.9 * fps + 0.1 * (1 / dt) if dt > 0 else fps

        # ponytail: overlay only carries raw detection boxes for now, not each
        # channel's crop rect (modes.py returns finished tiles, not geometry) -
        # good enough for M4's read-only console; add crop rects if M5's editor needs them.
        fh, fw = frame.shape[:2]
        boxes = [
            {"track_id": tid, "x1": x1 / fw, "y1": y1 / fh, "x2": x2 / fw, "y2": y2 / fh}
            for tid, x1, y1, x2, y2 in people
        ]
        state.update(
            frame=composite(tiles, args.mode, fps),
            overlay={"boxes": boxes, "channels": len(tiles), "fps": round(fps, 1), "mode": args.mode},
        )

    cap.release()
    for pubs in channel_publishers:
        for pub in pubs:
            pub.close()


def self_test():
    import numpy as np
    from fastapi.testclient import TestClient

    state, cmdq = PipelineState(), CommandQueue()
    state.update(
        frame=np.zeros((1080, 1920, 3), np.uint8),
        overlay={"boxes": [{"track_id": 1, "x1": 0.1, "y1": 0.1, "x2": 0.5, "y2": 0.5}],
                 "channels": 4, "fps": 30.0, "mode": 1},
        source_id=0,
    )
    app = make_app(state, cmdq)
    client = TestClient(app)

    r = client.get("/api/state")
    assert r.status_code == 200 and r.json() == {"mode": 1, "fps": 30.0, "source_id": 0}

    r = client.post("/api/input", json={"source_id": 1, "width": 1920, "height": 1080})
    assert r.status_code == 200
    assert cmdq.drain() == [{"type": "switch_input", "source_id": 1, "width": 1920, "height": 1080}]

    chunk = mjpeg_chunk(state)
    assert chunk is not None and b"--frame" in chunk and b"image/jpeg" in chunk

    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()
        assert data["mode"] == 1 and data["fps"] == 30.0

    print("server self-test OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="0", help="camera index or video file")
    ap.add_argument("--mode", type=int, default=1, choices=[1, 2, 3])
    ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--detect-every", type=int, default=DETECT_EVERY)
    ap.add_argument("--rtsp-out-base", help="e.g. rtsp://localhost:8554/out -> out1..out4")
    ap.add_argument("--ndi-out-base", help="e.g. reframe-out -> reframe-out1..reframe-out4")
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
