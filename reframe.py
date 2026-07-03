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
"""
import argparse
import sys
import time

import cv2
import numpy as np

HD_W, HD_H = 1920, 1080
DETECT_W = 960          # detection runs on this width, crops come from the source frame
EMA_ALPHA = 0.15        # ponytail: EMA + deadzone smoothing; upgrade to One-Euro filter if jitter matters
DEADZONE = 40           # px in source coords; moves smaller than this are ignored
MODE_NAMES = {1: "MULTI", 2: "QUAD+TRACK", 3: "SINGLE"}


class Smoother:
    """Per-track EMA smoothing with a deadzone so crops don't jitter."""

    def __init__(self, alpha=EMA_ALPHA, deadzone=DEADZONE):
        self.alpha = alpha
        self.deadzone = deadzone
        self.pos = {}

    def update(self, key, cx, cy):
        if key not in self.pos:
            self.pos[key] = (float(cx), float(cy))
            return self.pos[key]
        px, py = self.pos[key]
        if abs(cx - px) < self.deadzone and abs(cy - py) < self.deadzone:
            return px, py
        nx = px + self.alpha * (cx - px)
        ny = py + self.alpha * (cy - py)
        self.pos[key] = (nx, ny)
        return nx, ny

    def drop_except(self, keys):
        self.pos = {k: v for k, v in self.pos.items() if k in keys}


def clamp_window(cx, cy, cw, ch, fw, fh):
    """Clamp a cw x ch window centered at (cx, cy) inside a fw x fh frame."""
    cw, ch = min(cw, fw), min(ch, fh)
    x = int(round(cx - cw / 2))
    y = int(round(cy - ch / 2))
    x = max(0, min(x, fw - cw))
    y = max(0, min(y, fh - ch))
    return x, y, int(cw), int(ch)


def crop_hd(frame, cx, cy, ch):
    """Crop a 16:9 window of height ch centered at (cx, cy), resized to HD."""
    fh, fw = frame.shape[:2]
    cw = ch * 16 / 9
    x, y, w, h = clamp_window(cx, cy, cw, ch, fw, fh)
    return cv2.resize(frame[y:y + h, x:x + w], (HD_W, HD_H), interpolation=cv2.INTER_LINEAR)


def placeholder(label):
    img = np.full((HD_H, HD_W, 3), 32, np.uint8)
    cv2.putText(img, label, (60, HD_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 2, (200, 200, 200), 3)
    return img


def detect_people(model, frame):
    """Run tracking on a downscaled frame, return [(tid, x1, y1, x2, y2)] in source coords, largest first."""
    fh, fw = frame.shape[:2]
    scale = DETECT_W / fw
    small = cv2.resize(frame, (DETECT_W, int(fh * scale)))
    res = model.track(small, persist=True, classes=[0], verbose=False)[0]
    people = []
    if res.boxes is not None and res.boxes.id is not None:
        for box, tid in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.id.int().cpu().numpy()):
            x1, y1, x2, y2 = box / scale
            people.append((int(tid), x1, y1, x2, y2))
    people.sort(key=lambda p: (p[3] - p[1]) * (p[4] - p[2]), reverse=True)
    return people


def track_crop(frame, smoother, key, bbox, zoom=1.6):
    """HD crop following a person bbox; zoom = window height as multiple of person height."""
    _, x1, y1, x2, y2 = bbox
    cx, cy = smoother.update(key, (x1 + x2) / 2, (y1 + y2) / 2)
    return crop_hd(frame, cx, cy, max((y2 - y1) * zoom, HD_H))


def render_multi(frame, people, smoother):
    # ponytail: slot = track-id order; ids only grow, so tiles stay put until someone leaves.
    # Upgrade to sticky slot assignment if tile shuffling bothers you.
    chosen = sorted(people[:4], key=lambda p: p[0])
    smoother.drop_except({p[0] for p in chosen})
    tiles = [track_crop(frame, smoother, p[0], p) for p in chosen]
    while len(tiles) < 4:
        tiles.append(placeholder("no subject"))
    return tiles


