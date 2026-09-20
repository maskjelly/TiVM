import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import replay


def el(role, text, x=10, y=20, w=100, h=30):
    return {"role": role, "text": text, "x": x, "y": y, "w": w, "h": h}


class HelperTests(unittest.TestCase):
    def test_norm_and_diff_texts(self):
        self.assertEqual(replay.norm("Todos (1)"), "todos1")
        added = replay.diff_texts(["All (0)", "Add"], ["All (1)", "Add", "write docs", "write docs"])
        self.assertEqual(added, ["all1", "writedocs"])

    def test_diff_texts_ignores_short_and_duplicate(self):
        self.assertEqual(replay.diff_texts([], ["a", "ab", "Done", "Done"]), ["done"])

    def test_parse_target(self):
        self.assertEqual(replay.parse_target('push button "Add"'), ("push button", "Add"))
        self.assertEqual(replay.parse_target(""), ("", ""))
        self.assertEqual(replay.parse_target("no quotes here"), ("", ""))


class MatchTests(unittest.TestCase):
    def test_exact_then_partial_then_point(self):
        elements = [el("entry", "What needs doing?"), el("push button", "Add")]
        self.assertEqual(replay.match_element(elements, {"target_text": 'push button "Add"'})["text"], "Add")
        self.assertEqual(
            replay.match_element(elements, {"target_text": 'entry "What needs doing"'})["text"],
            "What needs doing?",
        )
        point = replay.match_element(elements, {"point": (60, 35)})
        self.assertEqual((point["role"], point["x"], point["y"]), ("point", 60, 35))
        self.assertIsNone(replay.match_element([], {"target_text": 'push button "Add"'}))


class FakeActions:
    def __init__(self):
        self.calls = []

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def double_click(self, x, y):
        self.calls.append(("double_click", x, y))

    def scroll(self, x, y, direction):
        self.calls.append((direction, x, y))

    def type_text(self, text):
        self.calls.append(("type", text))

    def press_key(self, key):
        self.calls.append(("key", key))

    def wait(self, seconds=None):
        self.calls.append(("wait", seconds))


class TraceTests(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            steps = [{"action": "click", "target_text": 'push button "Add"'}]
            path = replay.save("add a todo", "http://127.0.0.1:8080", steps, ["Add"], ["Add", "write docs"], traces_dir=d, log=lambda *_: None)
            self.assertTrue(os.path.exists(path))
            trace = replay.load("add a todo", "http://127.0.0.1:8080", traces_dir=d)
            self.assertEqual(trace["steps"], steps)
            self.assertEqual(trace["added_texts"], ["writedocs"])
            self.assertIsNone(replay.load("other task", "http://127.0.0.1:8080", traces_dir=d))
            self.assertIsNone(replay.load("add a todo", "http://elsewhere", traces_dir=d))

    def test_traces_without_visible_typed_text_are_not_saved(self):
        with tempfile.TemporaryDirectory() as d:
            steps = [{"action": "type", "typed": "ship it"}]
            path = replay.save("t", "u", steps, [], ["unrelated"], traces_dir=d, log=lambda *_: None)
            self.assertIsNone(path)
            path = replay.save("t", "u", steps, [], ["unrelated", "ship it"], traces_dir=d, log=lambda *_: None)
            self.assertTrue(path)

    def test_verify_requires_typed_text(self):
        trace = {"steps": [{"action": "type", "typed": "ship it"}], "added_texts": ["done"]}
        ok, why = replay.verify(trace, [el("label", "done")])
        self.assertFalse(ok)
        self.assertIn("typed text missing", why)
        ok, _ = replay.verify(trace, [el("label", "done"), el("label", "ship it")])
        self.assertTrue(ok)

    def test_traces_without_assertions_are_not_saved(self):
        with tempfile.TemporaryDirectory() as d:
            path = replay.save("t", "u", [{"action": "click"}], ["same"], ["same"], traces_dir=d, log=lambda *_: None)
            self.assertIsNone(path)

    def test_run_reproduces_a_trace(self):
        screens = [
            [el("entry", "New todo"), el("push button", "Add")],
            [el("entry", "New todo"), el("push button", "Add")],
            [el("entry", "New todo"), el("push button", "Add"), el("label", "write docs")],
        ]
        trace = {
            "steps": [
                {"action": "type", "target_text": 'entry "New todo"', "typed": "write docs", "key": "Return"},
                {"action": "click", "target_text": 'push button "Add"'},
            ],
            "added_texts": ["writedocs"],
        }
        calls = {"settle": 0}
        act = FakeActions()
        status, detail = replay.run(
            trace,
            perceive=lambda: screens.pop(0),
            settle=lambda action=None: calls.__setitem__("settle", calls["settle"] + 1),
            log=lambda *_: None,
            act=act,
        )
        self.assertEqual((status, detail), ("ok", "2 steps"))
        self.assertEqual(calls["settle"], 2)
        self.assertIn(("type", "write docs"), act.calls)
        self.assertIn(("key", "Return"), act.calls)
        self.assertIn("click", [c[0] for c in act.calls])

    def test_run_diverges_when_a_target_is_missing(self):
        screens = [[el("entry", "New todo")]]
        trace = {"steps": [{"action": "click", "target_text": 'push button "Add"'}], "added_texts": ["x"]}
        status, detail = replay.run(trace, perceive=lambda: screens.pop(0), settle=lambda action=None: None,
                                    log=lambda *_: None, act=FakeActions())
        self.assertEqual(status, "diverged")
        self.assertIn("target not on screen", detail)

    def test_run_diverges_when_the_final_state_is_wrong(self):
        screens = [[el("push button", "Add")], [el("push button", "Add")]]
        trace = {"steps": [{"action": "click", "target_text": 'push button "Add"'}], "added_texts": ["writedocs", "done"]}
        status, detail = replay.run(trace, perceive=lambda: screens.pop(0), settle=lambda action=None: None,
                                    log=lambda *_: None, act=FakeActions())
        self.assertEqual(status, "diverged")
        self.assertIn("expected text missing", detail)

    def test_verify_threshold(self):
        trace = {"added_texts": ["a1", "b2", "c3", "d4", "e5"]}
        elements = [el("label", "a1"), el("label", "b2"), el("label", "c3")]
        ok, _ = replay.verify(trace, elements)
        self.assertTrue(ok)
        ok, why = replay.verify(trace, [el("label", "a1")])
        self.assertFalse(ok)
        self.assertIn("missing", why)


if __name__ == "__main__":
    unittest.main()
