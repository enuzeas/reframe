"""One Euro Filter smoothing for tracked positions and sizes."""
import math
import time

ONEEURO_MIN_CUTOFF = 3.0  # ponytail: bumped from 1.0 (2026-07-04 live test) - at 1.0 the
                          # filter treated ordinary head movement as noise to suppress,
                          # only reacting to fast/large moves (subject leaving frame).
                          # lower = smoother but laggier; raise further if still sluggish.
ONEEURO_BETA = 0.15       # ponytail: dropped from 0.7 (2026-07-04) - beta scales cutoff by
                          # *velocity*, so at 0.7 almost any real detection noise (a person
                          # naturally swaying, YOLO bbox jitter frame-to-frame) reads as
                          # "fast movement" and lets jitter straight through, independent of
                          # min_cutoff - confirmed with a synthetic noisy-signal test: cutting
                          # min_cutoff in half barely changed frame-to-frame jitter, but
                          # dropping beta 0.7->0.1 nearly halved it, for only a small lag
                          # increase. This is also why the UI's smoothing slider (which only
                          # adjusts min_cutoff) had barely any visible effect on shakiness.
                          # higher = less lag on fast moves, more jitter on ordinary ones.
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


class Smoother:
    """Per-track One Euro Filter smoothing, independent per axis (x/y/size)."""

    def __init__(self, min_cutoff=ONEEURO_MIN_CUTOFF, beta=ONEEURO_BETA, d_cutoff=ONEEURO_D_CUTOFF):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.filters = {}  # (key, axis) -> _OneEuro

    def _filter(self, key, axis):
        fkey = (key, axis)
        if fkey not in self.filters:
            self.filters[fkey] = _OneEuro(self.min_cutoff, self.beta, self.d_cutoff)
        return self.filters[fkey]

    def update(self, key, cx, cy, t=None):
        """Smooth a 2D position for `key`. Returns (x, y)."""
        t = time.time() if t is None else t
        return self._filter(key, "x").filter(cx, t), self._filter(key, "y").filter(cy, t)

    def scalar(self, key, value, t=None):
        """Smooth an arbitrary 1D value for `key` (e.g. crop height, for zoom rate limiting)."""
        t = time.time() if t is None else t
        return self._filter(key, "s").filter(value, t)

    def drop_except(self, keys):
        self.filters = {k: v for k, v in self.filters.items() if k[0] in keys}
