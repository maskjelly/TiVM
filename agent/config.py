import os

VERSION = "0.5.0"
TOKEN = os.environ.get("TIVM_TOKEN", "")

MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
API_KEY = os.environ.get("TYPESAFE_API_KEY", "")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1/chat/completions")
OPENAI_PLANNER_MODEL = os.environ.get("TIVM_OPENAI_PLANNER_MODEL", "gpt-5.6-sol")
OPENAI_VISION_MODEL = os.environ.get("TIVM_OPENAI_VISION_MODEL", "gpt-5.6-sol")
OPENAI_IMAGE_DETAIL = os.environ.get("TIVM_OPENAI_IMAGE_DETAIL", "high")
OPENAI_REASONING_EFFORT = os.environ.get("TIVM_OPENAI_REASONING_EFFORT", "none")
OPENAI_MAX_ELEMENTS = int(os.environ.get("TIVM_OPENAI_MAX_ELEMENTS", "80"))
STALL_LIMIT = int(os.environ.get("TIVM_STALL_LIMIT", "4"))
PLANNER = os.environ.get("TIVM_PLANNER", "openai")
PERCEPTION = os.environ.get("TIVM_PERCEPTION", "hybrid")

SCREEN_W = int(os.environ.get("SCREEN_W", "1280"))
SCREEN_H = int(os.environ.get("SCREEN_H", "800"))

MAX_STEPS = int(os.environ.get("TIVM_MAX_STEPS", "0"))  # 0 = no step limit
MAX_ELEMENTS = 150
OCR_SCALE = int(os.environ.get("TIVM_OCR_SCALE", "2"))
GRID_COLS = 4
GRID_ROWS = 3

DONE_THRESHOLD = 0.60
BLOCKED_THRESHOLD = float(os.environ.get("TIVM_BLOCKED_THRESHOLD", "0.70"))
MIN_CONFIDENCE = float(os.environ.get("TIVM_MIN_CONFIDENCE", "0.35"))
UNCERTAIN_LIMIT = 3
STUCK_REPEAT_LIMIT = 3
ABORT_REPEAT_LIMIT = 4
STEP_SLEEP = float(os.environ.get("TIVM_STEP_SLEEP", "1.0"))
SETTLE_TIMEOUT = float(os.environ.get("TIVM_SETTLE_TIMEOUT", "1.5"))
SETTLE_POLL = float(os.environ.get("TIVM_SETTLE_POLL", "0.08"))
SETTLE_MIN = float(os.environ.get("TIVM_SETTLE_MIN", "0.35"))
COMMAND_SETTLE_TIMEOUT = float(os.environ.get("TIVM_COMMAND_SETTLE_TIMEOUT", "10"))
MAX_CONSECUTIVE_WAITS = int(os.environ.get("TIVM_MAX_WAITS", "20"))
WAIT_SECONDS = float(os.environ.get("TIVM_WAIT_SECONDS", "5"))
A11Y_SKIP_OCR_MIN = int(os.environ.get("TIVM_A11Y_SKIP_OCR_MIN", "10"))
CHANGE_RATIO = float(os.environ.get("TIVM_CHANGE_RATIO", "0.004"))
RUNS_DIR = os.environ.get("TIVM_RUNS_DIR", "/app/runs")
PROJECTS_DIR = os.environ.get("TIVM_PROJECTS_DIR", "/root/projects")
KEEP_RUNS = int(os.environ.get("TIVM_KEEP_RUNS", "20"))
VIDEO = os.environ.get("TIVM_VIDEO", "1") not in ("0", "false", "False")
VIDEO_FPS = int(os.environ.get("TIVM_VIDEO_FPS", "10"))
VIDEO_CRF = int(os.environ.get("TIVM_VIDEO_CRF", "30"))
VIDEO_PRESET = os.environ.get("TIVM_VIDEO_PRESET", "ultrafast")
VIDEO_FAILURE_WINDOW = float(os.environ.get("TIVM_VIDEO_FAILURE_WINDOW", "25"))
TYPE_PRESSES_RETURN = os.environ.get("TIVM_TYPE_RETURN", "1") not in ("0", "false", "False")

H_NAMES = ["left", "center-left", "center-right", "right"]
V_NAMES = ["top", "middle", "bottom"]
LETTERS = "ABCDEFGH"

KEYS = {
    "Return": None,
    "Tab": None,
    "Escape": None,
    "space": None,
    "BackSpace": None,
    "ctrl+l": "focus the address bar (web browser)",
    "ctrl+t": None,
    "ctrl+w": None,
    "ctrl+a": None,
    "ctrl+s": None,
    "ctrl+f": None,
    "alt+F4": "close the current window",
    "super": "open the applications menu",
    "Up": None,
    "Down": None,
    "Left": None,
    "Right": None,
    "Page_Down": None,
    "Page_Up": None,
}


def grid_criteria():
    cells = {}
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            cells[f"{LETTERS[c]}{r + 1}"] = f"{H_NAMES[c]} part, {V_NAMES[r]} third of the screen"
    return cells


def build_questions(task, elements, can_type=True):
    target_criteria = {e["id"]: e["desc"] for e in elements}
    target_criteria["no_text_target"] = (
        "The click target has no listed text (an icon, image, blank area, or window border). "
        "Ignore the grid question unless the action is a click on such a target."
    )

    return {
        "done": {
            "type": "noul",
            "instructions": "The TASK described in the state is now fully complete and visible on screen.",
            "criteria": {
                "true": "The goal is visibly achieved, nothing more is needed",
                "false": "The goal is not achieved yet, or it is unclear",
            },
        },
        "blocked": {
            "type": "noul",
            "instructions": (
                "The screen shows an error, a password or login prompt, or a refusal that prevents "
                "completing the TASK and that no further click or key would fix."
            ),
            "criteria": {
                "true": "Progress is impossible: an unrecoverable error or an auth prompt is on screen",
                "false": "No such blocker is visible",
            },
        },
        "target": {
            "type": "choice",
            "instructions": (
                "Which on-screen element should the next click land on? "
                "Only consulted when the action is click or double_click. "
                "Pick the element whose text matches what the task needs next."
            ),
            "criteria": target_criteria,
        },
        "action": {
            "type": "choice",
            "instructions": (
                "What is the single next action to make progress on the TASK? "
                "If a click is needed, pick click or double_click and choose its element in the target question."
            ),
            "criteria": {
                "click": "Single left click on the target element (menus, buttons, items in lists).",
                "double_click": "Double left click on the target element (desktop icons, files, folder icons).",
                **(
                    {
                        "type": "Type text into the currently focused field. The text comes from the TASK itself."
                    }
                    if can_type
                    else {}
                ),
                "key": "Press a keyboard key (for example ctrl+l to focus the address bar).",
                "scroll_down": "Scroll down inside the window at the target.",
                "scroll_up": "Scroll up inside the window at the target.",
            },
        },
    }


def build_key_question():
    return {
        "type": "choice",
        "instructions": "Which key should be pressed?",
        "criteria": KEYS,
    }


def build_grid_question():
    return {
        "type": "choice",
        "instructions": (
            "The action is a click on something with no text (an icon, image, blank area, or window border). "
            "Which screen region holds it?"
        ),
        "criteria": grid_criteria(),
    }
