import os
import subprocess
import time

from . import config


def _env():
    return {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")}


def _xdotool(*args):
    subprocess.run(["xdotool", *args], check=False, env=_env(), capture_output=True)


def click(x, y):
    _xdotool("mousemove", str(x), str(y))
    _xdotool("click", "--clearmodifiers", "1")


def double_click(x, y):
    _xdotool("mousemove", str(x), str(y))
    _xdotool("click", "--clearmodifiers", "--repeat", "2", "--delay", "80", "1")


def type_text(text):
    _xdotool("type", "--delay", "25", "--clearmodifiers", text)


def press_key(key):
    _xdotool("key", "--clearmodifiers", key)


def scroll(x, y, direction):
    button = "5" if direction == "scroll_down" else "4"
    _xdotool("mousemove", str(x), str(y))
    _xdotool("click", "--clearmodifiers", "--repeat", "3", "--delay", "60", button)


def wait(seconds=None):
    time.sleep(seconds if seconds is not None else config.STEP_SLEEP)
