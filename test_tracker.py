"""Offline tests: fake grid page with Mac mini tiles -> parse, filter, diff.
Run: python3 -m unittest -v test_tracker.py"""
import json
import os
import tempfile
import unittest
from pathlib import Path


def tile(part, title, model="macmini", mem="16gb", cap="512gb", price="509.00"):
    return {
        "partNumber": part, "title": title,
        "productDetailsUrl": f"/shop/product/{part.lower()}/refurbished-mac-mini?fnode=abc",
        "filters": {"dimensions": {"refurbClearModel": model, "tsMemorySize": mem, "dimensionCapacity": cap}},
        "price": {"currentPrice": {"amount": f"${price}", "raw_amount": price}},
    }


def page(tiles):
    return "<html><script>window.REFURB_GRID_BOOTSTRAP = " + json.dumps({"tiles": tiles}) + ";</script></html>"


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["REFURB_STATE"] = str(Path(self.tmp) / "state.json")
        os.environ["REFURB_CONFIG"] = str(Path(self.tmp) / "none.json")
        import importlib
        import refurb_tracker
        self.rt = importlib.reload(refurb_tracker)

    def grid(self):
        return [
            tile("FU9D3LL/A", "Refurbished Mac mini Apple M4 Chip with 10‑Core CPU and 10‑Core GPU"),
            tile("FX2H3LL/A", 'Refurbished Mac mini Apple M4 Pro Chip with 12‑Core CPU <span class="visuallyhidden">Now </span>',
                 mem="24gb", cap="1tb", price="1189.00"),
            tile("FMNM2LL/A", "Refurbished Mac mini Apple M2 Chip with 8‑Core CPU"),
            tile("FAKE1LL/A", "Refurbished 24-inch iMac Apple M4 Chip", model="imac"),
        ]

    def test_parse_and_filter(self):
        boot = self.rt.extract_bootstrap(page(self.grid()))
        minis = self.rt.parse_listings(boot)
        self.assertEqual(len(minis), 3)  # iMac excluded
        m = self.rt.apply_filters(minis, {"chips": ["M4", "M4 Pro"]})
        self.assertEqual([x["part"] for x in m], ["FU9D3LL/A", "FX2H3LL/A"])  # M2 excluded
        pro = m[1]
        self.assertEqual((pro["chip"], pro["memory_gb"], pro["storage_gb"], pro["price"]), ("M4 Pro", 24, 1024, 1189.0))
        self.assertNotIn("Now", pro["title"])
        self.assertTrue(pro["url"].endswith("refurbished-mac-mini"))
        self.assertEqual(len(self.rt.apply_filters(minis, {"chips": ["M4 Pro"]})), 1)
        self.assertEqual(len(self.rt.apply_filters(minis, {"chips": ["M4", "M4 Pro"], "min_memory_gb": 24})), 1)
        self.assertEqual(len(self.rt.apply_filters(minis, {"chips": ["M4", "M4 Pro"], "min_storage_gb": 1024})), 1)
        self.assertEqual(len(self.rt.apply_filters(minis, {"chips": ["M4", "M4 Pro"], "max_price": 600})), 1)

    def test_diff_new_price_drop_flicker_restock(self):
        rt = self.rt
        a = {"part": "A", "title": "t", "chip": "M4", "memory_gb": 16, "storage_gb": 256, "price": 509.0, "url": "u"}
        state = {"items": {}}
        self.assertEqual([e[0] for e in rt.diff(state, [a], "t1")], ["new"])
        self.assertEqual(rt.diff(state, [a], "t2"), [])                       # no duplicate
        self.assertEqual([e[0] for e in rt.diff(state, [{**a, "price": 459.0}], "t3")], ["price_drop"])
        self.assertEqual(rt.diff(state, [{**a, "price": 499.0}], "t4"), [])   # price up: silent
        rt.diff(state, [], "t5")                                              # 1 miss: flicker
        self.assertEqual(rt.diff(state, [a], "t6"), [])                       # no false restock
        for t in ("t7", "t8", "t9"):
            rt.diff(state, [], t)                                             # 3 misses: gone
        self.assertFalse(state["items"]["A"]["present"])
        self.assertEqual([e[0] for e in rt.diff(state, [a], "t10")], ["restock"])

    def test_missing_bootstrap_raises(self):
        with self.assertRaises(ValueError):
            self.rt.extract_bootstrap("<html>blocked</html>")

    def test_state_stable_across_quiet_polls(self):
        a = {"part": "A", "title": "t", "chip": "M4", "memory_gb": 16, "storage_gb": 256, "price": 509.0, "url": "u"}
        state = {"items": {}}
        self.rt.diff(state, [a], "t1")
        snap = json.dumps(state, sort_keys=True)
        self.rt.diff(state, [a], "t2")
        self.assertEqual(json.dumps(state, sort_keys=True), snap)  # no per-poll churn -> no commit

    def test_check_fails_quietly_and_keeps_state(self):
        import urllib.error
        Path(os.environ["REFURB_STATE"]).write_text('{"initialized": true, "items": {"X": {"present": true}}}')
        def boom(*a, **k):
            raise urllib.error.URLError("network down")
        self.rt.fetch_grid = boom
        self.assertEqual(self.rt.cmd_check(self.rt.load_config()), 0)
        self.assertIn('"X"', Path(os.environ["REFURB_STATE"]).read_text())

    def test_empty_grid_is_failure_not_everything_gone(self):
        Path(os.environ["REFURB_STATE"]).write_text('{"initialized": true, "items": {"X": {"present": true, "misses": 0}}}')
        self.rt.fetch_grid = lambda *a, **k: page([])
        self.rt.cmd_check(self.rt.load_config())
        self.assertIn('"misses": 0', Path(os.environ["REFURB_STATE"]).read_text())


if __name__ == "__main__":
    unittest.main()
