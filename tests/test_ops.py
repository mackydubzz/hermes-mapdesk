"""MapDesk analysis ops tests (stdlib unittest, real geodesic math)."""
import copy
import math
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mapdesk_core
import mapdesk_ops as ops


def ring(west, south, east, north):
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def square(west, south, east, north, feature_id, properties=None):
    return {"type": "Feature", "id": feature_id, "properties": properties or {},
            "geometry": {"type": "Polygon", "coordinates": [ring(west, south, east, north)]}}


def point(x, y, feature_id, properties=None):
    return {"type": "Feature", "id": feature_id, "properties": properties or {},
            "geometry": {"type": "Point", "coordinates": [x, y]}}


def line(x1, y1, x2, y2, feature_id, properties=None):
    return {"type": "Feature", "id": feature_id, "properties": properties or {},
            "geometry": {"type": "LineString", "coordinates": [[x1, y1], [x2, y2]]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class BufferTests(unittest.TestCase):
    def test_point_buffer_near_equator_spans_about_a_hundredth_of_a_degree(self):
        result = ops.buffer(collection(point(0, 0, "p1", {"name": "x"})), 1000)
        self.assertFalse(result["partial"])
        self.assertEqual(result["skipped"], 0)
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 1)
        feature = features[0]
        self.assertEqual(feature["id"], 1)
        self.assertEqual(feature["properties"], {"name": "x", "_source_id": "p1"})
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        coordinates = feature["geometry"]["coordinates"][0]
        self.assertEqual(coordinates[0], coordinates[-1])
        longitudes = [position[0] for position in coordinates]
        latitudes = [position[1] for position in coordinates]
        # 1 km geodesic: about 0.00898 degrees of longitude at the equator.
        for span in ((max(longitudes) - min(longitudes)) / 2, (max(latitudes) - min(latitudes)) / 2):
            self.assertGreater(span, 0.0085)
            self.assertLess(span, 0.0095)
        mapdesk_core._geojson(result["geojson"])

    def test_buffer_rejects_out_of_range_distance(self):
        data = collection(point(0, 0, "p1"))
        for bad in (0, -5, 100001, "10", True, None, float("inf")):
            with self.subTest(distance=bad):
                with self.assertRaises(ValueError):
                    ops.buffer(data, bad)

    def test_buffer_by_field_groups_features(self):
        data = collection(point(0, 0, "a", {"g": "A"}), point(0.005, 0, "b", {"g": "A"}),
                          point(20, 20, "c", {"g": "B"}))
        result = ops.buffer(data, 1000, "g")
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 2)
        self.assertEqual([f["properties"]["_source_id"] for f in features], ["a", "c"])
        # The two nearby group-A points merge into one buffered shape.
        self.assertEqual(features[0]["geometry"]["type"], "Polygon")
        mapdesk_core._geojson(result["geojson"])

    def test_buffer_rejects_by_field_that_is_not_a_name(self):
        data = collection(point(0, 0, "a"))
        for bad in (5, True, "", "  "):
            with self.subTest(by_field=bad):
                with self.assertRaises(ValueError):
                    ops.buffer(data, 100, bad)


