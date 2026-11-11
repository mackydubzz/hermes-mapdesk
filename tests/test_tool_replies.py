"""Agent-facing reply shaping and on-disk housekeeping."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

import mapdesk_core as core
from mapdesk_core import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]


def load_tools():
    try:
        import tools.registry  # noqa: F401  (present inside Hermes)
    except ImportError:
        registry = types.ModuleType("tools.registry")
        registry.tool_result = lambda value: json.dumps(value)
        registry.tool_error = lambda message: json.dumps({"error": message})
        package = types.ModuleType("tools")
        package.registry = registry
        sys.modules.setdefault("tools", package)
        sys.modules["tools.registry"] = registry
    spec = importlib.util.spec_from_file_location("mapdesk_tools_under_test", ROOT / "mapdesk_tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ring(count):
    points = [[-102 + index * 0.0001, 32 + (index % 7) * 0.0001] for index in range(count)]
    return points + [points[0]]


class SelectionReplyTests(unittest.TestCase):
    def setUp(self):
        self.tools = load_tools()

    def reply(self, features):
        return {"layer_id": "L", "feature_ids": [item["id"] for item in features], "bbox": None,
                "features": features, "total": len(features), "complete": True}

    def test_small_replies_are_returned_untouched(self):
        features = [{"type": "Feature", "id": index, "properties": {"n": index},
                     "geometry": {"type": "Point", "coordinates": [index, index]}} for index in range(100)]
        reply = self.reply(features)
        self.assertIs(self.tools._compact_selection(reply), reply)
        empty = {"layer_id": None, "feature_ids": [], "bbox": None, "features": [], "total": 0, "complete": True}
        self.assertIs(self.tools._compact_selection(empty), empty)

    def test_large_geometries_are_summarized_and_everything_else_kept(self):
        big = {"type": "Feature", "id": "county", "properties": {"name": "Midland", "area": 2.3},
               "geometry": {"type": "Polygon", "coordinates": [ring(3000)]}}
        multi = {"type": "Feature", "id": "pieces", "properties": {},
                 "geometry": {"type": "GeometryCollection", "geometries": [
                     {"type": "LineString", "coordinates": ring(400)}, {"type": "Point", "coordinates": [10, 20]}]}}
        small = {"type": "Feature", "id": "well", "properties": {"api": "42-1"},
                 "geometry": {"type": "Point", "coordinates": [-101.9, 32.1, 880.0]}}
        reply = self.reply([big, small, multi])
        before = json.dumps(reply)
        shaped = self.tools._compact_selection(reply)
        self.assertEqual(json.dumps(reply), before, "the stored result is not modified")
        self.assertLess(len(json.dumps(shaped)), 2000)
        self.assertEqual(shaped["geometry_omitted"], 2)
        self.assertEqual((shaped["feature_ids"], shaped["total"], shaped["complete"]), (["county", "well", "pieces"], 3, True))
        county, well, pieces = shaped["features"]
        self.assertEqual(county["properties"], big["properties"])
        self.assertEqual(county["geometry"]["type"], "Polygon")
        self.assertEqual(county["geometry"]["position_count"], 3001)
        self.assertTrue(county["geometry"]["omitted"])
        west, south, east, north = county["geometry"]["bbox"]
        self.assertEqual((west, south), (-102.0, 32.0))
        self.assertAlmostEqual(east, -102 + 2999 * 0.0001)
        self.assertAlmostEqual(north, 32.0006)
        self.assertEqual(well, small)
        self.assertEqual(pieces["geometry"]["position_count"], 402)
        self.assertEqual(pieces["geometry"]["bbox"][2:], [10, 32.0006])
        self.assertIn("mapdesk_query", shaped["note"])


class ReclaimTests(unittest.TestCase):
    def setUp(self):
        try:
            from testpaths import scratch_dir
        except ImportError:
            from tests.testpaths import scratch_dir
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(setattr, core, "RECLAIM_BYTES", core.RECLAIM_BYTES)
        core.RECLAIM_BYTES = 200_000

    def test_removing_large_layers_shrinks_the_file(self):
        store = WorkspaceStore(self.root)
        keep = store.add_layer("keep", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"n": 1}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]})["id"]
        bulk = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"note": "x" * 120, "n": index},
             "geometry": {"type": "Point", "coordinates": [index * 0.001, 10]}} for index in range(4000)]}
        ids = [store.add_layer(f"bulk {index}", bulk)["id"] for index in range(2)]
        path = self.root / "workspace.sqlite3"
        full = os.path.getsize(path)
        self.assertGreater(full, 1_000_000)
        for layer_id in ids:
            store.remove_layer(layer_id)
        self.assertLess(os.path.getsize(path), full / 4, "freed pages go back to the filesystem")
        self.assertEqual([layer["id"] for layer in WorkspaceStore(self.root).summary()["layers"]], [keep])
        self.assertEqual(WorkspaceStore(self.root).layer(keep)["geojson"]["features"][0]["geometry"]["coordinates"], [1, 2])


if __name__ == "__main__":
    unittest.main()
