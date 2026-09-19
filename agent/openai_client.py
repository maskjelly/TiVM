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


def image_size(path, max_w=1024):
    with Image.open(path) as im:
        width, height = im.width, im.height
    if width > max_w:
        height = int(height * max_w / width)
        width = max_w
    return width, height


def image_scale(path, max_w=1024):
    with Image.open(path) as im:
        width = im.width or 1
    return min(1.0, max_w / width)


def to_screen(x, y, scale):
    scale = scale or 1.0
    sx = round(float(x) / scale)
    sy = round(float(y) / scale)
    return (
        max(0, min(config.SCREEN_W - 1, sx)),
        max(0, min(config.SCREEN_H - 1, sy)),
    )


PLANNER_SYSTEM = """You are the orchestrator of a Linux desktop agent. You get a screenshot, the
task, the detected on-screen elements (id, role, text, position), open windows, terminal output,
recent actions, and your own plan, memory and last self-check.
Reply with JSON only:
{"done": bool, "blocked": bool, "action": "click"|"double_click"|"type"|"key"|"wait"|"scroll_up"|"scroll_down",
 "target": "<element id, or empty>", "x": int, "y": int, "text": "<exact text to type>",
 "key": "<key name>", "reason": "<short>",
 "plan": ["<remaining substep>", "..."], "memory": "<short facts to remember>",
 "check": "<what must be true after this action>"}

Rules:
- One action per reply. Use exactly one of the action values.
- Keep "plan" updated every reply: the remaining substeps of the task, in order, with completed
  ones removed. Never lose sight of the overall task; the plan is your memory of it.
- Use "memory" for short facts you must not forget: repo paths, dev server URL and port,
  which command is still running, versions, errors seen. It is carried to every future step.
- "check" states what should be true right after your action. On the next step you are shown
  your previous check, so verify it against the screen before moving on; if it did not hold,
  fix it first instead of continuing the plan.
- For clicks, "target" must be an element id from the list (never empty), or supply "x"/"y" pixels.
  Prefer a listed element id; use x/y only when no element fits. x/y are in the screenshot's pixel
  space (the state states its size), not the real screen's.
- For "type", put the complete exact text in "text" (a full shell command if needed). It goes to
  whatever has keyboard focus; focus is marked "has keyboard focus" in the element list. If the
  window you need is already focused, just type - do not click first.
- For "key", "key" must name the key: Return, Tab, Escape, space, BackSpace, ctrl+l, ctrl+t,
  ctrl+w, ctrl+a, ctrl+s, ctrl+f, alt+F4, super, Up, Down, Left, Right, Page_Down, Page_Up.
- Use "wait" when a command you started is still running (installs, builds, servers). Waiting
  repeatedly is fine while output advances; keep checking terminal output for completion or errors.
- Set done=true only when the task's goal is visibly achieved on screen.
- Set blocked=true only when an unrecoverable error or a login/password prompt blocks the task,
  or when the task asks for something that does not exist (a folder or app that is not on screen
  and cannot be reached), and say why in "reason".
- Never repeat an action that produced no change; choose a different target or approach."""


def plan(task, elements, windows, focused_window, terminal_output, history, screenshot_path,
         plan_state=None, memory="", last_check="", app=""):
    element_lines = []
    for e in elements[: config.OPENAI_MAX_ELEMENTS]:
        focus = " (focused)" if e.get("focused") else ""
        element_lines.append(
            f'{e["id"]} {e.get("role", e.get("source", "text"))} '
            f'"{e.get("text", "")}" at {e.get("x", 0)},{e.get("y", 0)}{focus}'
        )
    state = {
        "task": task,
        "your_plan": plan_state or [],
        "memory": memory,
        "your_last_check": last_check,
        "focused_window": focused_window,
        "open_windows": windows,
        "elements": element_lines,
        "terminal_output": terminal_output or [],
        "recent_actions": history[-6:],
    }
    if app:
        state["app_under_test"] = app
    image_w, image_h = image_size(screenshot_path)
    state["screenshot"] = f"{image_w}x{image_h} pixels; x/y coordinates you return are in this space"
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
    parsed["_scale"] = image_scale(screenshot_path)
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
