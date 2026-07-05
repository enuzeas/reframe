"""Editable output channels: independent crop windows with optional tracking.

Sits alongside modes.py's fixed MULTI/QUAD/SINGLE presets (unchanged, still used
by reframe.py's plain-CLI path) rather than replacing them - this is the model
server.py/console use for free-form channel editing (UI-PLAN.md §2-3), matching
what mockup/index.html already validated with simulated data.
"""
from geometry import clamp_window, crop_hd, full_frame
from smoothing import Smoother
from tracking import Presence

ZOOM_MULT = {"full": 1.3, "waist": 0.7, "face": 0.35}  # reuses modes.py's render_single
                                                        # values (measured this session),
                                                        # not mockup's eyeballed 1.3/0.7/0.32
# Fraction of the tracked bbox's height, measured from the top (y1), that each preset
# centers its crop on - i.e. which *part* of the whole-body box to actually follow,
# not just how tightly to zoom. There's no dedicated face/head detector (only a
# whole-person box from YOLO), so this is an anatomical approximation: a standing
# adult's head/face center sits close to the top, "waist-up" framing centers around
# the chest/upper torso, and "full" stays at the whole-body midpoint. Replaces the
# previous approach (center on the whole-body midpoint always, then clamp upward
# after the fact so a tight crop doesn't slice the head off) with picking the right
# point to begin with.
# face=0.10 -> 0.16: confirmed live the bbox isn't always a full standing body (a
# close webcam framing gives a "person" box that's already mostly head/upper-torso),
# so 0.10 landed too high and cropped into the forehead/hair, missing the lower face.
# This is inherently a per-setup approximation without a real face detector - revisit
# if a different camera distance/mounting needs a different value.
ANCHOR_FRAC = {"full": 0.5, "waist": 0.28, "face": 0.16}
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
        # "manual" zoom's frozen crop height - deliberately separate from self.h, which
        # the full-frame safety fallback below overwrites whenever a target is missing/
        # unbound. manual mode used to just read back self.h, so one waiting period or
        # one lost-track event permanently corrupted its "fixed" size to full-frame.
        self.manual_h = h
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
    # "manual" never times out to the full-frame fallback once a target's been seen -
    # the whole point of manual is a framing the user picked on purpose, and blowing
    # that out to a wide shot because the subject turned away for a couple seconds is
    # the opposite of "fixed". Holds the last known position indefinitely instead.
    hold_frames = float("inf") if channel.zoom == "manual" else None
    resolved, widen = channel.presence.resolve(key, bbox, hold_frames=hold_frames)
    if resolved is None:
        # No target bound yet, or lost longer than Presence's hold window - never show
        # a bare gray placeholder on an actual broadcast output (that's a broadcast
        # accident waiting to happen), fall back to the full frame instead. Unlike the
        # old placeholder-only path, this also updates x/y/w/h to reflect what's
        # actually on screen instead of leaving them frozen at whatever pixel values
        # existed when the channel started waiting.
        channel.x, channel.y, channel.w, channel.h = 0, 0, fw, fh
        return full_frame(frame)

    _, x1, y1, x2, y2 = resolved
    anchor = ANCHOR_FRAC.get(channel.zoom, 0.5)
    cx, cy = channel.smoother.update(key, (x1 + x2) / 2, y1 + anchor * (y2 - y1))
    if channel.zoom in ZOOM_MULT:
        # Floor only applies to the auto-computed presets, which derive height from
        # the detected bbox and could otherwise shrink to near nothing for a distant/
        # small person. "manual" is a size the user picked deliberately - flooring it
        # to 50% of frame height defeated the entire point of setting a small size
        # before turning tracking on (confirmed: a 360px-tall manual box grew past
        # 1000px the moment tracking resolved a target).
        #
        # The floor itself is scaled by each preset's own multiplier relative to
        # "full" rather than one flat MIN_CROP_FRACTION for all three - confirmed live
        # that a single shared floor made "waist" and "face" collapse to the exact
        # same crop size (both 0.5x0.5 normalized) for a person at ordinary webcam
        # distance, since both 0.7x and 0.35x of their bbox height landed under the
        # same 50%-of-frame floor. Scaling preserves full > waist > face ordering
        # instead of flattening it, while still preventing each from shrinking to
        # near-zero for a very distant/small person.
        floor = MIN_CROP_FRACTION * (ZOOM_MULT[channel.zoom] / ZOOM_MULT["full"])
        target_h = max(ZOOM_MULT[channel.zoom] * (y2 - y1), fh * floor)
        ch_h = channel.smoother.scalar(f"{key}:h", target_h * widen)
    else:
        # "manual" has no bbox to re-derive target_h from each frame - it reads back
        # channel.h, which clamp_window below overwrites with whatever ch_h comes out
        # to. That makes it self-referential: applying `widen` here (meant to buy a
        # preset a bit of margin while a target is ambiguously missing) has nothing
        # external to snap back to once the target reappears, so each missed-detection
        # frame permanently ratchets the size up by another `widen` factor. Confirmed:
        # 15 frames of the target briefly not detected (ordinary tracking noise, not
        # even a real occlusion) inflated a 360px-tall manual box to full frame height
        # and it never recovered even after 10 more frames of clean redetection.
        # Manual mode should just hold the size the user picked; widen doesn't apply.
        # Reads manual_h, not self.h - self.h gets overwritten by the full-frame
        # fallback (e.g. the "waiting to bind" period before target_id is set), which
        # would otherwise corrupt this "fixed" size the same way widen used to.
        target_h = channel.manual_h
        ch_h = channel.smoother.scalar(f"{key}:h", target_h)
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
