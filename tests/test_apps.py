import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import apps


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("")


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tivm-apps-")

    def test_contract_fields_and_flows(self):
        write(
            os.path.join(self.root, ".tivm.yml"),
            "tivm: 1\napp:\n  dir: web\n  setup: [\"bun install\"]\n"
            "  run: [\"bun run dev\"]\n  url: http://localhost:7777\n  ready: curl -sf localhost:7777\n"
            "flows:\n  - name: signup\n    steps: sign up as a new customer\n",
        )
        os.makedirs(os.path.join(self.root, "web"))
        spec = apps.resolve(self.root)
        self.assertEqual(spec["source"], "contract")
        self.assertEqual(spec["app"]["dir"], os.path.join(self.root, "web"))
        self.assertEqual(spec["app"]["run"], ["bun run dev"])
        self.assertEqual(spec["app"]["setup"], ["bun install"])
        self.assertEqual(spec["app"]["url"], "http://localhost:7777")
        self.assertTrue(spec["app"]["ready"].startswith("curl"))
        self.assertEqual(spec["flows"][0]["name"], "signup")

    def test_contract_relative_default_dir(self):
        write(os.path.join(self.root, ".tivm.yml"),
              "tivm: 1\napp:\n  run: [\"python3 -m http.server 8000\"]\n  url: http://localhost:8000\n")
        spec = apps.resolve(self.root)
        self.assertEqual(spec["app"]["dir"], os.path.abspath(self.root))

    def test_unsupported_contract_version(self):
        write(os.path.join(self.root, ".tivm.yml"), "tivm: 2\napp: {}\n")
        with self.assertRaises(apps.PrepareError):
            apps.resolve(self.root)

    def test_missing_run_is_an_error(self):
        write(os.path.join(self.root, ".tivm.yml"), "tivm: 1\napp:\n  url: http://localhost:3000\n")
        with self.assertRaises(apps.PrepareError) as ctx:
            apps.resolve(self.root)
        self.assertIn("start the app", str(ctx.exception))

    def test_missing_url_is_an_error(self):
        write(os.path.join(self.root, ".tivm.yml"), "tivm: 1\napp:\n  run: [\"bun run dev\"]\n")
        with self.assertRaises(apps.PrepareError) as ctx:
            apps.resolve(self.root)
        self.assertIn("url", str(ctx.exception))

    def test_missing_dir_is_an_error(self):
        write(os.path.join(self.root, ".tivm.yml"),
              "tivm: 1\napp:\n  dir: nope\n  run: [\"bun run dev\"]\n  url: http://localhost:3000\n")
        with self.assertRaises(apps.PrepareError) as ctx:
            apps.resolve(self.root)
        self.assertIn("does not exist", str(ctx.exception))

    def test_api_override_wins_over_contract(self):
        write(os.path.join(self.root, ".tivm.yml"),
              "tivm: 1\napp:\n  run: [\"bun run dev\"]\n  url: http://localhost:3000\n")
        spec = apps.resolve(self.root, {"run": ["bun start"], "url": "http://localhost:4000"})
        self.assertEqual(spec["app"]["run"], ["bun start"])
        self.assertEqual(spec["app"]["url"], "http://localhost:4000")


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tivm-infer-")

    def test_bun_lock_and_dev_script(self):
        write(os.path.join(self.root, "package.json"),
              json.dumps({"scripts": {"dev": "bun server.ts"}}))
        touch(os.path.join(self.root, "bun.lockb"))
        spec = apps.resolve(self.root)
        self.assertEqual(spec["source"], "inferred")
        self.assertEqual(spec["app"]["setup"], ["bun install"])
        self.assertEqual(spec["app"]["run"], ["bun run dev"])
        self.assertEqual(spec["app"]["url"], "http://localhost:3000")

    def test_package_manager_field_beats_lockfile(self):
        write(os.path.join(self.root, "package.json"),
              json.dumps({"packageManager": "yarn@4.1.0", "scripts": {"start": "node ."}}))
        touch(os.path.join(self.root, "package-lock.json"))
        spec = apps.resolve(self.root)
        self.assertEqual(spec["app"]["setup"], ["yarn install"])
        self.assertEqual(spec["app"]["run"], ["yarn run start"])

    def test_vite_port_detected_from_dependencies(self):
        write(os.path.join(self.root, "package.json"),
              json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}}))
        spec = apps.resolve(self.root)
        self.assertEqual(spec["app"]["url"], "http://localhost:5173")

    def test_explicit_port_flag_wins(self):
        write(os.path.join(self.root, "package.json"),
              json.dumps({"scripts": {"dev": "vite --port 4321"}, "devDependencies": {"vite": "^5"}}))
        spec = apps.resolve(self.root)
        self.assertEqual(spec["app"]["url"], "http://localhost:4321")

    def test_no_contract_and_no_package_json(self):
        with self.assertRaises(apps.PrepareError):
            apps.resolve(self.root)


class RepoTests(unittest.TestCase):
    def test_normalize_repo(self):
        self.assertEqual(apps.normalize_repo("owner/name"), "https://github.com/owner/name")
        self.assertEqual(apps.normalize_repo("https://x.test/a.git"), "https://x.test/a.git")
        self.assertEqual(apps.normalize_repo("/tmp/somewhere"), "/tmp/somewhere")
        with self.assertRaises(apps.PrepareError):
            apps.normalize_repo("")

    def test_clone_local_repo_at_ref(self):
        src = tempfile.mkdtemp(prefix="tivm-src-")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
        write(os.path.join(src, "app.txt"), "main\n")
        subprocess.run(["git", "add", "-A"], cwd=src, check=True)
        subprocess.run(["git", "commit", "-qm", "main"], cwd=src, check=True)
        subprocess.run(["git", "checkout", "-qb", "feature"], cwd=src, check=True)
        write(os.path.join(src, "app.txt"), "feature\n")
        subprocess.run(["git", "commit", "-aqm", "feature"], cwd=src, check=True)

        dest_root = tempfile.mkdtemp(prefix="tivm-clone-")
        dest = apps.clone(src, ref="feature", dest_root=dest_root)
        with open(os.path.join(dest, "app.txt")) as fh:
            self.assertEqual(fh.read(), "feature\n")
        self.assertTrue(apps._sha(dest))

    def test_prepare_end_to_end_with_local_dir(self):
        root = tempfile.mkdtemp(prefix="tivm-prep-")
        write(os.path.join(root, "index.html"), "<h1>hello</h1>")
        write(
            os.path.join(root, ".tivm.yml"),
            "tivm: 1\napp:\n  setup: []\n  run: [\"python3 -m http.server 8123\"]\n"
            "  url: http://localhost:8123\n  ready: curl -sf http://localhost:8123\n"
            "  ready_timeout: 20\n",
        )
        run_dir = tempfile.mkdtemp(prefix="tivm-run-")
        try:
            info = apps.prepare({"local_dir": root}, run_dir, log=lambda *_: None)
            self.assertEqual(info["url"], "http://localhost:8123")
            self.assertEqual(info["source"], "contract")
            self.assertTrue(os.path.exists(os.path.join(run_dir, "prepare.log")))
        finally:
            apps.stop_all()


if __name__ == "__main__":
    unittest.main()
