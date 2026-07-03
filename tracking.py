"""Per-track crop following, stable tile slots, and hold-on-loss behavior."""
from geometry import crop_hd

HOLD_FRAMES = 30        # ~1s at 30fps: keep last framing after a target vanishes
WIDEN_PER_FRAME = 1.03  # crop grows by this factor per frame while a target is missing
MIN_CROP_FRACTION = 0.5  # never zoom tighter than this fraction of the source frame's
                         # height (matches HD_H/4K-height on a real 4K source; expressed
                         # as a ratio so sub-4K test sources like a 720p webcam still
                         # leave panning room instead of clamping to the whole frame)


def track_crop(frame, smoother, key, bbox, zoom=1.6, widen=1.0):
    """HD crop following a person bbox; zoom = window height as multiple of person height.
    widen > 1 pulls the crop back (used while a target is missing, see Presence)."""
    fh = frame.shape[0]
    _, x1, y1, x2, y2 = bbox
    cx, cy = smoother.update(key, (x1 + x2) / 2, (y1 + y2) / 2)
    ch = smoother.scalar(f"{key}:h", max((y2 - y1) * zoom, fh * MIN_CROP_FRACTION) * widen)
    return crop_hd(frame, cx, cy, ch)


class SlotManager:
    """Assigns each track id a stable tile slot; a slot stays empty rather than
    reshuffling everyone else's tile when its occupant leaves."""

    def __init__(self, n=4):
        self.slots = [None] * n

    def assign(self, track_ids):
        present = set(track_ids)
        for i, tid in enumerate(self.slots):
            if tid is not None and tid not in present:
                self.slots[i] = None
        slotted = {tid for tid in self.slots if tid is not None}
        for tid in track_ids:
            if tid in slotted:
                continue
            for i, occupant in enumerate(self.slots):
                if occupant is None:
                    self.slots[i] = tid
                    break
        return list(self.slots)


class Presence:
    """Holds a track's last known bbox for HOLD_FRAMES after it vanishes, widening the
    crop each frame it stays missing, instead of hard-cutting to a placeholder."""

    def __init__(self, hold_frames=HOLD_FRAMES):
        self.hold_frames = hold_frames
        self.last = {}
        self.missing = {}

    def resolve(self, key, bbox):
        if bbox is not None:
            self.last[key] = bbox
            self.missing[key] = 0
            return bbox, 1.0
        if key in self.last and self.missing.get(key, 0) < self.hold_frames:
            self.missing[key] = self.missing.get(key, 0) + 1
            return self.last[key], WIDEN_PER_FRAME ** self.missing[key]
        self.last.pop(key, None)
        self.missing.pop(key, None)
        return None, 1.0