class DissolveTests(unittest.TestCase):
    def test_dissolve_merges_adjacent_squares(self):
        data = collection(square(0, 0, 1, 1, "a"), square(1, 0, 2, 1, "b"))
        result = ops.dissolve(data)
        self.assertFalse(result["partial"])
        self.assertEqual(result["skipped"], 0)
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 1)
        geometry = features[0]["geometry"]
        self.assertEqual(geometry["type"], "Polygon")
        xs = [p[0] for p in geometry["coordinates"][0]]
        ys = [p[1] for p in geometry["coordinates"][0]]
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (0.0, 2.0, 0.0, 1.0))
        mapdesk_core._geojson(result["geojson"])

    def test_dissolve_by_field_keeps_group_value(self):
        data = collection(square(0, 0, 1, 1, "a", {"g": "A"}), square(1, 0, 2, 1, "b", {"g": "A"}),
                          square(5, 5, 6, 6, "c", {"g": "B"}))
        result = ops.dissolve(data, "g")
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 2)
        self.assertEqual([f["properties"]["g"] for f in features], ["A", "B"])
        self.assertEqual([f["properties"]["_source_id"] for f in features], ["a", "c"])
        xs = [p[0] for p in features[0]["geometry"]["coordinates"][0]]
        self.assertEqual((min(xs), max(xs)), (0.0, 2.0))
        mapdesk_core._geojson(result["geojson"])

    def test_dissolve_rejects_non_polygon_features(self):
        with self.assertRaises(ValueError):
            ops.dissolve(collection(point(0, 0, "a")))
        with self.assertRaises(ValueError):
            ops.dissolve(collection(line(0, 0, 1, 1, "a")))

    def test_ops_reject_empty_or_malformed_collections(self):
        for bad in (None, {}, {"type": "FeatureCollection", "features": []},
                    {"type": "GeometryCollection", "geometries": []},
                    collection({"type": "Feature", "id": "a", "properties": {},
                                "geometry": {"type": "Point", "coordinates": [181, 0]}})):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    ops.dissolve(bad)
                with self.assertRaises(ValueError):
                    ops.buffer(bad, 10)


class ClipTests(unittest.TestCase):
    def test_clip_keeps_attributes_and_intersects_geometry(self):
        data = collection(square(0, 0, 2, 2, "f1", {"name": "big"}),
                          square(10, 10, 11, 11, "f2", {"name": "far"}))
        overlay = collection(square(1, 1, 3, 3, "ov"))
        result = ops.clip(data, overlay)
        self.assertFalse(result["partial"])
        self.assertEqual(result["skipped"], 1)
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"], {"name": "big", "_source_id": "f1"})
        xs = [p[0] for p in features[0]["geometry"]["coordinates"][0]]
        ys = [p[1] for p in features[0]["geometry"]["coordinates"][0]]
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (1.0, 2.0, 1.0, 2.0))
        mapdesk_core._geojson(result["geojson"])

    def test_clip_overlay_must_be_polygons(self):
        data = collection(square(0, 0, 2, 2, "f1"))
        for bad in (collection(point(0, 0, "a")), collection(line(0, 0, 1, 1, "a"))):
            with self.subTest(overlay=bad):
                with self.assertRaises(ValueError):
                    ops.clip(data, bad)


class CentroidHullTests(unittest.TestCase):
    def test_centroid_of_square_is_its_center(self):
        result = ops.centroid(collection(square(0, 0, 2, 2, "a", {"k": 1})))
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["geometry"]["type"], "Point")
        center = features[0]["geometry"]["coordinates"]
        self.assertAlmostEqual(center[0], 1.0, places=9)
        self.assertAlmostEqual(center[1], 1.0, places=9)
        self.assertEqual(features[0]["properties"], {"k": 1, "_source_id": "a"})
        mapdesk_core._geojson(result["geojson"])

    def test_hull_covers_all_points(self):
        result = ops.hull(collection(point(0, 0, "a"), point(2, 0, "b"), point(2, 2, "c"),
                                     point(0, 2, "d"), point(1, 1, "e")))
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 1)
        geometry = features[0]["geometry"]
        self.assertEqual(geometry["type"], "Polygon")
        xs = [p[0] for p in geometry["coordinates"][0]]
        ys = [p[1] for p in geometry["coordinates"][0]]
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (0.0, 2.0, 0.0, 2.0))
        mapdesk_core._geojson(result["geojson"])


