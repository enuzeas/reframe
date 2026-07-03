"""Publish one HD channel to a local MediaMTX RTSP endpoint via ffmpeg."""
import subprocess

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
