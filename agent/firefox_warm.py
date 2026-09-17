import subprocess
import time

from . import a11y


def main():
    subprocess.Popen(
        ["firefox", "--new-window", "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(2)
        elements = a11y.elements()
        if not elements:
            continue
        cont = next(
            (e for e in elements if e["text"] == "Continue" and e["invokable"]),
            None,
        )
        if cont is not None:
            a11y.invoke(cont["node"])
            time.sleep(2)
            break
        if any(e["role"] in ("entry", "text") for e in elements):
            break
    subprocess.run(["pkill", "-x", "firefox"], capture_output=True)


if __name__ == "__main__":
    main()
