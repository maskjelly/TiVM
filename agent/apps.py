import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request

import yaml

from . import config

CONTRACT_NAMES = (".tivm.yml", ".tivm.yaml")
MANAGERS = (
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)
FRAMEWORK_PORTS = {
    "vite": 5173,
    "@sveltejs/kit": 5173,
    "astro": 4321,
    "next": 3000,
    "nuxt": 3000,
    "remix": 3000,
    "gatsby": 8000,
}
SCRIPT_ORDER = ("dev", "start", "preview", "serve")
SETUP_TIMEOUT = 900
READY_TIMEOUT = 180

_processes = []


class PrepareError(Exception):
    pass


def _env(extra=None):
    env = {
        **os.environ,
        "DISPLAY": os.environ.get("DISPLAY", ":99"),
        "GIT_TERMINAL_PROMPT": "0",
    }
    env.update({str(k): str(v) for k, v in (extra or {}).items()})
    return env


def normalize_repo(repo):
    repo = (repo or "").strip()
    if not repo:
        raise PrepareError("no repository given")
    if re.match(r"^[a-z][a-z0-9+.-]*://", repo) or repo.startswith("/") or os.path.isdir(repo):
        return repo
    if re.match(r"^[\w.-]+/[\w.-]+$", repo):
        return f"https://github.com/{repo}"
    return repo


def clone(repo, ref=None, pr=None, dest_root=None, log=print):
    url = normalize_repo(repo)
    name = re.sub(r"[^\w.-]+", "-", url.rstrip("/").split("/")[-1].removesuffix(".git")) or "app"
    dest = os.path.join(dest_root or config.PROJECTS_DIR, name)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    log(f"clone {url}" + (f" ref={ref}" if ref else "") + (f" pr={pr}" if pr else ""))
    result = subprocess.run(
        ["git", "clone", "--quiet", "--filter=blob:none", url, dest],
        capture_output=True, text=True, env=_env(), timeout=300,
    )
    if result.returncode != 0:
        raise PrepareError(f"git clone failed: {result.stderr.strip()[:300]}")
    target = f"pull/{pr}/head" if pr else ref
    if target:
        fetched = subprocess.run(
            ["git", "-C", dest, "fetch", "--quiet", "--depth", "1", "origin", target],
            capture_output=True, text=True, env=_env(), timeout=300,
        )
        if fetched.returncode != 0:
            raise PrepareError(f"git fetch {target} failed: {fetched.stderr.strip()[:300]}")
        checkout = subprocess.run(
            ["git", "-C", dest, "checkout", "--quiet", "FETCH_HEAD"],
            capture_output=True, text=True, env=_env(), timeout=60,
        )
        if checkout.returncode != 0:
            raise PrepareError(f"git checkout failed: {checkout.stderr.strip()[:300]}")
    return dest


def _sha(repo_dir):
    result = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, env=_env(), timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def find_contract(repo_dir):
    for base in (repo_dir, os.path.join(repo_dir, "app")):
        for name in CONTRACT_NAMES:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                return path
    return ""


