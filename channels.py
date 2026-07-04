"""Editable output channels: independent crop windows with optional tracking.

Sits alongside modes.py's fixed MULTI/QUAD/SINGLE presets (unchanged, still used
by reframe.py's plain-CLI path) rather than replacing them - this is the model
server.py/console use for free-form channel editing (UI-PLAN.md §2-3), matching
what mockup/index.html already validated with simulated data.
"""
from geometry import clamp_window, crop_hd, placeholder
from smoothing import Smoother
from tracking import Presence

ZOOM_MULT = {"full": 1.3, "waist": 0.7, "face": 0.35}  # reuses modes.py's render_single
                                                        # values (measured this session),
                                                        # not mockup's eyeballed 1.3/0.7/0.32
MIN_CROP_FRACTION = 0.5  # same rationale as tracking.py's constant - frame-relative floor
DEFAULT_W = 1440         # new-channel default width (source coords), matches mockup


def smoothing_to_min_cutoff(smoothing):
    """0-100 slider -> One Euro min_cutoff. Left = smooth/laggy, right = snappy/jittery."""
    return 0.5 + (smoothing / 100) * 4.5


class Channel:
    def __init__(self, id, x, y, w, h, tracking=False, target_id=None, zoom="manual", smoothing=50):
        self.id = id
        self.x, self.y, self.w, self.h = x, y, w, h
        self.tracking = tracking
        self.target_id = target_id
        self.zoom = zoom
        self.smoothing = smoothing
        self.smoother = Smoother(min_cutoff=smoothing_to_min_cutoff(smoothing))
        self.presence = Presence()
        self.status = "live"

    def set_smoothing(self, value):
        self.smoothing = value
        cutoff = smoothing_to_min_cutoff(value)
        self.smoother.min_cutoff = cutoff
        for f in self.smoother.filters.values():
            f.min_cutoff = cutoff

    def to_dict(self):
        return {
            "id": self.id, "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "tracking": self.tracking, "target_id": self.target_id,
            "zoom": self.zoom, "smoothing": self.smoothing, "status": self.status,
        }


def next_channel_id(channels, max_channels=4):
    used = {c.id for c in channels}
    for i in range(1, max_channels + 1):
        if i not in used:
            return i
    return None


def _status(channel, people_by_id):
    if not channel.tracking:
        return "live"
    if channel.target_id is None:
        return "waiting"
    return "live" if channel.target_id in people_by_id else "lost"


def render_channel(frame, people_by_id, channel):
    fh, fw = frame.shape[:2]
    key = f"ch{channel.id}"

    if not channel.tracking:
        cx, cy = channel.x + channel.w / 2, channel.y + channel.h / 2
        tile = crop_hd(frame, cx, cy, channel.h)
        channel.x, channel.y, channel.w, channel.h = clamp_window(cx, cy, channel.h * 16 / 9, channel.h, fw, fh)
        return tile

    bbox = people_by_id.get(channel.target_id) if channel.target_id is not None else None
    resolved, widen = channel.presence.resolve(key, bbox)
    if resolved is None:
        return placeholder("추적 대상 선택 대기" if channel.target_id is None else "대상 소실")

    _, x1, y1, x2, y2 = resolved
    cx, cy = channel.smoother.update(key, (x1 + x2) / 2, (y1 + y2) / 2)
    target_h = ZOOM_MULT[channel.zoom] * (y2 - y1) if channel.zoom in ZOOM_MULT else channel.h
    ch_h = channel.smoother.scalar(f"{key}:h", max(target_h, fh * MIN_CROP_FRACTION) * widen)
    tile = crop_hd(frame, cx, cy, ch_h)
    channel.x, channel.y, channel.w, channel.h = clamp_window(cx, cy, ch_h * 16 / 9, ch_h, fw, fh)
    return tile


def render_channels(frame, people, channels):
    """Returns {channel_id: HD tile}; also updates each channel's x/y/w/h/status in place
    so GET /api/channels and the overlay always reflect what's actually on screen."""
    people_by_id = {p[0]: p for p in people}
    tiles = {}
    for c in channels:
        tiles[c.id] = render_channel(frame, people_by_id, c)
        c.status = _status(c, people_by_id)
    return tiles


def preset_multi(people, fw, fh):
    cx, cy = (fw - DEFAULT_W) / 2, (fh - DEFAULT_W * 9 / 16) / 2  # centered until the first
                                                                   # render tick repositions
                                                                   # tracking channels anyway
    ids = [p[0] for p in people[:4]]
    chs = [
        Channel(i + 1, cx, cy, DEFAULT_W, DEFAULT_W * 9 / 16, tracking=True, target_id=tid, zoom="full")
        for i, tid in enumerate(ids)
    ]
    while len(chs) < 4:
        chs.append(Channel(len(chs) + 1, cx, cy, DEFAULT_W, DEFAULT_W * 9 / 16, tracking=True, zoom="full"))
    return chs


def preset_quad(people, fw, fh):
    qw, qh = fw / 2, fh / 2
    main_target = people[0][0] if people else None
    return [
        Channel(1, 0, 0, qw, qh, tracking=False),
        Channel(2, qw, 0, qw, qh, tracking=False),
        Channel(3, 0, qh, qw, qh, tracking=False),
        Channel(4, qw, qh, qw, qh, tracking=True, target_id=main_target, zoom="full"),
    ]


def preset_single(people, fw, fh):
    tid = people[0][0] if people else None
    return [
        Channel(1, 0, 0, fw, fh, tracking=False, zoom="manual"),
        Channel(2, 0, 0, DEFAULT_W, DEFAULT_W * 9 / 16, tracking=True, target_id=tid, zoom="full"),
        Channel(3, 0, 0, DEFAULT_W * 0.6, DEFAULT_W * 0.6 * 9 / 16, tracking=True, target_id=tid, zoom="waist"),
        Channel(4, 0, 0, DEFAULT_W * 0.3, DEFAULT_W * 0.3 * 9 / 16, tracking=True, target_id=tid, zoom="face"),
    ]


PRESETS = {"multi": preset_multi, "quad": preset_quad, "single": preset_single}
