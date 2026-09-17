import os

MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
API_KEY = os.environ.get("TYPESAFE_API_KEY", "")

SCREEN_W = int(os.environ.get("SCREEN_W", "1280"))
SCREEN_H = int(os.environ.get("SCREEN_H", "800"))

MAX_STEPS = int(os.environ.get("TIVM_MAX_STEPS", "20"))
MAX_ELEMENTS = 150
OCR_SCALE = int(os.environ.get("TIVM_OCR_SCALE", "2"))
GRID_COLS = 4
GRID_ROWS = 3

DONE_THRESHOLD = 0.60
MIN_CONFIDENCE = float(os.environ.get("TIVM_MIN_CONFIDENCE", "0.35"))
UNCERTAIN_LIMIT = 3
STUCK_REPEAT_LIMIT = 3
ABORT_REPEAT_LIMIT = 4
STEP_SLEEP = float(os.environ.get("TIVM_STEP_SLEEP", "1.0"))
SETTLE_TIMEOUT = float(os.environ.get("TIVM_SETTLE_TIMEOUT", "1.5"))
SETTLE_POLL = float(os.environ.get("TIVM_SETTLE_POLL", "0.08"))
A11Y_SKIP_OCR_MIN = int(os.environ.get("TIVM_A11Y_SKIP_OCR_MIN", "10"))
CHANGE_RATIO = float(os.environ.get("TIVM_CHANGE_RATIO", "0.004"))

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


def build_questions(task, elements):
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
                "type": "Type text into the currently focused field. The text comes from the TASK itself.",
                "key": "Press a keyboard key (for example ctrl+l to focus the address bar).",
                "scroll_down": "Scroll down inside the window at the target.",
                "scroll_up": "Scroll up inside the window at the target.",
                "wait": "Do nothing for one step because the screen is still loading.",
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
