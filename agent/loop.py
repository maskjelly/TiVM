import re
import threading
import time

from . import a11y, actions, config, ts, vision


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _same_target(ocr_el, a11y_el):
    a, b = _norm(ocr_el["text"]), _norm(a11y_el["text"])
    if not a or not b or not (a == b or a in b or b in a):
        return False
    ix = max(0, min(ocr_el["x"] + ocr_el["w"], a11y_el["x"] + a11y_el["w"]) - max(ocr_el["x"], a11y_el["x"]))
    iy = max(0, min(ocr_el["y"] + ocr_el["h"], a11y_el["y"] + a11y_el["h"]) - max(ocr_el["y"], a11y_el["y"]))
    smaller = min(ocr_el["w"] * ocr_el["h"], a11y_el["w"] * a11y_el["h"]) or 1
    return (ix * iy) / smaller > 0.4


def type_candidates(task):
    candidates = []
    for pattern in (r'"([^"]{1,100})"', r"'([^']{1,100})'"):
        candidates.extend(re.findall(pattern, task))
    urls = re.findall(r"https?://[^\s\"']+", task)
    install = re.search(r"\binstall\s+(?:the\s+)?(?:package\s+)?([A-Za-z0-9+_.-]+)", task, re.I)
    if install:
        candidates.append(f"sudo apt-get install -y {install.group(1)}")
    if re.search(r"\b(?:clone|pull|checkout|fetch)\b", task, re.I):
        candidates.extend(f"git clone {url}" for url in urls)
    candidates.extend(urls)
    match = re.search(r"(?:type|write|search(?: the web)?(?: for)?|enter|look up)\s+(.+?)(?:[.,;]|$)", task, re.I)
    if match:
        candidates.append(match.group(1).strip())
    unique = []
    for c in candidates:
        c = c.strip()
        if c and c.lower() not in (u.lower() for u in unique):
            unique.append(c)
    return unique


