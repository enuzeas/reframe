#!/usr/bin/env python3
"""reframe: live 4K input -> 4 HD (1920x1080) auto-reframed views.

Modes (switch live with keys 1/2/3, q quits):
  1 MULTI  - up to 4 detected people, one tracking HD crop each (fancam style)
  2 QUAD   - 3 fixed quadrants + 1 tracking crop of the main person
  3 SINGLE - one person, 4 framings: wide / full body / waist-up / face

Usage:
  python reframe.py --src 0            # camera index
  python reframe.py --src video.mp4    # file (simulates live)
  python reframe.py --self-test

Code is split by feature: smoothing.py (One Euro Filter), geometry.py (crop math),
detection.py (YOLO+ByteTrack), tracking.py (slots/hold-on-loss), modes.py (the 3
render modes), display.py (debug compositing). This file is just the CLI entrypoint.
"""
import argparse
import sys
import time

import cv2
import numpy as np

from detection import detect_people
from display import composite
from geometry import HD_H, HD_W, clamp_window, crop_hd
from modes import render_multi, render_quad, render_single
from output import NDIPublisher, RTSPPublisher
from smoothing import Smoother
from tracking import Presence, SlotManager

DETECT_EVERY = 2        # ponytail: measured on M-series+MPS, 4K synthetic video (2026-07-04) -
                        # every=1 -> 15.6fps (misses 30fps target), every=2 -> 39.6fps.
                        # CoreML export skipped: this knob alone clears the target.


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="0", help="camera index or video file")
    ap.add_argument("--mode", type=int, default=1, choices=[1, 2, 3])
    ap.add_argument("--model", default="yolo26n.pt")
    ap.add_argument("--detect-every", type=int, default=DETECT_EVERY,
                     help="run YOLO every Nth frame, reusing the last boxes in between")
    ap.add_argument("--rtsp-out", help="rtsp:// URL to publish tile 0 to (e.g. rtsp://localhost:8554/out1)")
    ap.add_argument("--ndi-out", help="NDI source name to publish tile 0 as (e.g. reframe-out1)")
    ap.add_argument("--audio-src", type=int, default=None,
                     help="avfoundation audio device index to mux into --rtsp-out (see "
                          "sources.probe_audio_devices())")
    ap.add_argument("--no-preview", action="store_true", help="skip the cv2 debug window")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    import torch
    from ultralytics import YOLO
    model = YOLO(args.model)
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    src = int(args.src) if args.src.isdigit() else args.src
    cap = cv2.VideoCapture(src)
    if isinstance(src, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
    if not cap.isOpened():
        sys.exit(f"cannot open source: {args.src}")

    mode = args.mode
    smoother, slots, presence = Smoother(), SlotManager(), Presence()
    fps, t0 = 0.0, time.time()
    people, frame_idx = [], 0
    out_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    publishers = []
    if args.rtsp_out:
        publishers.append(RTSPPublisher(args.rtsp_out, fps=out_fps, audio_src=args.audio_src))
    if args.ndi_out:
        publishers.append(NDIPublisher(args.ndi_out, fps=out_fps))

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % args.detect_every == 0:
            people = detect_people(model, frame, device=device)
        frame_idx += 1

        if mode == 1:
            tiles = render_multi(frame, people, smoother, slots)
        elif mode == 2:
            tiles = render_quad(frame, people, smoother, presence)
        else:
            tiles = render_single(frame, people, smoother, presence)

        for pub in publishers:
            pub.write(tiles[0])
        if not args.no_preview:
            cv2.imshow("reframe", composite(tiles, mode, fps))

        dt, t0 = time.time() - t0, time.time()
        fps = 0.9 * fps + 0.1 * (1 / dt) if dt > 0 else fps
        key = cv2.waitKey(1) & 0xFF if not args.no_preview else -1
        if key == ord("q"):
            break
        if key in (ord("1"), ord("2"), ord("3")):
            mode = int(chr(key))
            smoother, slots, presence = Smoother(), SlotManager(), Presence()

    cap.release()
    cv2.destroyAllWindows()
    for pub in publishers:
        pub.close()


def self_test():
    # window clamping stays in bounds and keeps requested size
    assert clamp_window(0, 0, 1920, 1080, 3840, 2160) == (0, 0, 1920, 1080)
    assert clamp_window(3840, 2160, 1920, 1080, 3840, 2160) == (1920, 1080, 1920, 1080)
    assert clamp_window(1920, 1080, 9999, 9999, 3840, 2160) == (0, 0, 3840, 2160)
    # non-integer crop width hitting the frame edge must not leak a float into x/y
    # (regression: crashed frame[y:y+h, x:x+w] slicing with SINGLE mode's face close-up)
    x, y, _, _ = clamp_window(3840, 1080, 1000.7, 562.5, 3840, 2160)
    assert isinstance(x, int) and isinstance(y, int)
    # smoother: One Euro Filter lags toward a step change, then converges (t is explicit
    # so the test doesn't depend on real elapsed wall-clock time between calls)
    s = Smoother(min_cutoff=1.0, beta=0.0)
    t = 0.0
    assert s.update("a", 100, 100, t) == (100, 100)         # first sample: no history yet
    t += 1 / 30
    nx, ny = s.update("a", 300, 100, t)
    assert 100 < nx < 300 and ny == 100                     # lagged toward target, not snapped
    for _ in range(60):
        t += 1 / 30
        nx, ny = s.update("a", 300, 100, t)
    assert abs(nx - 300) < 1                                # converges after enough frames
    # scalar smoothing (used for zoom-rate limiting) behaves the same way
    assert s.scalar("a:h", 1000, t) == 1000                 # first sample: no history yet
    t += 1 / 30
    assert s.scalar("a:h", 400, t) > 400                    # lagged toward smaller target
    s.drop_except(set())
    assert s.filters == {}
    # crop_hd output shape
    frame = np.zeros((2160, 3840, 3), np.uint8)
    assert crop_hd(frame, 0, 0, 1080).shape == (HD_H, HD_W, 3)
    # slot manager: a leaving occupant frees its own slot, doesn't reshuffle the rest
    sm = SlotManager(n=4)
    assert sm.assign([3, 7, 12, 20]) == [3, 7, 12, 20]
    assert sm.assign([3, 12, 20]) == [3, None, 12, 20]      # 7 left -> its slot empties
    assert sm.assign([3, 12, 20, 99]) == [3, 99, 12, 20]    # newcomer fills the empty slot
    # presence: holds last bbox and widens while missing, then finally lets go
    pr = Presence(hold_frames=2)
    bbox = (1, 0, 0, 100, 100)
    assert pr.resolve("x", bbox) == (bbox, 1.0)
    held, widen = pr.resolve("x", None)
    assert held == bbox and widen > 1.0                     # held + widening
    pr.resolve("x", None)
    assert pr.resolve("x", None) == (None, 1.0)             # hold window expired
    print("self-test OK")


if __name__ == "__main__":
    main()
