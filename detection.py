"""YOLO + ByteTrack person detection on a downscaled frame."""
import cv2

DETECT_W = 960  # detection runs on this width, crops come from the source frame


def detect_people(model, frame, device=None):
    """Run tracking on a downscaled frame, return [(tid, x1, y1, x2, y2)] in source coords, largest first."""
    fh, fw = frame.shape[:2]
    scale = DETECT_W / fw
    small = cv2.resize(frame, (DETECT_W, int(fh * scale)))
    res = model.track(small, persist=True, classes=[0], verbose=False, device=device)[0]
    people = []
    if res.boxes is not None and res.boxes.id is not None:
        for box, tid in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.id.int().cpu().numpy()):
            x1, y1, x2, y2 = box / scale
            people.append((int(tid), x1, y1, x2, y2))
    people.sort(key=lambda p: (p[3] - p[1]) * (p[4] - p[2]), reverse=True)
    return people
