"""One Euro Filter smoothing for tracked positions and sizes."""
import math
import time

ONEEURO_MIN_CUTOFF = 3.0  # ponytail: bumped from 1.0 (2026-07-04 live test) - at 1.0 the
                          # filter treated ordinary head movement as noise to suppress,
                          # only reacting to fast/large moves (subject leaving frame).
                          # lower = smoother but laggier; raise further if still sluggish.
ONEEURO_BETA = 0.0        # ponytail: dropped 0.7->0.15->0.02->0.0 (2026-07-04) - beta scales
                          # cutoff by *velocity*, i.e. it exists specifically to react
                          # faster/less-smoothed during fast movement. Explicit user
                          # preference: fast movement is fine to lag behind smoothly rather
                          # than snap to catch up, so that whole mechanism is unwanted here,
                          # not just "turned down". At 0.0 the filter is a plain constant-
                          # cutoff low-pass - min_cutoff (the UI's smoothing slider) is now
                          # the only thing controlling responsiveness, uniformly regardless
                          # of how fast the target is moving.
ONEEURO_D_CUTOFF = 1.0    # derivative cutoff, rarely needs tuning


class _OneEuro:
    """One Euro Filter for a single scalar signal (Casiez et al. 2012)."""

    def __init__(self, min_cutoff, beta, d_cutoff):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, x, t):
        if self.t_prev is None:
            self.x_prev, self.t_prev = x, t
            return x
        dt = max(t - self.t_prev, 1e-3)
        a_d = self._alpha(self.d_cutoff, dt)
        dx = (x - self.x_prev) / dt
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat


MEDIAN_WINDOW = 3  # ponytail: One Euro is a low-pass filter - it dampens a single-frame
                   # YOLO outlier (occasional misdetected bbox corner) but doesn't reject
                   # it outright, so a big enough spike still visibly perturbs the output.
                   # Confirmed with a synthetic test (steady drift + per-frame noise +
                   # occasional +-40 unit glitches, ~8% of frames): a median-of-3 prefilter
                   # dropped average frame jitter from 5.09 to 0.41 and the worst single-
                   # frame jump from 29.8 to 2.2. Cheap (3-sample window, no extra
                   # detection calls) - unlike raising detect_every, which needs a real
                   # YOLO call per frame and cost too much fps to be worth it (~30fps ->
                   # ~15fps at 4 channels, tested live).


class Smoother:
    """Per-track One Euro Filter smoothing, independent per axis (x/y/size). A short
    rolling median runs ahead of each axis's One Euro filter to reject single-frame
    outliers (see MEDIAN_WINDOW) before the low-pass smoothing proper."""

    def __init__(self, min_cutoff=ONEEURO_MIN_CUTOFF, beta=ONEEURO_BETA, d_cutoff=ONEEURO_D_CUTOFF):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.filters = {}  # (key, axis) -> _OneEuro
        self.history = {}  # (key, axis) -> list of recent raw values, for the median prefilter

    def _filter(self, key, axis):
        fkey = (key, axis)
        if fkey not in self.filters:
            self.filters[fkey] = _OneEuro(self.min_cutoff, self.beta, self.d_cutoff)
        return self.filters[fkey]

    def _median(self, key, axis, value):
        h = self.history.setdefault((key, axis), [])
        h.append(value)
        if len(h) > MEDIAN_WINDOW:
            h.pop(0)
        return sorted(h)[len(h) // 2]

    def update(self, key, cx, cy, t=None):
        """Smooth a 2D position for `key`. Returns (x, y)."""
        t = time.time() if t is None else t
        cx, cy = self._median(key, "x", cx), self._median(key, "y", cy)
        return self._filter(key, "x").filter(cx, t), self._filter(key, "y").filter(cy, t)

    def scalar(self, key, value, t=None):
        """Smooth an arbitrary 1D value for `key` (e.g. crop height, for zoom rate limiting)."""
        t = time.time() if t is None else t
        value = self._median(key, "s", value)
        return self._filter(key, "s").filter(value, t)

    def drop_except(self, keys):
        self.filters = {k: v for k, v in self.filters.items() if k[0] in keys}
        self.history = {k: v for k, v in self.history.items() if k[0] in keys}