class CountPointsTests(unittest.TestCase):
    def test_count_points_tallies_per_polygon(self):
        polygons = collection(square(0, 0, 1, 1, "p1"), square(3, 3, 4, 4, "p2"))
        points = collection(point(0.2, 0.2, "q1"), point(0.5, 0.5, "q2"), point(0.9, 0.9, "q3"),
                            point(3.5, 3.5, "q4"), point(9, 9, "q5"))
        result = ops.count_points(polygons, points)
        self.assertFalse(result["partial"])
        self.assertEqual(result["skipped"], 0)
        features = result["geojson"]["features"]
        self.assertEqual([f["properties"]["point_count"] for f in features], [3, 1])
        self.assertEqual([f["properties"]["_source_id"] for f in features], ["p1", "p2"])
        mapdesk_core._geojson(result["geojson"])

    def test_count_points_supports_custom_field_and_multipoints(self):
        polygons = collection(square(0, 0, 1, 1, "p1"))
        multipoint = {"type": "Feature", "id": "m1", "properties": {},
                      "geometry": {"type": "MultiPoint", "coordinates": [[0.2, 0.2], [0.5, 0.5]]}}
        result = ops.count_points(polygons, collection(multipoint), count_field="n")
        self.assertEqual(result["geojson"]["features"][0]["properties"]["n"], 2)

    def test_count_points_rejects_wrong_geometry_types(self):
        good_points = collection(point(0.5, 0.5, "q"))
        good_polygons = collection(square(0, 0, 1, 1, "p1"))
        with self.assertRaises(ValueError):
            ops.count_points(collection(line(0, 0, 1, 1, "a")), good_points)
        with self.assertRaises(ValueError):
            ops.count_points(good_polygons, collection(square(0, 0, 1, 1, "a")))
        with self.assertRaises(ValueError):
            ops.count_points(good_polygons, good_points, count_field=" ")
        with self.assertRaises(ValueError):
            ops.count_points(good_polygons, good_points, count_field=3)


class MeasureTests(unittest.TestCase):
    def test_measure_known_geodesic_values(self):
        data = collection(line(0, 0, 1, 0, "L"), square(0, 0, 1, 1, "P"), point(5, 5, "Q"))
        result = ops.measure(data)
        entries = result["features"]
        self.assertEqual([entry["id"] for entry in entries], ["L", "P", "Q"])
        self.assertAlmostEqual(entries[0]["length_km"], 111.3195, delta=0.05)
        self.assertAlmostEqual(entries[1]["area_km2"], 12308.78, delta=5.0)
        self.assertNotIn("length_km", entries[1])
        self.assertNotIn("area_km2", entries[0])
        self.assertEqual(set(entries[2]), {"id"})
        self.assertAlmostEqual(result["total_length_km"], 111.3195, delta=0.05)
        self.assertAlmostEqual(result["total_area_km2"], 12308.78, delta=5.0)

    def test_measure_caps_feature_list_but_sums_all_totals(self):
        data = collection(*[line(0, 0, 1, 0, "L%d" % index) for index in range(150)])
        result = ops.measure(data)
        self.assertEqual(len(result["features"]), 100)
        self.assertAlmostEqual(result["total_length_km"], 150 * 111.3195, delta=0.5)


class CircleTests(unittest.TestCase):
    def test_circle_is_a_closed_64_gon_with_geodesic_radius(self):
        from pyproj import Geod
        from shapely.geometry import Polygon
        geod = Geod(ellps="WGS84")
        result = ops.circle([0, 0], 10000)
        self.assertEqual(result["type"], "Polygon")
        ring_coordinates = result["coordinates"][0]
        self.assertEqual(len(ring_coordinates), 65)
        self.assertEqual(ring_coordinates[0], ring_coordinates[-1])
        perimeter = geod.line_length([p[0] for p in ring_coordinates], [p[1] for p in ring_coordinates])
        circumference = 2 * math.pi * 10000
        self.assertLess(abs(perimeter - circumference) / circumference, 0.005)
        # Counterclockwise exterior ring => positive geodesic signed area.
        area = geod.geometry_area_perimeter(Polygon(ring_coordinates))[0]
        self.assertGreater(area, 0)
        self.assertLess(abs(area - math.pi * 10000 ** 2) / (math.pi * 10000 ** 2), 0.005)

    def test_circle_validates_inputs(self):
        for bad in (9, 5000001, 0, -10, "10", True, None, float("nan")):
            with self.subTest(meters=bad):
                with self.assertRaises(ValueError):
                    ops.circle([0, 0], bad)
        for bad in ([181, 0], [0, -91], [0], "x", None, [0, 0, 0]):
            with self.subTest(center=bad):
                with self.assertRaises(ValueError):
                    ops.circle(bad, 1000)


