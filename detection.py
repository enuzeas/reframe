"""YOLO + ByteTrack person detection on a downscaled frame."""
import cv2

DETECT_W = 960  # detection runs on this width, crops come from the source frame


def detect_people(model, frame, device=None):
    """Run tracking on a downscaled frame, return [(tid, x1, y1, x2, y2)] in source coords, largest first."""
    fh, fw = frame.shape[:2]
    scale = DETECT_W / fw
    small = cv2.resize(frame, (DETECT_W, int(fh * scale)))
    # tracker="bytetrack.yaml": PLAN.md §3.2 chose ByteTrack (throughput priority, no
    # camera motion compensation step), but model.track() never actually specified it -
    # ultralytics silently used its own current default instead (tracktrack.yaml here,
    # which runs GMC via sparseOptFlow). That GMC step's "not enough matching points"
    # warnings lined up exactly with this session's periodic ~0.2-1s pipeline stalls on
    # this camera's jittery frame timing/sizing. ByteTrack has no GMC at all.
    res = model.track(small, persist=True, classes=[0], verbose=False, device=device,
                       tracker="bytetrack.yaml")[0]
    people = []
    if res.boxes is not None and res.boxes.id is not None:
        for box, tid in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.id.int().cpu().numpy()):
            x1, y1, x2, y2 = box / scale
            people.append((int(tid), x1, y1, x2, y2))
    people.sort(key=lambda p: (p[3] - p[1]) * (p[4] - p[2]), reverse=True)
    return people
