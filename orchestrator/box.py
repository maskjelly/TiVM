import time

import requests

from . import config


class BoxError(RuntimeError):
    pass


class Box:
    """Client for one dev box's HTTP API."""

    def __init__(self, url=None, session=None, log=print):
        self.url = (url or config.BOX_URL).rstrip("/")
        self.session = session or requests.Session()
        self.log = log

    def start(self, app, tasks=None, max_steps=None):
        payload = {"app": app, "max_steps": max_steps or config.JOB_MAX_STEPS}
        if tasks:
            payload["tasks"] = tasks
        response = self.session.post(f"{self.url}/api/run", json=payload, timeout=60)
        if response.status_code == 409:
            raise BoxError("box is already running a suite")
        if response.status_code >= 400:
            raise BoxError(f"run start failed: HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if not data.get("ok"):
            raise BoxError(f"run start rejected: {data}")
        return data["run_id"]

    def state(self):
        response = self.session.get(f"{self.url}/api/state", timeout=30)
        response.raise_for_status()
        return response.json()

    def result(self):
        response = self.session.get(f"{self.url}/api/result", timeout=30)
        response.raise_for_status()
        return response.json()

    def stop(self):
        try:
            self.session.post(f"{self.url}/api/stop", timeout=30)
        except requests.RequestException as e:
            self.log(f"box stop failed: {e}")

    def wait(self, run_id, timeout=None, poll=10):
        deadline = time.monotonic() + (timeout or config.JOB_TIMEOUT)
        while time.monotonic() < deadline:
            state = self.state()
            if state.get("run_id") == run_id and not state.get("running"):
                return True
            if state.get("run_id") != run_id and not state.get("running"):
                return True
            time.sleep(poll)
        return False
