"""Publish one HD channel via RTSP (ffmpeg/MediaMTX) or NDI (cyndilib)."""
import subprocess
from fractions import Fraction

import cv2

from geometry import HD_H, HD_W


class RTSPPublisher:
    """Feeds raw BGR frames to an ffmpeg subprocess that publishes h264 over RTSP.

    ponytail: single hardcoded 30fps output rate and videotoolbox encoder (macOS-only).
    Upgrade to a configurable rate/encoder if this needs to run off Apple Silicon.
    """

    def __init__(self, url, fps=30):
        self.proc = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{HD_W}x{HD_H}", "-r", str(fps), "-i", "-",
                "-c:v", "h264_videotoolbox", "-realtime", "true", "-bf", "0",
                "-g", str(fps), "-b:v", "8M",
                "-f", "rtsp", "-rtsp_transport", "udp", url,
            ],
            stdin=subprocess.PIPE,
        )

    def write(self, frame):
        """frame must be HD_W x HD_H x 3 BGR (as produced by crop_hd/track_crop)."""
        self.proc.stdin.write(frame.tobytes())

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


class NDIPublisher:
    """Publish one HD channel as an NDI video source via cyndilib.

    ponytail: video-only, no audio embedding (matches RTSPPublisher's scope).
    Requires the NDI SDK runtime (libndi.dylib) - already present on this
    machine via NDI Tools; not a pip-installable dependency.
    """

    def __init__(self, name, fps=30):
        from cyndilib.sender import Sender
        from cyndilib.video_frame import VideoSendFrame
        from cyndilib.wrapper.ndi_structs import FourCC

        self.video_frame = VideoSendFrame()
        self.video_frame.set_resolution(HD_W, HD_H)
        self.video_frame.set_frame_rate(Fraction(fps).limit_denominator())
        self.video_frame.set_fourcc(FourCC.BGRX)

        self.sender = Sender(ndi_name=name)
        self.sender.set_video_frame(self.video_frame)
        self.sender.open()

    def write(self, frame):
        """frame must be HD_W x HD_H x 3 BGR (as produced by crop_hd/track_crop)."""
        bgrx = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        self.sender.write_video(bgrx.reshape(-1))

    def close(self):
        self.sender.close()
