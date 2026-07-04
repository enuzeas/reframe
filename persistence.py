"""Channel-layout persistence so a restarted service comes back to its last configuration.

Only the channel *layout* is saved (geometry / tracking on-off / zoom preset / smoothing),
deliberately NOT:
- target_id: the tracker assigns fresh track IDs every session, so a saved one is always
  stale - server.py drops it on load and the channel comes back waiting for a re-bind.
- input source: camera indices shift between sessions on this platform (see sources.py),
  so the capture device belongs in the launch args, not in restored state.

Atomic write (temp + os.replace): a KeepAlive launchd service can be killed mid-write, and
a half-written state.json that then fails to parse would silently drop the whole layout.
"""
import json
import os

# macOS convention for per-user app state (INFRA-PLAN §8).
STATE_PATH = os.path.expanduser("~/Library/Application Support/reframe/state.json")


def save_channels(channels, path=STATE_PATH):
    """Persist the given Channel objects' layout (via their to_dict())."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump([c.to_dict() for c in channels], f)
    os.replace(tmp, path)


def load_channels(path=STATE_PATH):
    """Return the saved channel dicts (list), or None if nothing usable is stored."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) and data else None


if __name__ == "__main__":
    import tempfile

    class _Fake:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return self._d

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sub", "state.json")  # dir doesn't exist yet - save must create it
        assert load_channels(p) is None
        chans = [_Fake({"id": 1, "x": 0, "y": 0, "w": 640, "h": 360,
                        "tracking": True, "zoom": "face", "smoothing": 40})]
        save_channels(chans, p)
        assert load_channels(p) == [chans[0].to_dict()]
        with open(p, "w") as f:
            f.write("{not json")  # corrupt -> None, never raises
        assert load_channels(p) is None
    print("persistence self-test OK")
