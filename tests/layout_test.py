import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_lib", ROOT / "scripts" / "install_lib.py")
install_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_lib)


class ToolbarTests(unittest.TestCase):
    def test_appends_beside_an_existing_tabs_row(self):
        layout = {
            "id": "tabs",
            "type": "row",
            "size": {"px": 42},
            "children": [
                {"id": "insights-original-tabs", "type": "block", "content": {"mod": "tabs"}},
                {"id": "insights-toolbar-button", "type": "block", "content": {"mod": "insights", "props": {"mode": "toolbar"}}},
            ],
        }
        updated = install_lib.with_toolbar(layout)
        self.assertEqual(layout["children"][0]["id"], "insights-original-tabs")
        self.assertEqual(len(layout["children"]), 2)
        ids = [child["id"] for child in updated["children"]]
        self.assertEqual(ids, ["insights-original-tabs", "insights-toolbar-button", "cursor-usage-toolbar-button"])
        button = updated["children"][-1]
        self.assertEqual(button["content"], {"mod": "cursor-usage", "props": {"mode": "toolbar"}})
        self.assertEqual(updated["size"], {"px": 42})

    def test_turns_a_lone_tabs_block_into_a_row(self):
        layout = {"id": "tabs", "type": "block", "size": {"px": 42}, "window_drag": True, "content": {"mod": "tabs"}}
        updated = install_lib.with_toolbar(layout)
        self.assertEqual(layout["type"], "block")
        self.assertEqual(updated["type"], "row")
        self.assertEqual(updated["size"], {"px": 42})
        self.assertTrue(updated["window_drag"])
        self.assertEqual(updated["children"][0]["content"], {"mod": "tabs"})
        self.assertEqual(updated["children"][1]["id"], "cursor-usage-toolbar-button")

    def test_refuses_a_layout_without_exactly_one_tabs_mod(self):
        with self.assertRaises(RuntimeError):
            install_lib.with_toolbar({"id": "root", "type": "row", "children": []})


if __name__ == "__main__":
    unittest.main()
