"""The three output modes: MULTI, QUAD+TRACK, SINGLE."""
import cv2

from geometry import HD_H, HD_W, crop_hd, placeholder
from tracking import track_crop

MODE_NAMES = {1: "MULTI", 2: "QUAD+TRACK", 3: "SINGLE"}


def render_multi(frame, people, smoother, slots):
    by_id = {p[0]: p for p in people[:4]}
    slot_ids = slots.assign(list(by_id.keys()))
    smoother.drop_except({tid for tid in slot_ids if tid is not None})
    tiles = []
    for tid in slot_ids:
        if tid is not None:
            tiles.append(track_crop(frame, smoother, tid, by_id[tid]))
        else:
            tiles.append(placeholder("no subject"))
    return tiles


def render_quad(frame, people, smoother, presence):
    fh, fw = frame.shape[:2]
    hw, hh = fw // 2, fh // 2
    quads = [frame[:hh, :hw], frame[:hh, hw:], frame[hh:, :hw]]
    tiles = [cv2.resize(q, (HD_W, HD_H)) for q in quads]
    bbox, widen = presence.resolve("quad-main", people[0] if people else None)
    if bbox is not None:
        tiles.append(track_crop(frame, smoother, "quad-main", bbox, widen=widen))
    else:
        tiles.append(placeholder("no subject"))
    return tiles


def render_single(frame, people, smoother, presence):
    p, widen = presence.resolve("single", people[0] if people else None)
    if p is None:
        return [cv2.resize(frame, (HD_W, HD_H))] + [placeholder("no subject")] * 3
    _, x1, y1, x2, y2 = p
    h = y2 - y1
    bx = (x1 + x2) / 2
    cx, _ = smoother.update("single", bx, (y1 + y2) / 2)
    dx = cx - bx  # reuse one smoothed offset for every framing
    framings = [
        ("full", bx + dx, y1 + h * 0.50, h * 1.3),   # full body
        ("waist", bx + dx, y1 + h * 0.35, h * 0.7),  # waist-up
        ("face", bx + dx, y1 + h * 0.15, h * 0.35),  # face close-up (upscales, unavoidable)
    ]
    tiles = [cv2.resize(frame, (HD_W, HD_H))]
    for name, fx, fy, fh_ in framings:
        ch = smoother.scalar(f"single:{name}", max(fh_, 240) * widen)
        tiles.append(crop_hd(frame, fx, fy, ch))
    return tiles
