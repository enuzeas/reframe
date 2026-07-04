"""Thread-safe state shared between the pipeline loop (background thread) and
the FastAPI request/WS handlers (asyncio event loop) - single process for now
(see docs/INFRA-PLAN.md §2 note: process separation deferred to M6)."""
import queue
import threading


class PipelineState:
    def __init__(self):
        self._lock = threading.Lock()
        self.latest_frame = None   # preview tile, BGR ndarray, for MJPEG
        self.overlay = {"boxes": [], "channels": [], "fps": 0.0, "mode": 1}
        self.source_id = None      # OpenCV index currently in use, for sources.probe_devices skip

    def update(self, *, frame=None, overlay=None, source_id=None):
        with self._lock:
            if frame is not None:
                self.latest_frame = frame
            if overlay is not None:
                self.overlay = overlay
            if source_id is not None:
                self.source_id = source_id

    def snapshot(self):
        with self._lock:
            return self.latest_frame, self.overlay, self.source_id


class CommandQueue:
    """POST handlers enqueue commands; the pipeline loop drains non-blockingly."""

    def __init__(self):
        self._q = queue.Queue()

    def put(self, command):
        self._q.put(command)

    def drain(self):
        commands = []
        while True:
            try:
                commands.append(self._q.get_nowait())
            except queue.Empty:
                break
        return commands
