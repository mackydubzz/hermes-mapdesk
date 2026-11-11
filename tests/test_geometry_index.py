"""Indexed geometry paths must give the answers of the plain per-feature definitions they replaced."""
import math
from pathlib import Path
import random
import tempfile
import unittest

from pyproj import Geod
from shapely.geometry import box, shape
from shapely.ops import unary_union

import mapdesk_core as core
import mapdesk_ops as ops
from mapdesk_core import WorkspaceStore


def feature(geometry, index, **properties):
    return {"type": "Feature", "id": f"f{index}", "properties": properties, "geometry": geometry}


def square(x, y, size):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]}


def scene(seed=7):
    rng = random.Random(seed)
    points = [feature({"type": "Point", "coordinates": [rng.uniform(0, 4), rng.uniform(0, 4)]}, i) for i in range(160)]
    points.append(feature({"type": "Point", "coordinates": [1.0, 0.5]}, "edge"))  # on the shared edge of two cells
    lines = []
    for i in range(60):
        x, y = rng.uniform(0, 4), rng.uniform(0, 4)
        lines.append(feature({"type": "LineString", "coordinates": [[x, y], [x + rng.uniform(-1.2, 1.2), y + rng.uniform(-1.2, 1.2)]]}, i))
    lines.append(feature({"type": "LineString", "coordinates": [[0.2, 0.5], [1.8, 0.5]]}, "span"))  # inside the union, in no single cell
    patches = [feature(square(rng.uniform(0, 3.5), rng.uniform(0, 3.5), rng.uniform(0.1, 0.9)), i) for i in range(50)]
    patches.append(feature(square(0.25, 0.25, 1.5), "span"))  # spans four cells
    patches.append(feature(square(-1, -1, 7), "cover"))  # covers every target
    cells = [feature(square(x, y, 1), f"{x}{y}") for x in range(3) for y in range(3)]  # adjacent 1x1 cells
    scattered = [feature(square(0.3, 0.3, 0.2), "a"), feature(square(2.6, 2.4, 0.3), "b"),
                 feature({"type": "LineString", "coordinates": [[0.4, 3.2], [1.4, 3.6]]}, "c")]
    return {"points": points, "lines": lines, "patches": patches}, {"cells": cells, "scattered": scattered}


class SpatialMatchTests(unittest.TestCase):
    def test_matches_equal_the_union_definition(self):
        layers, targets = scene()
        checked = 0
        for target_name, target in targets.items():
            union = unary_union([shape(item["geometry"]) for item in target])
            for layer_name, features in layers.items():
                for op in ("intersects", "within", "contains"):
                    expected = [item["id"] for item in features if getattr(shape(item["geometry"]), op)(union)]
                    actual = [item["id"] for item in core._spatial_matches(features, op, {"geojson": {"features": target}})]
                    self.assertEqual(actual, expected, f"{layer_name} {op} {target_name}")
                    checked += len(expected)
        self.assertGreater(checked, 100)

    def test_the_cases_an_index_alone_would_get_wrong(self):
        layers, targets = scene()
        cells = {"geojson": {"features": targets["cells"]}}
        within_lines = {item["id"] for item in core._spatial_matches(layers["lines"], "within", cells)}
        self.assertIn("fspan", within_lines, "a line crossing a shared edge is within the union")
        within_patches = {item["id"] for item in core._spatial_matches(layers["patches"], "within", cells)}
        self.assertIn("fspan", within_patches)
        self.assertNotIn("fcover", within_patches)
        self.assertEqual([item["id"] for item in core._spatial_matches(layers["patches"], "contains", cells)], ["fcover"])
        self.assertIn("fedge", {item["id"] for item in core._spatial_matches(layers["points"], "within", cells)})

    def test_bbox_and_radius_batches_equal_per_feature_tests(self):
        layers, _ = scene()
        for geometry in (box(0.5, 0.5, 2.5, 1.75), shape(core._circle([2.0, 2.0], 90000))):
            for name, features in layers.items():
                expected = [item["id"] for item in features if shape(item["geometry"]).intersects(geometry)]
                self.assertEqual([item["id"] for item in core._intersecting(features, geometry)], expected, name)
        self.assertEqual(core._intersecting([], box(0, 0, 1, 1)), [])

    def test_points_with_elevation_take_the_same_path(self):
        features = [feature({"type": "Point", "coordinates": [0.5, 0.5, 120.0]}, 1),
                    feature({"type": "Point", "coordinates": [5, 5]}, 2)]
        self.assertEqual([item["id"] for item in core._intersecting(features, box(0, 0, 1, 1))], ["f1"])