def load_contract(path):
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:
        raise PrepareError(f"{os.path.basename(path)} is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise PrepareError(f"{os.path.basename(path)} must contain a mapping")
    version = data.get("tivm")
    if version not in (None, 1):
        raise PrepareError(f"unsupported tivm contract version: {version}")
    return data


def infer(app_dir):
    path = os.path.join(app_dir, "package.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            pkg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    scripts = pkg.get("scripts") or {}
    script = next((s for s in SCRIPT_ORDER if s in scripts), None)
    if not script:
        return None
    manager = ""
    declared = str(pkg.get("packageManager") or "").split("@")[0]
    if declared in ("bun", "pnpm", "yarn", "npm"):
        manager = declared
    else:
        for lockfile, candidate in MANAGERS:
            if os.path.exists(os.path.join(app_dir, lockfile)):
                manager = candidate
                break
    manager = manager or "npm"
    port = 0
    for value in scripts.values():
        match = re.search(r"(?:--port[= ]|-p )(\d{2,5})", str(value))
        if match:
            port = int(match.group(1))
            break
    if not port:
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        for name, candidate in FRAMEWORK_PORTS.items():
            if name in deps:
                port = candidate
                break
    port = port or 3000
    return {
        "setup": [f"{manager} install"],
        "run": [f"{manager} run {script}"],
        "url": f"http://localhost:{port}",
        "ready": "",
    }


def resolve(repo_dir, override=None):
    override = override or {}
    repo_dir = os.path.abspath(repo_dir)
    contract_path = find_contract(repo_dir)
    contract = load_contract(contract_path) if contract_path else {}
    app = dict(contract.get("app") or {})
    inferred = None
    if not app.get("run"):
        rel = override.get("dir") or app.get("dir") or "."
        inferred = infer(os.path.abspath(os.path.join(repo_dir, rel)))
        if inferred:
            app = {**inferred, **{k: v for k, v in app.items() if v}}
    source = "contract" if contract.get("app") else ("inferred" if inferred else "")
    for key in ("dir", "setup", "run", "url", "ready", "ready_timeout", "env"):
        if override.get(key) not in (None, "", []):
            app[key] = override[key]
    if inferred and not contract.get("app") and any(k in override for k in ("setup", "run", "url")):
        source = "override"
    rel_dir = str(app.get("dir") or ".")
    app_dir = os.path.abspath(os.path.join(repo_dir, rel_dir))
    if not os.path.isdir(app_dir):
        raise PrepareError(f"app dir does not exist in the repo: {rel_dir}")
    run = app.get("run")
    if isinstance(run, str):
        run = [run]
    run = [str(c).strip() for c in (run or []) if str(c).strip()]
    if not run:
        raise PrepareError("no way to start the app: add app.run to .tivm.yml (or a dev/start script)")
    url = str(app.get("url") or "").strip()
    if not url:
        raise PrepareError("no app url: add app.url to .tivm.yml (or a package.json dev script)")
    setup = app.get("setup")
    if isinstance(setup, str):
        setup = [setup]
    setup = [str(c).strip() for c in (setup or []) if str(c).strip()]
    return {
        "repo_dir": repo_dir,
        "contract": contract_path,
        "source": source or "override",
        "app": {
            "dir": app_dir,
            "setup": setup,
            "run": run,
            "url": url,
            "ready": str(app.get("ready") or "").strip(),
            "ready_timeout": float(app.get("ready_timeout") or READY_TIMEOUT),
            "env": app.get("env") or {},
        },
        "flows": contract.get("flows") or [],
    }


def _tail(path, lines=8):
    try:
        with open(path, errors="replace") as fh:
            return "".join(fh.readlines()[-lines:]).strip()
    except OSError:
        return ""


def run_setup(spec, log_path, log):
    for cmd in spec["setup"]:
        log(f"setup: {cmd}")
        with open(log_path, "a") as fh:
            fh.write(f"\n$ {cmd}\n")
            fh.flush()
            try:
                result = subprocess.run(
                    ["bash", "-lc", cmd],
                    cwd=spec["dir"], env=_env(spec["env"]),
                    stdout=fh, stderr=subprocess.STDOUT,
                    timeout=SETUP_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                raise PrepareError(f"setup timed out after {SETUP_TIMEOUT}s: {cmd}")
        if result.returncode != 0:
            raise PrepareError(f"setup failed ({result.returncode}): {cmd}\n{_tail(log_path)}")


def launch(spec, log_path, log):
    for cmd in spec["run"]:
        log(f"run: {cmd}")
        fh = open(log_path, "ab")
        fh.write(f"\n$ {cmd}\n".encode())
        fh.flush()
        try:
            proc = subprocess.Popen(
                ["bash", "-lc", cmd],
                cwd=spec["dir"], env=_env(spec["env"]),
                stdout=fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as e:
            fh.close()
            raise PrepareError(f"cannot start the app: {e}")
        _processes.append((proc, fh))


def _probe(url, ready_cmd):
    if ready_cmd:
        try:
            result = subprocess.run(
                ["bash", "-lc", ready_cmd], capture_output=True, text=True, env=_env(), timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return response.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError):
        return False


def wait_ready(spec, log, log_path):
    deadline = time.monotonic() + spec["ready_timeout"]
    last = ""
    while time.monotonic() < deadline:
        if _processes and all(proc.poll() is not None for proc, _ in _processes):
            raise PrepareError(f"the app exited before it was ready\n{_tail(log_path)}")
        if _probe(spec["url"], spec["ready"]):
            return time.monotonic()
        last = _tail(log_path, 4)
        time.sleep(2)
    raise PrepareError(f"app not ready after {int(spec['ready_timeout'])}s: {spec['url']}\n{last}")


def close_browsers(timeout=8):
    if _running("firefox"):
        try:
            subprocess.run(
                ["firefox", "--quit"], check=False, env=_env(), timeout=timeout / 2,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        deadline = time.monotonic() + timeout / 2
        while time.monotonic() < deadline and _running("firefox"):
            time.sleep(0.5)
    if _running("firefox"):
        subprocess.run(
            ["pkill", "-f", "firefox"], check=False, env=_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if _running("epiphany"):
        subprocess.run(
            ["pkill", "-f", "epiphany"], check=False, env=_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _running(pattern):
    result = subprocess.run(
        ["pgrep", "-f", pattern], capture_output=True, env=_env(),
    )
    return result.returncode == 0


def stop_all():
    while _processes:
        proc, fh = _processes.pop()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
        try:
            fh.close()
        except OSError:
            pass


def prepare(request, run_dir, log=print):
    request = dict(request or {})
    log_path = os.path.join(run_dir, "prepare.log")
    os.makedirs(run_dir, exist_ok=True)
    if request.get("repo"):
        repo_dir = clone(request["repo"], ref=request.get("ref"), pr=request.get("pr"), log=log)
    elif request.get("local_dir"):
        repo_dir = os.path.abspath(request["local_dir"])
    else:
        raise PrepareError("app.repo (or app.local_dir) is required")
    override = {k: request[k] for k in ("dir", "setup", "run", "url", "ready", "ready_timeout", "env") if k in request}
    spec = resolve(repo_dir, override)
    log(f"app contract: {spec['contract'] or spec['source']} -> {spec['app']['dir']}")
    stop_all()
    app = spec["app"]
    run_setup(app, log_path, log)
    launch(app, log_path, log)
    started = time.monotonic()
    wait_ready(app, log, log_path)
    info = {
        "repo": request.get("repo") or request.get("local_dir"),
        "ref": request.get("ref"),
        "pr": request.get("pr"),
        "sha": _sha(repo_dir),
        "dir": spec["app"]["dir"],
        "url": spec["app"]["url"],
        "ready_s": round(time.monotonic() - started, 1),
        "source": spec["source"],
        "contract": spec["contract"],
        "setup": spec["app"]["setup"],
        "run": spec["app"]["run"],
        "flows": spec["flows"],
        "log": "prepare.log",
    }
    log(f"app ready at {info['url']} in {info['ready_s']}s ({info['source']})")
    return info
