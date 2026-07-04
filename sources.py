"""Camera device discovery: probing by OpenCV index, not by ffmpeg device name.

ponytail: ffmpeg's avfoundation device list order/names don't reliably match
OpenCV's AVFoundation index order (confirmed on this machine twice - the same
physical camera showed up at different indices between the two). So instead of
trusting names, callers show a thumbnail per index and let a human pick by
what they see.
"""
import cv2

RESOLUTION_CANDIDATES = [(3840, 2160), (1920, 1080), (1600, 1200), (1280, 720), (640, 480)]


def probe_devices(max_index=6, skip_index=None):
    """Return [{"id": i}] for indices that actually open, skipping `skip_index`
    (the pipeline's own in-use camera - reopening it risks device contention)."""
    devices = []
    for i in range(max_index):
        if i == skip_index:
            devices.append({"id": i})
            continue
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                devices.append({"id": i})
        cap.release()
    return devices


def thumbnail_jpeg(index, latest_frame=None, max_width=320):
    """JPEG bytes for a single frame from `index`, or from `latest_frame` if given
    (use this for the index the pipeline already has open)."""
    if latest_frame is not None:
        frame = latest_frame
    else:
        cap = cv2.VideoCapture(index)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return None
    h, w = frame.shape[:2]
    if w > max_width:
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)))
    ok, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes() if ok else None


def probe_resolutions(index, candidates=RESOLUTION_CANDIDATES):
    """Try each candidate resolution and keep only what the device actually delivers
    (cv2.VideoCapture.set() silently ignores unsupported sizes instead of erroring)."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return []
    confirmed = []
    for w, h in candidates:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        ok, frame = cap.read()
        if ok and (frame.shape[1], frame.shape[0]) == (w, h) and (w, h) not in confirmed:
            confirmed.append((w, h))
    cap.release()
    return confirmed