def build_state(task, elements, history, note, step, max_steps):
    return {
        "task": task,
        "step": f"{step} of {max_steps}",
        "screen": (
            f"{config.SCREEN_W}x{config.SCREEN_H} Linux desktop. "
            "You cannot see the screen; this is the list of widgets and text detected on it, in reading order."
        ),
        "open_windows": [f'{w["title"]} ({w["x"]},{w["y"]} {w["w"]}x{w["h"]})' for w in vision.windows()],
        "focused_window": vision.active_window(),
        "recent_actions": history[-6:],
        "note": note,
    }


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self._blank()

    def _blank(self):
        self.tasks = []
        self.task = ""
        self.task_index = 0
        self.running = False
        self.passed = None
        self.step = 0
        self.max_steps = config.MAX_STEPS
        self.log_lines = []
        self.history = []
        self.last_element = None
        self.last_label = ""
        self.error = None
        self.tokens_in = 0
        self.tokens_out = 0
        self.results = []
        self.started_at = None
        self.finished_at = None
        self._nodes = {}
        self.ocr_skipped = False
        self._fallback_click = None

    def log(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{stamp}] {message}")
        self.log_lines = self.log_lines[-300:]

    def start(self, tasks, max_steps=None):
        with self.lock:
            if self.running:
                return False
            self._blank()
            self.tasks = [t.strip() for t in tasks if t.strip()]
            if not self.tasks:
                return False
            self.max_steps = max_steps or config.MAX_STEPS
            self.running = True
            self.stop_flag = False
            self.started_at = time.time()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True

    def stop(self):
        self.stop_flag = True
        self.log("stop requested")

    def _execute(self, answers, elements, task):
        by_id = {e["id"]: e for e in elements}
        target = answers["target"]["choice"]
        action = answers["action"]["choice"]
        element = by_id.get(target)
        self.last_element = element
        self.last_label = f"task {self.task_index}: {action} -> {target}"
        self._fallback_click = None

        if action in ("click", "double_click"):
            if element:
                node = self._nodes.get(target)
                if (
                    node is not None
                    and element.get("role") in a11y.INVOKE_ROLES
                    and a11y.invoke(node)
                ):
                    self.log(f"invoked {target} via accessibility tree")
                    self._fallback_click = (
                        element["x"] + element["w"] // 2,
                        element["y"] + element["h"] // 2,
                        action,
                    )
                    return f'invoked {element.get("role", "widget")} "{element["text"]}"'
                point = (element["x"] + element["w"] // 2, element["y"] + element["h"] // 2)
            elif target == "no_text_target":
                cell = (answers.get("grid") or {}).get("choice") or "A1"
                point = grid_point(cell)
                self.log(f"no text target, using grid cell {cell}")
            else:
                self.log(f"click requested but target {target!r} is not usable, waiting instead")
                actions.wait()
                return "wait (no usable click target)"
            if action == "click":
                actions.click(*point)
            else:
                actions.double_click(*point)
            return f"{action} at {point[0]},{point[1]}"

        if action == "type":
            candidates = type_candidates(task)
            remaining = [c for c in candidates if c not in self.typed]
            if not candidates:
                self.log('nothing to type: put the text in quotes in the task, e.g. type "hello"')
                self.no_text_note = True
                return "type skipped (no text found in task)"
            if not remaining:
                self.log("all candidate texts already typed; nothing new to type")
                self.no_text_note = True
                return "type skipped (already typed)"
            actions.type_text(remaining[0])
            self.typed.append(remaining[0])
            if config.TYPE_PRESSES_RETURN:
                time.sleep(0.2)
                actions.press_key("Return")
                return f'typed "{remaining[0]}" + Return'
            return f'typed "{remaining[0]}"'

        if action == "key":
            key = (answers.get("key") or {}).get("choice") or "Return"
            actions.press_key(key)
            return f"pressed {key}"

        if action in ("scroll_up", "scroll_down"):
            if element:
                point = (element["x"] + element["w"] // 2, element["y"] + element["h"] // 2)
            elif target == "no_text_target":
                point = grid_point((answers.get("grid") or {}).get("choice") or "A1")
            else:
                point = (config.SCREEN_W // 2, config.SCREEN_H // 2)
            actions.scroll(point[0], point[1], action)
            return f"{action} at {point[0]},{point[1]}"

        actions.wait()
        return "waited"

    def _perceive(self):
        a11y_elements = a11y.elements()
        merged = list(a11y_elements)
        if len(a11y_elements) >= config.A11Y_SKIP_OCR_MIN:
            self.ocr_skipped = True
        else:
            self.ocr_skipped = False
            for ocr_el in vision.ocr_elements():
                if not any(_same_target(ocr_el, a) for a in a11y_elements):
                    merged.append(ocr_el)
        merged.sort(key=lambda e: (e["y"] // 12, e["x"]))
        merged = merged[: config.MAX_ELEMENTS]
        self._nodes = {}
        for i, element in enumerate(merged, start=1):
            element["id"] = f"e{i}"
            if element.get("source") == "a11y":
                extras = []
                if element["role"] == "menu":
                    extras.append("opens a submenu")
                if element.get("focused"):
                    extras.append("has keyboard focus")
                value = element.get("value") or ""
                if value:
                    extras.append(f'contains "{value[:80]}"')
                suffix = f", {', '.join(extras)}" if extras else ""
                element["desc"] = (
                    f'{element["role"]} "{element["text"]}" at x={element["x"]} y={element["y"]} '
                    f'(size {element["w"]}x{element["h"]}{suffix})'
                )
            node = element.pop("node", None)
            if node is not None:
                self._nodes[element["id"]] = node
        return merged

    def _settle(self, previous_thumb):
        last = previous_thumb
        stable = 0
        started = time.time()
        while time.time() - started < config.SETTLE_TIMEOUT:
            time.sleep(config.SETTLE_POLL)
            vision.capture()
            current = vision.thumb(vision.RAW_PATH)
            if vision.diff_ratio(current, last) <= config.CHANGE_RATIO:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
                last = current
        return last

    def _run_task(self, task):
        history = []
        last_thumb = None
        last_signature = None
        repeat = 0
        low_conf = 0
        self.typed = []
        self.no_text_note = False
        note = "This is the first step."

        for step in range(1, self.max_steps + 1):
            if self.stop_flag:
                return {"task": task, "passed": False, "steps": step - 1, "reason": "stopped by user"}
            self.step += 1

            started = time.perf_counter()
            vision.capture()
            current_thumb = vision.thumb(vision.RAW_PATH)
            if last_thumb is None:
                changed = True
            else:
                changed = vision.diff_ratio(current_thumb, last_thumb) > config.CHANGE_RATIO
            last_thumb = current_thumb

            elements = self._perceive()
            perceive_ms = (time.perf_counter() - started) * 1000
            a11y_count = sum(1 for e in elements if e.get("source") == "a11y")
            self.log(
                f"step {self.step}: {len(elements)} elements "
                f"({a11y_count} a11y, {len(elements) - a11y_count} ocr"
                f"{', ocr skipped' if self.ocr_skipped else ''}) in {perceive_ms:.0f}ms"
            )

            state = build_state(task, elements, history, note, step, self.max_steps)
            started = time.perf_counter()
            result = ts.ask(state, config.build_questions(task, elements))
            decide_ms = (time.perf_counter() - started) * 1000
            answers = result["answers"]
            usage = result.get("usage", {})
            step_in = usage.get("input_tokens", 0)
            step_out = usage.get("output_tokens", 0)

            done = answers["done"]["noul"]
            blocked = answers["blocked"]["noul"]
            action = answers["action"]["choice"]
            target = answers["target"]["choice"]
            confidence = answers["action"].get("confidence", 0)

            if action == "key":
                extra = ts.ask(state, {"key": config.build_key_question()})
                answers["key"] = extra["answers"]["key"]
                usage = extra.get("usage", {})
                step_in += usage.get("input_tokens", 0)
                step_out += usage.get("output_tokens", 0)
                self.log(f"follow-up key -> {answers['key']['choice']}")
            elif action in ("click", "double_click") and target == "no_text_target":
                extra = ts.ask(state, {"grid": config.build_grid_question()})
                answers["grid"] = extra["answers"]["grid"]
                usage = extra.get("usage", {})
                step_in += usage.get("input_tokens", 0)
                step_out += usage.get("output_tokens", 0)
                self.log(f"follow-up grid -> {answers['grid']['choice']}")

            self.tokens_in += step_in
            self.tokens_out += step_out
            self.log(f"done={done:.2f} action={action}({confidence:.2f}) target={target}")

            if done >= config.DONE_THRESHOLD:
                self.log("task complete according to Jev")
                return {"task": task, "passed": True, "steps": step, "reason": f"complete (done={done:.2f})"}

            if blocked >= config.BLOCKED_THRESHOLD:
                self.log(f"blocked (blocked={blocked:.2f}), stopping")
                return {
                    "task": task,
                    "passed": False,
                    "steps": step,
                    "reason": f"blocked: an error or auth prompt prevents progress (blocked={blocked:.2f})",
                }

            low_conf = low_conf + 1 if confidence < config.MIN_CONFIDENCE else 0
            if low_conf >= config.UNCERTAIN_LIMIT:
                self.log(f"uncertain: {low_conf} low-confidence decisions in a row")
                return {
                    "task": task,
                    "passed": False,
                    "steps": step,
                    "reason": f"uncertain: {low_conf} decisions under confidence {config.MIN_CONFIDENCE}",
                }

            signature = (action, target)
            repeat = repeat + 1 if signature == last_signature else 0
            last_signature = signature
            if repeat >= config.ABORT_REPEAT_LIMIT:
                return {
                    "task": task,
                    "passed": False,
                    "steps": step,
                    "reason": f"stuck: repeated '{action} {target}' {repeat + 1} times",
                }

            started = time.perf_counter()
            outcome = self._execute(answers, elements, task)
            act_ms = (time.perf_counter() - started) * 1000
            self.log(f"-> {outcome}")
            history.append(f"step {step}: {outcome} ({'screen changed' if changed else 'screen unchanged'})")
            self.history = history

            started = time.perf_counter()
            last_thumb = self._settle(last_thumb)
            settle_ms = (time.perf_counter() - started) * 1000
            screen_changed_after_action = vision.diff_ratio(last_thumb, current_thumb) > config.CHANGE_RATIO

            if self._fallback_click and not screen_changed_after_action:
                x, y, fallback_action = self._fallback_click
                self.log("accessibility invoke had no visible effect, falling back to a mouse click")
                if fallback_action == "double_click":
                    actions.double_click(x, y)
                else:
                    actions.click(x, y)
                started = time.perf_counter()
                last_thumb = self._settle(last_thumb)
                settle_ms += (time.perf_counter() - started) * 1000

            self.log(
                f"timing: perceive {perceive_ms:.0f} + decide {decide_ms:.0f} "
                f"+ act {act_ms:.0f} + settle {settle_ms:.0f} ms | tokens "
                f"{step_in} in / {step_out} out"
            )
            self.no_text_note = False

            if self.no_text_note:
                note = (
                    "The previous action could not run: there was no text available to type. "
                    "Pick a different action, or a target whose text already appears on screen."
                )
            elif low_conf > 0:
                note = "The last decision had low confidence; pick an obvious, unambiguous target."
            elif action == "wait" and repeat >= 2:
                note = (
                    "Waiting has not changed the screen for several steps. If text was just typed, "
                    "the app is waiting for Return. Otherwise pick a clickable element or a different action."
                )
            elif repeat >= config.STUCK_REPEAT_LIMIT:
                actions.press_key("Escape")
                note = (
                    f"The last {repeat} actions were identical ({action} {target}) and did not work. "
                    "An Escape key was pressed to clear any popup or dialog. "
                    "Try a different element, a different action, or a different approach."
                )
            elif not changed:
                note = "The last action produced NO visible change on screen."
            else:
                note = "The screen changed after the last action."

        return {"task": task, "passed": False, "steps": self.max_steps, "reason": "step limit reached"}

    def _run(self):
        try:
            for index, task in enumerate(list(self.tasks), start=1):
                if self.stop_flag:
                    break
                self.task_index = index
                self.task = task
                self.log(f"--- task {index}/{len(self.tasks)}: {task}")
                tokens_before = (self.tokens_in, self.tokens_out)

                try:
                    result = self._run_task(task)
                except Exception as e:
                    result = {"task": task, "passed": False, "steps": 0, "reason": f"error: {e}"}
                    self.error = str(e)
                    self.log(f"error: {e}")

                result["tokens"] = {
                    "input": self.tokens_in - tokens_before[0],
                    "output": self.tokens_out - tokens_before[1],
                }
                result["frame"] = vision.frame_data_url(element=self.last_element, label=self.last_label)
                self.results.append(result)
                self.log(f"task {index} {'PASS' if result['passed'] else 'FAIL'}: {result['reason']}")
                time.sleep(0.5)
        except Exception as e:
            self.error = str(e)
            self.log(f"fatal: {e}")
        finally:
            self.running = False
            self.finished_at = time.time()
            self.passed = (
                len(self.results) == len(self.tasks) and all(r["passed"] for r in self.results)
            )

    def snapshot(self):
        with self.lock:
            return {
                "tasks": self.tasks,
                "task": self.task,
                "task_index": self.task_index,
                "running": self.running,
                "passed": self.passed,
                "step": self.step,
                "max_steps": self.max_steps,
                "error": self.error,
                "log": list(self.log_lines),
                "last_label": self.last_label,
                "results": [{k: v for k, v in r.items() if k != "frame"} for r in self.results],
                "tokens": {"input": self.tokens_in, "output": self.tokens_out},
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }

    def snapshot_with_frame(self):
        frame = vision.panel_frame(element=self.last_element, label=self.last_label)
        data = self.snapshot()
        data["frame"] = frame
        return data

    def result_payload(self):
        with self.lock:
            return {
                "suite": self.tasks,
                "passed": self.passed,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_s": round((self.finished_at or time.time()) - self.started_at, 1)
                if self.started_at
                else None,
                "tokens": {"input": self.tokens_in, "output": self.tokens_out},
                "tasks": list(self.results),
                "error": self.error,
            }


def grid_point(cell):
    column = config.LETTERS.index(cell[0])
    row = int(cell[1]) - 1
    x = int((column + 0.5) * config.SCREEN_W / config.GRID_COLS)
    y = int((row + 0.5) * config.SCREEN_H / config.GRID_ROWS)
    return x, y