def render_quad(frame, people, smoother):
    fh, fw = frame.shape[:2]
    hw, hh = fw // 2, fh // 2
    quads = [frame[:hh, :hw], frame[:hh, hw:], frame[hh:, :hw]]
    tiles = [cv2.resize(q, (HD_W, HD_H)) for q in quads]
    if people:
        tiles.append(track_crop(frame, smoother, "quad-main", people[0]))
    else:
        tiles.append(placeholder("no subject"))
    return tiles


def render_single(frame, people, smoother):
    if not people:
        return [cv2.resize(frame, (HD_W, HD_H))] + [placeholder("no subject")] * 3
    p = people[0]
    _, x1, y1, x2, y2 = p
    h = y2 - y1
    bx = (x1 + x2) / 2
    cx, _ = smoother.update("single", bx, (y1 + y2) / 2)
    dx = cx - bx  # reuse one smoothed offset for every framing
    framings = [
        (bx + dx, y1 + h * 0.50, h * 1.3),   # full body
        (bx + dx, y1 + h * 0.35, h * 0.7),   # waist-up
        (bx + dx, y1 + h * 0.15, h * 0.35),  # face close-up (upscales, unavoidable)
    ]
    tiles = [cv2.resize(frame, (HD_W, HD_H))]
    tiles += [crop_hd(frame, fx, fy, max(fh_, 240)) for fx, fy, fh_ in framings]
    return tiles


def composite(tiles, mode, fps):
    labels = {
        1: ["person 1", "person 2", "person 3", "person 4"],
        2: ["quad TL", "quad TR", "quad BL", "track"],
        3: ["wide", "full", "waist", "face"],
    }[mode]
    cells = []
    for tile, label in zip(tiles, labels):
        cell = cv2.resize(tile, (960, 540))
        cv2.putText(cell, label, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cells.append(cell)
    grid = np.vstack([np.hstack(cells[:2]), np.hstack(cells[2:])])
    cv2.putText(grid, f"[{MODE_NAMES[mode]}]  {fps:.0f} fps  keys: 1/2/3 mode, q quit",
                (16, 1060), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    return grid


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="0", help="camera index or video file")
    ap.add_argument("--mode", type=int, default=1, choices=[1, 2, 3])
    ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    from ultralytics import YOLO
    model = YOLO(args.model)

    src = int(args.src) if args.src.isdigit() else args.src
    cap = cv2.VideoCapture(src)
    if isinstance(src, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
    if not cap.isOpened():
        sys.exit(f"cannot open source: {args.src}")

    mode = args.mode
    smoother = Smoother()
    render = {1: render_multi, 2: render_quad, 3: render_single}
    fps, t0 = 0.0, time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        people = detect_people(model, frame)
        tiles = render[mode](frame, people, smoother)
        cv2.imshow("reframe", composite(tiles, mode, fps))

        dt, t0 = time.time() - t0, time.time()
        fps = 0.9 * fps + 0.1 * (1 / dt) if dt > 0 else fps
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key in (ord("1"), ord("2"), ord("3")):
            mode = int(chr(key))
            smoother = Smoother()

    cap.release()
    cv2.destroyAllWindows()


def self_test():
    # window clamping stays in bounds and keeps requested size
    assert clamp_window(0, 0, 1920, 1080, 3840, 2160) == (0, 0, 1920, 1080)
    assert clamp_window(3840, 2160, 1920, 1080, 3840, 2160) == (1920, 1080, 1920, 1080)
    assert clamp_window(1920, 1080, 9999, 9999, 3840, 2160) == (0, 0, 3840, 2160)
    # smoother: deadzone holds, big moves converge toward target
    s = Smoother(alpha=0.5, deadzone=10)
    assert s.update("a", 100, 100) == (100, 100)
    assert s.update("a", 105, 105) == (100, 100)          # inside deadzone
    nx, ny = s.update("a", 300, 100)
    assert 100 < nx < 300 and ny == 100                    # moved toward target
    # crop_hd output shape
    frame = np.zeros((2160, 3840, 3), np.uint8)
    assert crop_hd(frame, 0, 0, 1080).shape == (HD_H, HD_W, 3)
    print("self-test OK")


if __name__ == "__main__":
    main()