class SelectionDetailTests(unittest.TestCase):
    def setUp(self):
        try:
            from testpaths import scratch_dir
        except ImportError:
            from tests.testpaths import scratch_dir
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir())
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name))

    def test_detail_is_what_selection_returns(self):
        layers, targets = scene()
        strip = lambda items: {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": item["id"], "properties": {"n": index}, "geometry": item["geometry"]}
            for index, item in enumerate(items)]}
        points = self.store.add_layer("points", strip(layers["points"]))["id"]
        cells = self.store.add_layer("cells", strip(targets["cells"]))["id"]
        for arguments in ({"feature_ids": ["f1", "f5"]}, {"feature_ids": ["f1"], "invert": True},
                          {"bbox": [0.5, 0.5, 2.5, 1.75]}, {"radius": {"center": [2.0, 2.0], "meters": 90000}},
                          {"spatial": {"op": "within", "layer_id": cells}}):
            detail = self.store.set_selection(points, detail=True, **arguments)
            self.assertEqual(detail, self.store.selection(), arguments)
        detail = self.store.set_selection(points, feature_ids=["f1"], invert=True, detail=True)
        self.assertEqual(detail["total"], len(layers["points"]) - 1)
        self.assertFalse(detail["complete"])
        self.assertEqual(len(detail["features"]), 100)
        self.assertEqual(self.store.set_selection(None, detail=True), self.store.selection())


class OperationTests(unittest.TestCase):
    def collection(self, features):
        return {"type": "FeatureCollection", "features": features}

    def test_clip_equals_intersection_with_the_whole_overlay(self):
        layers, targets = scene()
        overlay = self.collection(targets["cells"][:6] + [feature(square(2.6, 2.4, 0.3), "b")])
        mask = unary_union([shape(item["geometry"]) for item in overlay["features"]])
        for name, features in layers.items():
            result = ops.clip(self.collection(features), overlay)
            expected = [(item["id"], shape(item["geometry"]).intersection(mask)) for item in features]
            kept = [(source_id, geometry) for source_id, geometry in expected if not geometry.is_empty]
            self.assertEqual([item["properties"]["_source_id"] for item in result["geojson"]["features"]],
                             [source_id for source_id, _ in kept], name)
            self.assertEqual(result["skipped"], len(expected) - len(kept), name)
            for item, (_, geometry) in zip(result["geojson"]["features"], kept):
                clipped = shape(item["geometry"])
                self.assertEqual(clipped.geom_type, geometry.geom_type, name)
                self.assertLess(clipped.symmetric_difference(geometry).area, 1e-12, name)
                self.assertAlmostEqual(clipped.length, geometry.length, places=9, msg=name)

    def test_buffer_stops_at_the_budget_with_the_same_report(self):
        points = [feature({"type": "Point", "coordinates": [-102 + (i % 40) * 0.01, 32 + (i // 40) * 0.01]}, i, n=i)
                  for i in range(1200)]
        result = ops.buffer(self.collection(points), 500)
        emitted = result["geojson"]["features"]
        self.assertTrue(result["partial"])
        self.assertEqual(len(emitted) + result["skipped"], len(points))
        self.assertLessEqual(sum(len(item["geometry"]["coordinates"][0]) for item in emitted), ops.MAX_OUTPUT_POSITIONS)
        self.assertEqual([item["properties"]["n"] for item in emitted], list(range(len(emitted))))

    def test_buffer_distance_is_geodesically_true_with_shared_projections(self):
        geod = Geod(ellps="WGS84")
        for longitude, latitude in ((-101.94, 32.29), (-102.049, 31.951), (10.049, 60.049), (139.951, -35.049)):
            for meters, tolerance in ((500, 0.001), (100000, 0.05)):
                result = ops.buffer(self.collection([feature({"type": "Point", "coordinates": [longitude, latitude]}, 1)]), meters)
                ring = result["geojson"]["features"][0]["geometry"]["coordinates"][0]
                worst = max(abs(geod.inv(longitude, latitude, x, y)[2] - meters) for x, y in ring)
                self.assertLess(worst, tolerance, f"{meters} m buffer at {longitude},{latitude} is off by {worst} m")
        ops._TRANSFORMERS.clear()
        ops.buffer(self.collection([feature({"type": "Point", "coordinates": [-102 + i * 0.001, 32.0]}, i) for i in range(40)]), 100)
        self.assertLessEqual(len(ops._TRANSFORMERS), 2, "neighbouring points share a projection")

    def test_count_points_equals_per_point_tests(self):
        layers, targets = scene()
        result = ops.count_points(self.collection(targets["cells"]), self.collection(layers["points"]))
        for item, cell in zip(result["geojson"]["features"], targets["cells"]):
            polygon = shape(cell["geometry"])
            expected = sum(1 for point in layers["points"] if shape(point["geometry"]).intersects(polygon))
            self.assertEqual(item["properties"]["point_count"], expected)
        self.assertTrue(math.isfinite(sum(item["properties"]["point_count"] for item in result["geojson"]["features"])))


if __name__ == "__main__":
    unittest.main()
