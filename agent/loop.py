import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import a11y, actions, apps, config, openai_client, report, ts, video, vision


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
        candidates.append(f"apt-get install -y {install.group(1)}")
    cloned = set()
    if re.search(r"\b(?:clone|pull|checkout|fetch)\b", task, re.I):
        for url in urls:
            repo = url.rstrip("/").split("/")[-1].removesuffix(".git")
            candidates.append(f"git clone {url} || git -C {repo} pull")
            cloned.add(url)
    candidates.extend(url for url in urls if url not in cloned)
    match = re.search(r"(?:type|write|search(?: the web)?(?: for)?|enter|look up)\s+(.+?)(?:[.,;]|$)", task, re.I)
    if match:
        candidates.append(match.group(1).strip())
    unique = []
    for c in candidates:
        c = c.strip()
        if c and c.lower() not in (u.lower() for u in unique):
            unique.append(c)
    return unique


def build_state(task, elements, history, note, step, max_steps, app=None):
    state = {
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
    if app:
        state["app_under_test"] = app
    terminal_output = [e["value"] for e in elements if e.get("role") == "terminal" and e.get("value")]
    if terminal_output:
        state["terminal_output"] = [text[-600:] for text in terminal_output]
    return state


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
        self.openai_tokens = 0
        self.timeline = []
        self.current = {}
        self.task_step = 0
        self.run_id = ""
        self.plan_state = []
        self.memory = ""
        self.last_check = ""
        self.last_terminal = []
        self.last_elements = []
        self.video_rec = None
        self.t0 = None
        self.app_request = None
        self.app = {}
        self.app_context = ""
        self.prepare_error = ""

    def log(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{stamp}] {message}")
        self.log_lines = self.log_lines[-300:]

    def emit(self, kind, **fields):
        entry = {"kind": kind, "at": time.strftime("%H:%M:%S")}
        if self.t0 is not None:
            entry["t"] = round(time.monotonic() - self.t0, 2)
        entry["task_index"] = self.task_index
        entry.update(fields)
        self.timeline.append(entry)
        self.timeline = self.timeline[-400:]
        if kind == "step":
            self.current = entry

    def _run_dir(self):
        return os.path.join(config.RUNS_DIR, self.run_id)

    def _prune_runs(self):
        root = config.RUNS_DIR
        if not os.path.isdir(root):
            return
        dirs = sorted(
            (d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))),
            reverse=True,
        )
        for old in dirs[config.KEEP_RUNS:]:
            shutil.rmtree(os.path.join(root, old), ignore_errors=True)

    def _save_task_artifacts(self, index, result):
        run_dir = self._run_dir()
        if not self.run_id or not os.path.isdir(run_dir):
            return
        data = {k: v for k, v in result.items() if k != "frame"}
        with open(os.path.join(run_dir, f"task-{index}.json"), "w") as fh:
            json.dump(data, fh, indent=1)
        frame = result.get("frame") or ""
        if frame.startswith("data:image"):
            raw = base64.b64decode(frame.split(",", 1)[1])
            with open(os.path.join(run_dir, f"task-{index}-final.jpg"), "wb") as fh:
                fh.write(raw)
        with open(os.path.join(run_dir, "timeline.json"), "w") as fh:
            json.dump(self.timeline, fh, indent=1)

    def _write_run_json(self):
        run_dir = self._run_dir()
        if not self.run_id or not os.path.isdir(run_dir):
            return
        payload = self.result_payload()
        payload["tasks"] = [{k: v for k, v in t.items() if k != "frame"} for t in payload["tasks"]]
        with open(os.path.join(run_dir, "run.json"), "w") as fh:
            json.dump(payload, fh, indent=1)

    def _start_video(self):
        run_dir = self._run_dir()
        if not self.run_id or not os.path.isdir(run_dir):
            return
        if config.VIDEO and not video.available():
            self.log("video disabled: ffmpeg not found in this image")
            return
        self.video_rec = video.start(os.path.join(run_dir, "screen.mp4"))
        if self.video_rec is not None:
            self.log("video recording started (screen.mp4)")

    def _stop_video(self):
        if self.video_rec is None:
            return
        rec, self.video_rec = self.video_rec, None
        rec.stop()
        size = os.path.getsize(rec.path) if os.path.exists(rec.path) else 0
        self.log(f"video recording stopped ({size // 1024} KB, {rec.elapsed():.0f}s)")

    def _cut_task_videos(self):
        run_dir = self._run_dir()
        source = os.path.join(run_dir, "screen.mp4")
        if not os.path.exists(source):
            return
        for index, result in enumerate(self.results, start=1):
            info = result.get("video")
            if not info:
                continue
            start, end = info["start_s"], info["end_s"]
            if video.cut(source, os.path.join(run_dir, info["file"]), start, end):
                self.log(f"video: {info['file']} ({end - start:.0f}s)")
            else:
                result.pop("video", None)
                continue
            if not result["passed"]:
                fail_start = max(start, end - config.VIDEO_FAILURE_WINDOW)
                clip = f"task-{index}-failure.mp4"
                if video.cut(source, os.path.join(run_dir, clip), fail_start, end):
                    info["failure_file"] = clip
                    info["failure_window"] = int(end - fail_start)

    def _write_report(self):
        run_dir = self._run_dir()
        if not self.run_id or not os.path.isdir(run_dir):
            return
        try:
            path = report.write(run_dir)
        except Exception as e:
            self.log(f"report generation failed: {e}")
            return
        if path:
            self.log("report: report.html")

    def _prepare(self):
        request = dict(self.app_request or {})
        self.emit("prepare_start", repo=request.get("repo"), ref=request.get("ref"), pr=request.get("pr"))
        self.log(
            "prepare: " + ", ".join(
                f"{k}={request[k]}" for k in ("repo", "ref", "pr", "local_dir") if request.get(k)
            )
        )
        try:
            info = apps.prepare(request, self._run_dir(), log=self.log)
        except apps.PrepareError as e:
            self.prepare_error = str(e)
            self.log(f"prepare failed: {e}")
            self.emit("prepare_failed", error=str(e))
            return
        self.app = info
        if not self.tasks:
            self.tasks = [
                str(f.get("steps") or f.get("name")).strip()
                for f in info.get("flows", [])
                if (f.get("steps") or f.get("name"))
            ]
            if self.tasks:
                self.log(f"flows from .tivm.yml: {len(self.tasks)} task(s)")
        self.app_context = (
            f"{info.get('repo', 'the app')} is already running and open in Firefox at {info['url']}. "
            "Work only inside that app; never re-clone or restart it."
        )
        self.emit("prepare_ready", url=info["url"], source=info["source"], dir=info["dir"], sha=info["sha"], ready_s=info["ready_s"])
        try:
            subprocess.Popen(
                ["firefox", info["url"]],
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.log(f"firefox opened at {info['url']}")
            time.sleep(4)
        except OSError as e:
            self.log(f"could not open firefox: {e}")

    def start(self, tasks, max_steps=None, app=None):
        with self.lock:
            if self.running:
                return False
            self._blank()
            self.app_request = dict(app) if app else None
            self.tasks = [t.strip() for t in tasks if t.strip()]
            if not self.tasks and not self.app_request:
                return False
            self.max_steps = max_steps or config.MAX_STEPS
            self.running = True
            self.stop_flag = False
            self.started_at = time.time()
            self.t0 = time.monotonic()
            self.run_id = time.strftime("%Y%m%d-%H%M%S")
            try:
                os.makedirs(os.path.join(config.RUNS_DIR, self.run_id), exist_ok=True)
                self._prune_runs()
            except OSError as e:
                self.log(f"cannot write run artifacts: {e}")
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
        self.last_typed = ""

        if action in ("click", "double_click"):
            point = None
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
            elif answers.get("point"):
                point = tuple(answers["point"])
            if point is None:
                if target == "no_text_target" and config.PLANNER != "openai":
                    cell = (answers.get("grid") or {}).get("choice") or "A1"
                    point = grid_point(cell)
                    self.log(f"no usable element target, using grid cell {cell}")
                else:
                    self.log("no click target given; asking the planner to choose one")
                    self.target_note = True
                    self.target_note_count += 1
                    return "no click target chosen"
            if action == "click":
                actions.click(*point)
            else:
                actions.double_click(*point)
            return f"{action} at {point[0]},{point[1]}"

        if action == "type":
            forced = str(answers.get("text") or "").strip()
            candidates = [forced] if forced else type_candidates(task)
            remaining = [c for c in candidates if c not in self.typed]
            if not candidates:
                self.log('nothing to type: put the text in quotes in the task, e.g. type "hello"')
                self.no_text_note = True
                return "type skipped (no text found in task)"
            if not remaining:
                self.log("all candidate texts already typed; nothing new to type")
                return "type skipped (already typed)"
            actions.type_text(remaining[0])
            self.typed.append(remaining[0])
            self.last_typed = remaining[0]
            if config.TYPE_PRESSES_RETURN:
                time.sleep(0.2)
                actions.press_key("Return")
                return f'typed "{remaining[0]}" + Return'
            return f'typed "{remaining[0]}"'

        if action == "key":
            key = (answers.get("key") or {}).get("choice")
            if not key:
                self.log("key action without a key name; asking the planner to choose one")
                self.key_note = True
                return "no key given"
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

        if action == "wait":
            actions.wait(config.WAIT_SECONDS)
            return f"waited {config.WAIT_SECONDS:.0f}s"

        actions.wait()
        return "waited"

    def _vision_elements(self):
        try:
            texts, usage = openai_client.vision_ocr(vision.RAW_PATH, config.SCREEN_W, config.SCREEN_H)
        except Exception as e:
            self.log(f"openai vision failed ({e}), falling back to tesseract")
            return vision.ocr_elements()
        self.openai_tokens += usage.get("total_tokens", 0)
        out = []
        for item in texts[: config.MAX_ELEMENTS]:
            text = (item.get("text") or "").strip()
            try:
                x, y, w, h = (int(item.get(k, 0)) for k in ("x", "y", "w", "h"))
            except (TypeError, ValueError):
                continue
            if text and w >= 2 and h >= 2:
                out.append(
                    {
                        "source": "vision",
                        "text": text[:80],
                        "x": max(0, x),
                        "y": max(0, y),
                        "w": w,
                        "h": h,
                    }
                )
        self.log(f"openai vision: {len(out)} text items")
        return out

    def _perceive(self):
        a11y_elements = a11y.elements()
        extras = []
        self.ocr_skipped = True
        if config.PERCEPTION == "vision":
            extras = self._vision_elements()
            self.ocr_skipped = False
        elif config.PERCEPTION != "a11y" and config.PLANNER != "openai":
            if len(a11y_elements) < config.A11Y_SKIP_OCR_MIN:
                extras = vision.ocr_elements()
                self.ocr_skipped = False
        merged = list(a11y_elements)
        for element in extras:
            if not any(_same_target(element, existing) for existing in merged):
                merged.append(element)
        merged.sort(key=lambda e: (e["y"] // 12, e["x"]))
        if len(merged) > config.MAX_ELEMENTS:
            interactive = [e for e in merged if e.get("role") in a11y.INTERACTIVE_ROLES]
            others = [e for e in merged if e.get("role") not in a11y.INTERACTIVE_ROLES]
            merged = (interactive + others)[: config.MAX_ELEMENTS]
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

    def _failure_context(self, stage, step, reason):
        last = self.current or {}
        screen = " / ".join(self.last_terminal)[-500:] if self.last_terminal else ""
        return {
            "stage": stage,
            "step": step,
            "reason": reason,
            "action": last.get("action"),
            "target": last.get("target"),
            "target_text": last.get("target_text"),
            "typed": last.get("typed"),
            "check": last.get("check"),
            "focused_window": vision.active_window(),
            "screen": screen,
            "elements": self.last_elements[:24],
        }

    def _fail(self, task, stage, step, reason):
        self.log(f"task failed at {stage} (step {step}): {reason}")
        return {
            "task": task,
            "passed": False,
            "steps": step,
            "reason": reason,
            "failure": self._failure_context(stage, step, reason),
        }

    def _settle(self, previous_thumb, timeout=None):
        last = previous_thumb
        stable = 0
        time.sleep(config.SETTLE_MIN)
        started = time.time()
        limit = timeout or config.SETTLE_TIMEOUT
        while time.time() - started < limit:
            time.sleep(config.SETTLE_POLL)
            vision.capture()
            current = vision.thumb(vision.RAW_PATH)
            if vision.diff_ratio(current, last) <= config.CHANGE_RATIO:
                stable += 1
                if stable >= 3:
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
        self.target_note = False
        self.key_note = False
        self.target_note_count = 0
        self.stall = 0
        self.waits = 0
        self.plan_state = []
        self.memory = ""
        self.last_check = ""
        self.last_terminal = []
        self.last_elements = []
        note = "This is the first step."

        for step in range(1, (self.max_steps or 10**9) + 1):
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

            state = build_state(task, elements, history, note, step, self.max_steps, app=self.app_context)
            self.last_terminal = state.get("terminal_output") or []
            self.last_elements = [f'{e["text"]}' for e in elements][:24]
            remaining_text = [c for c in type_candidates(task) if c not in self.typed]
            started = time.perf_counter()
            step_openai = 0
            plan_reason = ""
            if config.PLANNER == "openai":
                plan, usage = openai_client.plan(
                    task,
                    elements,
                    [w["title"] for w in vision.windows()],
                    vision.active_window(),
                    state.get("terminal_output"),
                    history,
                    vision.RAW_PATH,
                    plan_state=self.plan_state,
                    memory=self.memory,
                    last_check=self.last_check,
                    app=self.app_context,
                )
                step_openai = usage.get("total_tokens", 0)
                self.openai_tokens += step_openai
                if isinstance(plan.get("plan"), list) and plan["plan"]:
                    self.plan_state = [str(p)[:120] for p in plan["plan"]][:12]
                if plan.get("memory"):
                    self.memory = str(plan["memory"])[:300]
                self.last_check = str(plan.get("check") or "")[:200]
                if self.plan_state:
                    self.log("plan: " + " | ".join(self.plan_state[:6]))
                if self.memory:
                    self.log(f"memory: {self.memory[:160]}")
                action = str(plan.get("action") or "wait").strip()
                target = str(plan.get("target") or "").strip()
                answers = {
                    "action": {"choice": action, "confidence": 1.0},
                    "target": {"choice": target or "no_text_target"},
                    "text": str(plan.get("text") or "").strip(),
                    "key": {"choice": plan.get("key") or "Return"},
                }
                try:
                    if plan.get("x") is not None and plan.get("y") is not None:
                        answers["point"] = (int(plan["x"]), int(plan["y"]))
                except (TypeError, ValueError):
                    pass
                done = 1.0 if plan.get("done") else 0.0
                blocked = 1.0 if plan.get("blocked") else 0.0
                confidence = 1.0
                step_in = step_out = 0
                plan_reason = str(plan.get("reason") or "").strip()
                self.log(f"plan: {plan_reason[:120]}")
            else:
                result = ts.ask(
                    state, config.build_questions(task, elements, can_type=bool(remaining_text))
                )
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

            decide_ms = (time.perf_counter() - started) * 1000
            self.tokens_in += step_in
            self.tokens_out += step_out
            self.log(f"done={done:.2f} blocked={blocked:.2f} action={action}({confidence:.2f}) target={target}")

            if done >= config.DONE_THRESHOLD:
                self.log("task complete according to Jev")
                self.emit("done", n=step, action="done", reason=f"done={done:.2f}")
                return {"task": task, "passed": True, "steps": step, "reason": f"complete (done={done:.2f})"}

            if blocked >= config.BLOCKED_THRESHOLD:
                self.emit("blocked", n=step, action="blocked", reason=f"blocked={blocked:.2f}")
                return self._fail(task, "decide", step, "blocked: an error, auth prompt or missing target prevents progress")

            low_conf = low_conf + 1 if confidence < config.MIN_CONFIDENCE else 0
            if low_conf >= config.UNCERTAIN_LIMIT:
                return self._fail(
                    task, "decide", step,
                    f"uncertain: {low_conf} decisions under confidence {config.MIN_CONFIDENCE}",
                )

            signature = (action, target)
            if action == "wait":
                self.waits += 1
                if self.waits > config.MAX_CONSECUTIVE_WAITS:
                    return self._fail(
                        task, "wait", step,
                        f"no progress: waited {config.MAX_CONSECUTIVE_WAITS} times and the command never finished",
                    )
            else:
                self.waits = 0
                repeat = repeat + 1 if signature == last_signature else 0
                last_signature = signature
                if repeat >= config.ABORT_REPEAT_LIMIT:
                    return self._fail(
                        task, "act", step,
                        f"stuck: repeated '{action} {target}' {repeat + 1} times with no result",
                    )

            started = time.perf_counter()
            outcome = self._execute(answers, elements, task)
            act_ms = (time.perf_counter() - started) * 1000
            self.log(f"-> {outcome}")
            history.append(f"step {step}: {outcome} ({'screen changed' if changed else 'screen unchanged'})")
            self.history = history

            started = time.perf_counter()
            settle_timeout = config.COMMAND_SETTLE_TIMEOUT if action in ("type", "key", "wait") else None
            last_thumb = self._settle(last_thumb, settle_timeout)
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
                + (f" | openai {step_openai}" if step_openai else "")
            )
            self.no_text_note = False
            self.target_note = False
            self.key_note = False

            if action == "wait":
                pass
            elif screen_changed_after_action:
                self.stall = 0
            else:
                self.stall += 1
                if self.stall >= config.STALL_LIMIT:
                    return self._fail(
                        task, "settle", step,
                        f"no progress: {self.stall} actions produced no screen change",
                    )

            if self.no_text_note:
                note = (
                    "The previous action could not run: there was no text available to type. "
                    "Pick a different action, or a target whose text already appears on screen."
                )
            elif self.target_note:
                if self.target_note_count >= 2:
                    note = (
                        "Clicking without a target is impossible and has failed repeatedly. "
                        "If a window already has keyboard focus, use 'type' directly without clicking. "
                        "Otherwise set 'target' to a listed element id or give x/y pixels."
                    )
                else:
                    note = (
                        "The previous action had no target. Set 'target' to one of the listed element ids, "
                        "or give x/y pixel coordinates from the screenshot."
                    )
            elif self.key_note:
                note = (
                    "The previous action was 'key' without a key name. Name the key to press, "
                    "or use 'wait' if you are waiting for a running command."
                )
            elif low_conf > 0:
                note = "The last decision had low confidence; pick an obvious, unambiguous target."
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

            self.emit(
                "step",
                n=step,
                action=action,
                target=target,
                target_text=(
                    f'{self.last_element.get("role", "widget")} "{self.last_element.get("text", "")}"'
                    if self.last_element and target not in ("", "no_text_target")
                    else ""
                ),
                typed=self.last_typed,
                reason=plan_reason,
                check=self.last_check,
                plan=list(self.plan_state),
                memory=self.memory,
                outcome=outcome,
                changed=screen_changed_after_action,
                timing={
                    "perceive": round(perceive_ms),
                    "decide": round(decide_ms),
                    "act": round(act_ms),
                    "settle": round(settle_ms),
                },
                tokens={"ts_in": step_in, "ts_out": step_out, "openai": step_openai},
                note=note,
            )

        return self._fail(
            task, "limit", step,
            f"step limit reached ({self.max_steps})" if self.max_steps else "stopped by user",
        )

    def _run(self):
        self._start_video()
        try:
            if self.app_request:
                self._prepare()
                if not self.tasks:
                    self.tasks = ["the app under test starts and its flows run"]
            for index, task in enumerate(list(self.tasks), start=1):
                if self.stop_flag:
                    break
                self.task_index = index
                self.task = task
                self.log(f"--- task {index}/{len(self.tasks)}: {task}")
                self.emit("task_start", index=index, total=len(self.tasks), task=task)
                task_started = time.time()
                video_started = self.video_rec.elapsed() if self.video_rec else 0.0
                tokens_before = (self.tokens_in, self.tokens_out)
                openai_before = self.openai_tokens

                if self.prepare_error:
                    result = self._fail(task, "prepare", 0, f"app prepare failed: {self.prepare_error}")
                else:
                    try:
                        result = self._run_task(task)
                    except Exception as e:
                        result = self._fail(task, "error", self.step, f"error: {e}")
                        self.error = str(e)
                        self.log(f"error: {e}")

                if self.video_rec is not None:
                    video_ended = self.video_rec.elapsed()
                    result["video"] = {
                        "file": f"task-{index}.mp4",
                        "start_s": round(video_started, 2),
                        "end_s": round(video_ended, 2),
                        "duration_s": round(video_ended - video_started, 1),
                    }
                result["tokens"] = {
                    "input": self.tokens_in - tokens_before[0],
                    "output": self.tokens_out - tokens_before[1],
                }
                result["openai_tokens"] = self.openai_tokens - openai_before
                result["duration_s"] = round(time.time() - task_started, 1)
                result["frame"] = vision.frame_data_url(element=self.last_element, label=self.last_label)
                self.results.append(result)
                if not result["passed"] and index < len(self.tasks):
                    self.log(f"task {index} failed, continuing with {len(self.tasks) - index} remaining task(s)")
                try:
                    self._save_task_artifacts(index, result)
                except OSError as e:
                    self.log(f"artifact save failed: {e}")
                self.emit(
                    "task_end",
                    index=index,
                    task=task,
                    passed=result["passed"],
                    reason=result["reason"],
                    steps=result["steps"],
                    duration_s=result["duration_s"],
                    tokens=result["tokens"],
                    openai_tokens=result["openai_tokens"],
                )
                self.log(f"task {index} {'PASS' if result['passed'] else 'FAIL'}: {result['reason']}")
                time.sleep(0.5)
        except Exception as e:
            self.error = str(e)
            self.log(f"fatal: {e}")
            self.emit("error", message=str(e))
        finally:
            self.running = False
            self.finished_at = time.time()
            self.passed = (
                len(self.results) == len(self.tasks) and all(r["passed"] for r in self.results)
            )
            try:
                apps.stop_all()
            except Exception:
                pass
            self._stop_video()
            self._cut_task_videos()
            try:
                self._write_run_json()
            except OSError as e:
                self.log(f"run.json save failed: {e}")
            self._write_report()

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
                "timeline": list(self.timeline),
                "current": dict(self.current),
                "plan": list(self.plan_state),
                "memory": self.memory,
                "last_check": self.last_check,
                "planner": config.PLANNER,
                "planner_model": config.OPENAI_PLANNER_MODEL if config.PLANNER == "openai" else config.MODEL,
                "perception": config.PERCEPTION,
                "tokens": {"input": self.tokens_in, "output": self.tokens_out},
                "openai_tokens": self.openai_tokens,
                "run_id": self.run_id,
                "run_dir": self._run_dir() if self.run_id else "",
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
                "openai_tokens": self.openai_tokens,
                "run_id": self.run_id,
                "run_dir": self._run_dir() if self.run_id else "",
                "app": self.app or None,
                "prepare_error": self.prepare_error or None,
                "tasks": list(self.results),
                "error": self.error,
            }


def grid_point(cell):
    column = config.LETTERS.index(cell[0])
    row = int(cell[1]) - 1
    x = int((column + 0.5) * config.SCREEN_W / config.GRID_COLS)
    y = int((row + 0.5) * config.SCREEN_H / config.GRID_ROWS)
    return x, y
