import time

import requests

from . import config


class TypeSafeError(RuntimeError):
    pass


def ask(state, questions, timeout=90):
    if not config.API_KEY or config.API_KEY.startswith("apikey_your"):
        raise TypeSafeError("TYPESAFE_API_KEY is not set (put it in .env)")

    payload = {"state": state, "model": config.MODEL, "questions": questions}
    headers = {"Authorization": f"Bearer {config.API_KEY}"}
    last = None

    for attempt in range(4):
        try:
            r = requests.post(config.API_URL, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
            continue

        if r.status_code in (429, 502, 503, 504, 529):
            last = f"HTTP {r.status_code}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 401:
            raise TypeSafeError("TypeSafe rejected the API key (401)")
        if r.status_code >= 400:
            raise TypeSafeError(f"TypeSafe HTTP {r.status_code}: {r.text[:400]}")

        return r.json()

    raise TypeSafeError(f"TypeSafe request failed after retries: {last}")
