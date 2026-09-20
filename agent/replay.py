import hashlib
import json
import math
import os
import re
import time

from . import actions, config


def norm(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def trace_path(task, url, traces_dir=None):
    key = hashlib.sha1(f"{url}|{task}".encode()).hexdigest()[:16]
    return os.path.join(traces_dir or config.TRACES_DIR, f"{key}.json")


def typed_texts(steps):
    return [norm(s.get("typed")) for s in steps or [] if len(norm(s.get("typed"))) >= 3]


def evidence_ok(steps, texts):
    typed = typed_texts(steps)
    if not typed:
        return True
    have = {norm(t) for t in texts or []}
    return all(t in have for t in typed)


CHROME_TEXTS = {
    "account", "applications", "back", "bookmarks", "customize", "edit", "extensions",
    "file", "firefox", "forward", "help", "history", "home", "listalltabs", "menu",
    "mozilla", "newtab", "openanewtab", "openmenu", "reload", "searchwithgoogleorenteraddress",
    "showallbookmarks", "tools", "view",
}


def diff_texts(baseline, final, limit=6):
    seen = {norm(t) for t in baseline or []}
    added = []
    for text in final or []:
        key = norm(text)
        if len(key) < 3 or key in seen or key in added or key in CHROME_TEXTS:
            continue
        added.append(key)
        if len(added) >= limit:
            break
    return added


def save(task, url, steps, baseline_texts, final_texts, traces_dir=None, log=print, window_origin=None):
    if not steps:
        log("replay: no steps to record")
        return None
    if not evidence_ok(steps, final_texts):
        missing = [t for t in typed_texts(steps) if t not in {norm(x) for x in final_texts or []}]
        log(f"replay: typed text is not on the final screen ({missing[:2]}); needs a visible result to assert on")
        return None
    added = diff_texts(baseline_texts, final_texts)
    if not added:
        sample = [str(t)[:24] for t in (final_texts or [])[:8]]
        log(f"replay: nothing new on screen after the flow ({len(final_texts or [])} texts, e.g. {sample})")
        return None
    path = trace_path(task, url, traces_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "task": task,
        "app": url,
        "created": time.time(),
        "steps": steps,
        "added_texts": added,
        "window_origin": list(window_origin) if window_origin else None,
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    log(f"replay: recorded {len(steps)} steps, {len(added)} assertions")
    return path


def load(task, url, traces_dir=None):
    path = trace_path(task, url, traces_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("task") != task or data.get("app") != url or not data.get("steps"):
        return None
    return data


def parse_target(target_text):
    match = re.match(r'^(.*?)\s+"(.*)"$', target_text or "", re.S)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def match_element(elements, step, slack=24):
    role, text = parse_target(step.get("target_text") or "")
    wanted = norm(text)
    if wanted:
        exact = [e for e in elements if norm(e.get("text")) == wanted]
        if exact:
            return exact[0]
        partial = [e for e in elements if wanted in norm(e.get("text")) and len(wanted) >= 4]
        if partial:
            return partial[0]
    point = step.get("point")
    if point:
        return {"role": "point", "text": "", "x": int(point[0]), "y": int(point[1]), "w": 0, "h": 0}
    return None


def execute(step, elements, log=print, act=None, delta=(0, 0)):
    action = step.get("action")
    act = act or actions
    if action in ("click", "double_click", "scroll_up", "scroll_down"):
        element = match_element(elements, step)
        if element is None:
            return False, f"target not on screen: {step.get('target_text') or step.get('point')}"
        x = element["x"] + element.get("w", 0) // 2
        y = element["y"] + element.get("h", 0) // 2
        if element.get("role") == "point":
            x += delta[0]
            y += delta[1]
        if action == "click":
            act.click(x, y)
        elif action == "double_click":
            act.double_click(x, y)
        else:
            act.scroll(x, y, action)
    elif action == "type":
        text = step.get("typed")
        if not text:
            return False, "recorded step has no text to type"
        element = match_element(elements, step)
        if element is not None:
            act.click(element["x"] + element.get("w", 0) // 2, element["y"] + element.get("h", 0) // 2)
            time.sleep(0.3)
        elif step.get("point"):
            act.click(int(step["point"][0]) + delta[0], int(step["point"][1]) + delta[1])
            time.sleep(0.3)
        act.type_text(text)
        if step.get("key"):
            act.press_key(step["key"])
    elif action == "key":
        if not step.get("key"):
            return False, "recorded step has no key"
        act.press_key(step["key"])
    elif action == "wait":
        act.wait()
    else:
        return False, f"unsupported action {action}"
    return True, "ok"


def _texts(items):
    return [norm(i.get("text") if isinstance(i, dict) else i) for i in items or []]


def verify(trace, elements, log=print):
    added = trace.get("added_texts") or []
    if not added:
        return False, "trace has no assertions"
    final = {t for t in _texts(elements) if t}
    typed = [t for t in typed_texts(trace.get("steps")) if t not in final]
    if typed:
        return False, f"typed text missing at the end: {', '.join(typed[:2])}"
    matched = [a for a in added if a in final]
    needed = max(1, math.ceil(len(added) * 0.6))
    if len(matched) < needed:
        missing = [a for a in added if a not in final][:3]
        return False, f"expected text missing at the end: {', '.join(missing)}"
    return True, "ok"


def run(trace, perceive, settle, log=print, act=None, texts=None, origin=None, focus=None):
    steps = trace.get("steps") or []
    if focus:
        focus()
    recorded = trace.get("window_origin")
    delta = (0, 0)
    if origin and recorded:
        delta = (origin[0] - recorded[0], origin[1] - recorded[1])
        if delta != (0, 0):
            log(f"replay: window moved by {delta}, shifting recorded points")
    for index, step in enumerate(steps, start=1):
        elements = perceive()
        ok, why = execute(step, elements, log=log, act=act, delta=delta)
        if not ok:
            return "diverged", f"step {index} ({step.get('action')}): {why}"
        settle(step.get("action"))
    time.sleep(1.5)
    sample = texts() if texts else [e.get("text", "") for e in perceive()]
    ok, why = verify(trace, sample, log=log)
    if not ok:
        return "diverged", why
    return "ok", f"{len(steps)} steps"
