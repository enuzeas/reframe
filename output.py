"""Publish one HD channel via RTSP (ffmpeg/MediaMTX) or NDI (cyndilib)."""
import subprocess
from fractions import Fraction

import cv2

from geometry import HD_H, HD_W


def rtsp_cmd(url, fps=30, audio_src=None):
    """Build the ffmpeg argv for RTSPPublisher. Split out so self-tests can check
    the audio branch without actually spawning ffmpeg."""
    cmd = ["ffmpeg", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{HD_W}x{HD_H}", "-r", str(fps),
           # -use_wallclock_as_timestamps on the video (stdin) input: `fps` here is the
           # camera's nominal rate (e.g. 60), not what the pipeline actually delivers once
           # detection is running (often much lower, ~15-20 with 4 channels + YOLO). Without
           # this flag ffmpeg assumes a constant `fps` and timestamps frames by frame count
           # alone, so its encoded PTS runs far ahead of real time - live players (confirmed
           # with IINA) show the first frame then stall, waiting for "future" PTS to become
           # due as real frames trickle in slower than declared. Wallclock timestamps each
           # frame by when it actually arrived instead.
           "-thread_queue_size", "1024", "-use_wallclock_as_timestamps", "1", "-i", "-"]
    if audio_src is not None:
        # Also wallclock here, despite avfoundation audio having its own accurate hardware
        # clock: that clock is a monotonic/uptime-based domain, not wall-clock epoch time,
        # and mixing it with the video input's epoch-based wallclock PTS put the two streams
        # in incomparable time domains. A downstream reader parsing the muxed RTSP session
        # computed a nonsensical negative relative start ("start: -3.6..."), and video
        # decoding stalled after the first frame - confirmed with a continuous capture test
        # (frame count kept climbing per ffmpeg's own stats, but 0 bytes ever got muxed).
        # Keeping both wallclock (same domain) reintroduces the occasional
        # "Queue input is backward in time" dropped audio frame, which is the smaller
        # problem by far.
        #
        # -thread_queue_size on both inputs: confirmed live that removing audio entirely
        # made video play back cleanly again, so combining the two inputs (not either one
        # alone) is what breaks playback - ffmpeg's default input queue (8 packets) is a
        # well-documented cause of exactly this "two independently-paced live inputs stall
        # each other" class of bug when one source (our irregular Python write() cadence)
        # doesn't keep pace with the other (avfoundation's steady real-time audio).
        cmd += ["-thread_queue_size", "1024", "-f", "avfoundation",
                "-use_wallclock_as_timestamps", "1", "-i", f":{audio_src}"]
    # GOP length capped at 15 (not `fps`, the camera's nominal rate) - keyframes only
    # every `fps` frames meant ~1-2 real seconds between them at this pipeline's actual
    # throughput (~15-40fps with detection running), adding to worst-case join/recovery
    # latency on top of TCP's own delay. More frequent keyframes cost a little bitrate,
    # not a lot at this resolution.
    gop = min(int(fps), 15)
    cmd += ["-c:v", "h264_videotoolbox", "-realtime", "true", "-bf", "0", "-g", str(gop), "-b:v", "8M"]
    if audio_src is not None:
        cmd += ["-c:a", "libopus", "-b:a", "128k", "-ar", "48000"]
    # ffmpeg's rtsp muxer defaults -pkt_size to 1472, over mediamtx's apparent 1440-byte
    # threshold - mediamtx was logging "RTP packets are too big (1460 > 1440), remuxing
    # them into smaller ones" for every channel, and OBS's own RTP depacketizer (unlike
    # IINA/mpv, which tolerated it) choked on the reassembled packets: its log showed
    # rapid connect/read/disconnect loops and never rendered a frame (plain black).
    # Keeping packets under the threshold at the source avoids the remux entirely.
    #
    # -rtsp_transport tcp: retested udp twice, including once after closing other apps
    # and confirming system load average had actually dropped (~9 -> ~7.7 on 8 cores) -
    # reproduced the identical stuck-frame symptom both times regardless of load (17
    # consecutive byte-identical decoded frames starting at frame 3 the second time).
    # Rules out "just this machine being busy" as the explanation; something in the UDP
    # publish/mediamtx-relay path is genuinely broken here, not merely loss-prone under
    # contention. TCP costs latency but is the one that actually works.
    cmd += ["-pkt_size", "1200", "-f", "rtsp", "-rtsp_transport", "tcp", url]
    return cmd


class RTSPPublisher:
    """Feeds raw BGR frames to an ffmpeg subprocess that publishes h264 (+ optional
    Opus audio, muxed from a live avfoundation device) over RTSP.

    ponytail: single hardcoded 30fps output rate and videotoolbox encoder (macOS-only).
    Upgrade to a configurable rate/encoder if this needs to run off Apple Silicon.
    """

    def __init__(self, url, fps=30, audio_src=None):
        self.proc = subprocess.Popen(rtsp_cmd(url, fps, audio_src), stdin=subprocess.PIPE)

    def write(self, frame):
        """frame must be HD_W x HD_H x 3 BGR (as produced by crop_hd/track_crop)."""
        self.proc.stdin.write(frame.tobytes())

    def close(self):
        """Closing stdin (the video pipe) only EOFs ffmpeg when video is its one
        live input. With audio_src set, ffmpeg also has a standing avfoundation
        capture that never EOFs on its own, so wait() would hang forever - kill
        it instead once given a grace period to flush."""
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


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

        # clock_video=False: cyndilib defaults to pacing write_video() to the frame
        # rate set above - fine if that rate is trustworthy, but `fps` here is the
        # camera's own self-reported CAP_PROP_FPS (server.py), which some webcams
        # under-report (confirmed live: a built-in camera claimed 15fps at 640x480,
        # and with clocking on that throttled every write_video() call to ~66ms,
        # dragging the *entire* pipeline loop - capture/detect/render included, since
        # write() is awaited synchronously - down to that same ~15fps). RTSPPublisher
        # already sidesteps this failure mode with wallclock timestamps instead of a
        # trusted declared rate; disabling the clock here is the NDI equivalent - send
        # frames as fast as they're produced and let receivers pace off arrival time.
        self.sender = Sender(ndi_name=name, clock_video=False)
        self.sender.set_video_frame(self.video_frame)
        self.sender.open()

    def write(self, frame):
        """frame must be HD_W x HD_H x 3 BGR (as produced by crop_hd/track_crop)."""
        bgrx = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        self.sender.write_video(bgrx.reshape(-1))

    def close(self):
        self.sender.close()
