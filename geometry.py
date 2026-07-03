"""Frame cropping/clamping math and placeholder tiles."""
import cv2
import numpy as np

HD_W, HD_H = 1920, 1080


def clamp_window(cx, cy, cw, ch, fw, fh):
    """Clamp a cw x ch window centered at (cx, cy) inside a fw x fh frame."""
    cw, ch = min(cw, fw), min(ch, fh)
    x = int(round(cx - cw / 2))
    y = int(round(cy - ch / 2))
    x = int(max(0, min(x, fw - cw)))
    y = int(max(0, min(y, fh - ch)))
    return x, y, int(cw), int(ch)


def crop_hd(frame, cx, cy, ch):
    """Crop a 16:9 window of height ch centered at (cx, cy), resized to HD."""
    fh, fw = frame.shape[:2]
    cw = ch * 16 / 9
    x, y, w, h = clamp_window(cx, cy, cw, ch, fw, fh)
    return cv2.resize(frame[y:y + h, x:x + w], (HD_W, HD_H), interpolation=cv2.INTER_LINEAR)


def placeholder(label):
    img = np.full((HD_H, HD_W, 3), 32, np.uint8)
    cv2.putText(img, label, (60, HD_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 2, (200, 200, 200), 3)
    return img
