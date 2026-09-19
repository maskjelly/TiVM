import os


def _int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


PORT = _int("TIVM_ORCH_PORT", 6090)
BOX_URL = os.environ.get("TIVM_BOX_URL", "http://tivm-desktop:6081").rstrip("/")
RUNS_DIR = os.environ.get("TIVM_RUNS_DIR", "/runs")
DB_PATH = os.environ.get("TIVM_DB", "/data/jobs.sqlite")

WEBHOOK_SECRET = os.environ.get("TIVM_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.environ.get("TIVM_GITHUB_TOKEN", "")
GITHUB_API = os.environ.get("TIVM_GITHUB_API", "https://api.github.com").rstrip("/")
PUBLIC_URL = os.environ.get("TIVM_PUBLIC_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
API_TOKEN = os.environ.get("TIVM_API_TOKEN", "")

TRIGGER_MENTION = os.environ.get("TIVM_TRIGGER_MENTION", "@tivm").lower()
TRIGGER_LABEL = os.environ.get("TIVM_TRIGGER_LABEL", "tivm").lower()
RUN_ON_OPEN = os.environ.get("TIVM_RUN_ON_OPEN", "0") not in ("0", "false", "False")

CONCURRENCY = _int("TIVM_CONCURRENCY", 1)
JOB_MAX_STEPS = _int("TIVM_JOB_MAX_STEPS", 24)
JOB_TIMEOUT = _int("TIVM_JOB_TIMEOUT", 1800)
KEEP_JOBS = _int("TIVM_KEEP_JOBS", 100)