class BudgetTests(unittest.TestCase):
    def star_ring(self, offset, positions=40):
        coordinates = []
        for step in range(positions - 1):
            angle = 2 * math.pi * step / (positions - 1)
            coordinates.append([offset + 0.001 * math.cos(angle), 0.001 * math.sin(angle)])
        coordinates.append(list(coordinates[0]))
        return coordinates

    def test_position_budget_marks_output_partial(self):
        polygons = collection(*[
            {"type": "Feature", "id": index, "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [self.star_ring(index * 0.01)]}}
            for index in range(1600)])
        result = ops.count_points(polygons, collection(point(60, 60, "q")))
        self.assertTrue(result["partial"])
        self.assertEqual(result["skipped"], 100)
        self.assertEqual(len(result["geojson"]["features"]), 1500)
        self.assertTrue(all("point_count" in f["properties"] for f in result["geojson"]["features"]))

    def test_feature_budget_marks_output_partial(self):
        polygons = collection(*[
            {"type": "Feature", "id": index, "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [
                 [[index * 0.001, 0], [index * 0.001 + 0.0005, 0.001],
                  [index * 0.001, 0.001], [index * 0.001, 0]]]}}
            for index in range(4200)])
        result = ops.count_points(polygons, collection(point(60, 60, "q")))
        self.assertTrue(result["partial"])
        self.assertEqual(result["skipped"], 200)
        self.assertEqual(len(result["geojson"]["features"]), 4000)


class ContractTests(unittest.TestCase):
    def test_outputs_get_fresh_sequential_ids_and_source_ids(self):
        data = collection(point(0, 0, "a"), point(1, 1, "b"), point(2, 2, "c"))
        result = ops.buffer(data, 100)
        features = result["geojson"]["features"]
        self.assertEqual([f["id"] for f in features], [1, 2, 3])
        self.assertEqual([f["properties"]["_source_id"] for f in features], ["a", "b", "c"])
        mapdesk_core._geojson(result["geojson"])

    def test_no_op_mutates_its_input(self):
        mixed = collection(point(0, 0, "a", {"g": "A"}),
                           square(1, 1, 2, 2, "b", {"g": "B"}),
                           line(0, 0, 1, 1, "c"))
        polygons = collection(square(0, 0, 1, 1, "a", {"g": "A"}),
                              square(1, 0, 2, 1, "b", {"g": "B"}))
        overlay = collection(square(0.5, 0.5, 1.5, 1.5, "ov"))
        points = collection(point(0.5, 0.5, "q"))
        calls = [
            lambda: ops.buffer(mixed, 100),
            lambda: ops.buffer(mixed, 100, "g"),
            lambda: ops.dissolve(polygons),
            lambda: ops.dissolve(polygons, "g"),
            lambda: ops.centroid(mixed),
            lambda: ops.hull(mixed),
            lambda: ops.clip(mixed, overlay),
            lambda: ops.count_points(polygons, points),
            lambda: ops.measure(mixed),
        ]
        for operation in calls:
            with self.subTest(operation=operation):
                before_mixed, before_polygons = copy.deepcopy(mixed), copy.deepcopy(polygons)
                before_overlay, before_points = copy.deepcopy(overlay), copy.deepcopy(points)
                operation()
                self.assertEqual(mixed, before_mixed)
                self.assertEqual(polygons, before_polygons)
                self.assertEqual(overlay, before_overlay)
                self.assertEqual(points, before_points)


if __name__ == "__main__":
    unittest.main()
