import os
import shutil
import signal
import subprocess
import threading
import time

from . import config

_lock = threading.Lock()
_active = None


def available():
    return shutil.which("ffmpeg") is not None


def enabled():
    return config.VIDEO and available()


class Recorder:
    def __init__(self, path):
        self.path = path
        self.proc = None
        self.started_monotonic = None

    def start(self):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "x11grab",
            "-framerate", str(config.VIDEO_FPS),
            "-video_size", f"{config.SCREEN_W}x{config.SCREEN_H}",
            "-i", os.environ.get("DISPLAY", ":99"),
            "-c:v", "libx264",
            "-preset", config.VIDEO_PRESET,
            "-crf", str(config.VIDEO_CRF),
            "-pix_fmt", "yuv420p",
            "-g", str(max(2, config.VIDEO_FPS * 2)),
            "-movflags", "+faststart",
            self.path,
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.started_monotonic = time.monotonic()
        return self

    def elapsed(self):
        if self.started_monotonic is None:
            return 0.0
        return time.monotonic() - self.started_monotonic

    def stop(self, timeout=15):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass


def start(path):
    global _active
    with _lock:
        if _active is not None or not enabled():
            return None
        rec = Recorder(path)
        try:
            rec.start()
        except (OSError, ValueError):
            return None
        _active = rec
        return rec


def stop():
    global _active
    with _lock:
        rec, _active = _active, None
    if rec is not None:
        rec.stop()
    return rec


def cut(src, dst, start_s, end_s):
    if not available() or not os.path.exists(src):
        return False
    start_s = max(0.0, float(start_s or 0.0))
    duration = float(end_s or 0.0) - start_s
    if duration < 0.5:
        duration = 0.5
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start_s:.2f}",
        "-i", src,
        "-t", f"{duration:.2f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(config.VIDEO_CRF),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        dst,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0
