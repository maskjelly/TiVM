import base64
import hashlib
import io
import os
import re
import subprocess
import threading
import time

from PIL import Image, ImageDraw

from . import config

RAW_PATH = "/tmp/tivm_raw.png"
PANEL_PATH = "/tmp/tivm_panel.png"
OCR_PATH = "/tmp/tivm_ocr.png"
_capture_lock = threading.Lock()


def _env():
    return {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")}


def capture(path=RAW_PATH):
    with _capture_lock:
        subprocess.run(
            ["scrot", "-o", "-q", "90", path],
            check=True,
            env=_env(),
            capture_output=True,
        )
    return path


def panel_frame(element=None, label=""):
    with _capture_lock:
        subprocess.run(
            ["scrot", "-o", "-q", "90", PANEL_PATH],
            check=True,
            env=_env(),
            capture_output=True,
        )
        return frame_data_url(PANEL_PATH, element, label)


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, env=_env())


def _region(x, y, w, h):
    cx = (x + w / 2) / config.SCREEN_W
    cy = (y + h / 2) / config.SCREEN_H
    hname = config.H_NAMES[min(int(cx * config.GRID_COLS), config.GRID_COLS - 1)]
    vname = config.V_NAMES[min(int(cy * config.GRID_ROWS), config.GRID_ROWS - 1)]
    return f"{hname}-{vname} of screen"


def ocr_elements(path=RAW_PATH):
    scale = config.OCR_SCALE
    if scale != 1:
        with Image.open(path) as im:
            im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
            im.save(OCR_PATH)
        ocr_path = OCR_PATH
    else:
        ocr_path = path

    out = subprocess.run(["tesseract", ocr_path, "stdout", "tsv"], capture_output=True, text=True)
    words = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            continue
        text = parts[11].strip()
        if conf < 35 or not text or not re.search(r"[A-Za-z0-9]", text):
            continue
        words.append(
            {
                "block": parts[2],
                "par": parts[3],
                "line": parts[4],
                "left": int(parts[6]) // scale,
                "top": int(parts[7]) // scale,
                "width": int(parts[8]) // scale,
                "height": int(parts[9]) // scale,
                "text": text,
            }
        )

    groups = {}
    for word in words:
        groups.setdefault((word["block"], word["par"], word["line"]), []).append(word)

    elements = []
    for group in groups.values():
        group.sort(key=lambda w: w["left"])
        text = " ".join(w["text"] for w in group)[:80]
        left = min(w["left"] for w in group)
        top = min(w["top"] for w in group)
        right = max(w["left"] + w["width"] for w in group)
        bottom = max(w["top"] + w["height"] for w in group)
        elements.append(
            {
                "source": "ocr",
                "text": text,
                "x": left,
                "y": top,
                "w": right - left,
                "h": bottom - top,
            }
        )

    elements.sort(key=lambda e: (e["y"] // 12, e["x"]))
    elements = elements[: config.MAX_ELEMENTS]

    for i, e in enumerate(elements, start=1):
        e["id"] = f"e{i}"
        e["desc"] = (
            f'text "{e["text"]}" at x={e["x"]} y={e["y"]} '
            f"(size {e['w']}x{e['h']}, {_region(e['x'], e['y'], e['w'], e['h'])})"
        )
    return elements


def windows():
    out = _run(["wmctrl", "-lG"])
    wins = []
    for line in out.stdout.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        wins.append(
            {
                "title": parts[7],
                "x": int(parts[2]),
                "y": int(parts[3]),
                "w": int(parts[4]),
                "h": int(parts[5]),
            }
        )
    return wins


def active_window():
    return _run(["xdotool", "getactivewindow", "getwindowname"]).stdout.strip()


def screen_hash(path=RAW_PATH):
    with Image.open(path) as im:
        small = im.convert("L").resize((64, 40))
        return hashlib.md5(small.tobytes()).hexdigest()


def thumb(path=RAW_PATH):
    with Image.open(path) as im:
        return im.convert("L").resize((160, 100)).tobytes()


def diff_ratio(a, b, threshold=10):
    if a is None or b is None or len(a) != len(b):
        return 1.0
    changed = 0
    for x, y in zip(a, b):
        if x - y > threshold or y - x > threshold:
            changed += 1
    return changed / len(a)


def frame_data_url(path=RAW_PATH, element=None, label="", max_w=1024, quality=78):
    for attempt in range(2):
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                draw = ImageDraw.Draw(im)
                if element:
                    x, y, w, h = element["x"], element["y"], element["w"], element["h"]
                    draw.rectangle([x - 3, y - 3, x + w + 3, y + h + 3], outline=(0, 220, 130), width=3)
                if label:
                    draw.rectangle([0, 0, min(len(label) * 8 + 16, im.width), 26], fill=(10, 10, 10))
                    draw.text((8, 7), label, fill=(0, 220, 130))
                if im.width > max_w:
                    im = im.resize((max_w, int(im.height * max_w / im.width)))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=quality)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except (FileNotFoundError, OSError):
            if attempt:
                raise
            time.sleep(0.4)
