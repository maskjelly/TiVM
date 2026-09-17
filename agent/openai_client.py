import base64
import io
import json
import time

import requests
from PIL import Image

from . import config


class OpenAIError(RuntimeError):
    pass


def _post(payload, timeout=120):
    if not config.OPENAI_API_KEY.startswith("sk-"):
        raise OpenAIError("OPENAI_API_KEY is not set (put it in .env)")
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(config.OPENAI_URL, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code >= 400:
            raise OpenAIError(f"OpenAI HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    raise OpenAIError(f"OpenAI request failed after retries: {last}")


def image_data_url(path, max_w=1024, quality=80):
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


PLANNER_SYSTEM = """You control a Linux desktop through a small action vocabulary.
You get a screenshot, the task, the detected on-screen elements (id, role, text, position), open
windows, terminal output and recent actions.
Reply with JSON only:
{"done": bool, "blocked": bool, "action": "click"|"double_click"|"type"|"key"|"scroll_up"|"scroll_down",
 "target": "<element id, or empty>", "x": int, "y": int, "text": "<exact text to type>",
 "key": "<key name>", "reason": "<short>"}

Rules:
- One action per reply. Use exactly one of the action values.
- For clicks, "target" must be an element id from the list (never empty), or supply "x"/"y" pixels.
  Prefer a listed element id; use x/y only when no element fits.
- For action "type", put the complete exact text to type in "text" (a full shell command if needed).
  The text is typed into whatever has keyboard focus. Focus is shown as "has keyboard focus" in the element list.
- For action "key", valid keys: Return, Tab, Escape, space, BackSpace, ctrl+l, ctrl+t, ctrl+w, ctrl+a,
  ctrl+s, ctrl+f, alt+F4, super, Up, Down, Left, Right, Page_Down, Page_Up.
- If the screen shows a dialog blocking progress (like a browser notice), dismiss it first.
- Set done=true only when the task's goal is visibly achieved on screen.
- Set blocked=true only when an unrecoverable error or a login/password prompt blocks the task,
  or when the task asks for something that does not exist (a folder or app that is not on screen
  and cannot be reached), and say why in "reason".
- Never repeat an action that produced no change; choose a different target or approach."""


def plan(task, elements, windows, focused_window, terminal_output, history, screenshot_path):
    element_lines = []
    for e in elements[: config.OPENAI_MAX_ELEMENTS]:
        focus = " (focused)" if e.get("focused") else ""
        element_lines.append(
            f'{e["id"]} {e.get("role", e.get("source", "text"))} '
            f'"{e.get("text", "")}" at {e.get("x", 0)},{e.get("y", 0)}{focus}'
        )
    state = {
        "task": task,
        "focused_window": focused_window,
        "open_windows": windows,
        "elements": element_lines,
        "terminal_output": terminal_output or [],
        "recent_actions": history[-6:],
    }
    payload = {
        "model": config.OPENAI_PLANNER_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(state)},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url(screenshot_path),
                            "detail": config.OPENAI_IMAGE_DETAIL,
                        },
                    },
                ],
            },
        ],
    }
    if config.OPENAI_REASONING_EFFORT:
        payload["reasoning_effort"] = config.OPENAI_REASONING_EFFORT
    data = _post(payload)
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise OpenAIError(f"planner returned non-JSON: {content[:200]}")
    return parsed, data.get("usage", {})


VISION_PROMPT = """Transcribe every visible piece of text in this screenshot of a {w}x{h} Linux desktop.
Reply with JSON only:
{{"texts": [{{"text": "...", "x": 0, "y": 0, "w": 0, "h": 0}}]}}
x,y is the top-left pixel of the text and w,h its size in the {w}x{h} coordinate space.
List menu items, buttons, labels, window titles and terminal lines separately, in reading order.
Skip unreadable text. Maximum 150 items."""


def vision_ocr(image_path, screen_w, screen_h):
    payload = {
        "model": config.OPENAI_VISION_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT.format(w=screen_w, h=screen_h)},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url(image_path),
                            "detail": config.OPENAI_IMAGE_DETAIL,
                        },
                    },
                ],
            }
        ],
    }
    if config.OPENAI_REASONING_EFFORT:
        payload["reasoning_effort"] = config.OPENAI_REASONING_EFFORT
    data = _post(payload)
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return [], data.get("usage", {})
    return parsed.get("texts", []), data.get("usage", {})
