"""MapDesk core integration tests (stdlib unittest, real SQLite)."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid


def feature(geometry, properties=None, feature_id=None):
    item = {"type": "Feature", "geometry": geometry, "properties": properties or {}}
    if feature_id is not None:
        item["id"] = feature_id
    return item


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def point(x=0, y=0, feature_id=None):
    return feature({"type": "Point", "coordinates": [x, y]}, feature_id=feature_id)


def polygon(ring, feature_id=None):
    return feature({"type": "Polygon", "coordinates": [ring]}, feature_id=feature_id)


def square(x0, y0, x1, y1, feature_id=None):
    return polygon([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]], feature_id=feature_id)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        try:
            from testpaths import scratch_dir
        except ImportError:
            from tests.testpaths import scratch_dir
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def store(self):
        self.assertIsNotNone(importlib.util.find_spec("mapdesk_core"), "workspace core is missing")
        from mapdesk_core import WorkspaceStore
        return WorkspaceStore(self.root)

    def test_add_layer_persists_across_store_instances(self):
        store = self.store()
        original = collection(point(12, 34, "001"))
        layer = store.add_layer("Places", original, source={"identifier": "0007"})
        self.assertEqual(str(uuid.UUID(layer["id"])), layer["id"])
        self.assertEqual(layer["geojson"], original)
        self.assertEqual(layer["feature_count"], 1)
        self.assertTrue(layer["visible"])
        self.assertEqual(layer["style"], {"color": "#3388ff", "opacity": 0.8, "color_by": None})
        state = self.store().state()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["name"], "Untitled map")
        self.assertEqual(state["viewport"], {"center": [0, 0], "zoom": 2})
        self.assertEqual(state["layers"], [layer])
        self.assertEqual(state["selection"], {"layer_id": None, "feature_ids": [], "bbox": None})
        self.assertEqual(state["layers"][0]["source"]["identifier"], "0007")
        dbs = list(self.root.glob("*.sqlite3"))
        self.assertEqual(len(dbs), 1)
        with closing(sqlite3.connect(dbs[0])) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_rejects_invalid_geojson_without_changing_workspace(self):
        store = self.store()
        store.add_layer("Existing", collection(point()))
        before = store.state()
        invalid = [
            None, "", {}, [], {"type": "FeatureCollection", "features": []},
            {"type": "FeatureCollection", "features": {}},
            {"type": "FeatureCollection", "features": [None]},
            collection({"type": "Point", "coordinates": [0, 0]}),
            collection(feature(None)),
            collection(feature({"type": "Unknown", "coordinates": [0, 0]})),
            collection(feature({"type": "Point", "coordinates": [181, 0]})),
            collection(feature({"type": "Point", "coordinates": [0, -91]})),
            collection(feature({"type": "Point", "coordinates": [True, 0]})),
            collection(feature({"type": "Point", "coordinates": ["1", 0]})),
            collection(feature({"type": "Point", "coordinates": [0]})),
            collection(feature({"type": "Point", "coordinates": [0, 0, 0, 0]})),
            collection(feature({"type": "LineString", "coordinates": [[0, 0]]})),
            collection(feature({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1]]]})),
            collection(feature({"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]})),
            collection(feature({"type": "GeometryCollection", "geometries": []})),
            collection({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": []}),
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    store.add_layer("Invalid", value)
                self.assertEqual(store.state(), before)

    def test_accepts_all_wgs84_geometry_types(self):
        ring = [[0, 0], [2, 0], [0, 2], [0, 0]]
        geometries = [
            {"type": "Point", "coordinates": [-180, 90, 123]},
            {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            {"type": "Polygon", "coordinates": [ring]},
            {"type": "MultiPoint", "coordinates": [[0, 0], [1, 1]]},
            {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]]]},
            {"type": "MultiPolygon", "coordinates": [[ring]]},
            {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [0, 0]}]},
        ]
        store = self.store()
        for geometry in geometries:
            with self.subTest(kind=geometry["type"]):
                result = store.add_layer(geometry["type"], collection(feature(geometry)))
                self.assertEqual(result["geojson"]["features"][0]["geometry"], geometry)
        self.assertEqual(len(store.state()["layers"]), len(geometries))

    def test_feature_ids_are_deterministic_unique_and_preserve_strings(self):
        store = self.store()
        original = collection(point(feature_id="001"), point(feature_id="001"),
                              point(), point(feature_id="mapdesk:3"),
                              point(feature_id=1), point(feature_id="1"))
        first = store.add_layer("First", original)["geojson"]["features"]
        second = store.add_layer("Second", original)["geojson"]["features"]
        ids = [item.get("id") for item in first]
        self.assertNotIn(None, ids)
        self.assertEqual(len(ids), len(set((type(x), x) for x in ids)))
        self.assertEqual(ids, [item["id"] for item in second])
        self.assertEqual(ids[0], "001")
        self.assertEqual(ids[3:], ["mapdesk:3", 1, "1"])
        self.assertNotIn("id", original["features"][2])
        before = store.state()
        for invalid_id in [True, [], {}, None]:
            item = point()
            item["id"] = invalid_id
            with self.subTest(feature_id=invalid_id):
                with self.assertRaises(ValueError):
                    store.add_layer("Bad ID", collection(item))
                self.assertEqual(store.state(), before)

    def test_box_selection_uses_geometry_not_feature_envelopes(self):
        store = self.store()
        features = [point(0, 0, "inside"), point(1, 0, "boundary"), point(9, 9, "outside"),
                    feature({"type": "LineString", "coordinates": [[-2, 0], [2, 0]]}, feature_id="crossing"),
                    feature({"type": "LineString", "coordinates": [[0, 4], [4, 0]]}, feature_id="line-envelope-only"),
                    feature({"type": "Polygon", "coordinates": [[[-5, -5], [5, -5], [5, 5], [-5, 5], [-5, -5]],
                                                               [[-2, -2], [-2, 2], [2, 2], [2, -2], [-2, -2]]]}, feature_id="hole"),
                    feature({"type": "Polygon", "coordinates": [[[0, 4], [4, 4], [4, 0], [0, 4]]]}, feature_id="polygon-envelope-only"),
                    feature({"type": "Polygon", "coordinates": [[[-2, -2], [2, -2], [2, 2], [-2, 2], [-2, -2]]]}, feature_id="containing")]
        layer = store.add_layer("Shapes", collection(*features))
        self.assertTrue(callable(getattr(store, "set_selection", None)), "set_selection is missing")
        selected = store.set_selection(layer["id"], bbox=[-1, -1, 1, 1])
        expected = {"layer_id": layer["id"], "feature_ids": ["inside", "boundary", "crossing", "containing"], "bbox": [-1, -1, 1, 1]}
        self.assertEqual(selected, expected)
        self.assertEqual(self.store().state()["selection"], expected)
        self.assertEqual(store.state()["revision"], 2)
        self.assertTrue(callable(getattr(store, "selection", None)), "selection is missing")
        result = self.store().selection()
        self.assertEqual([item["id"] for item in result["features"]], expected["feature_ids"])
        self.assertEqual(result["total"], 4)
        self.assertTrue(result["complete"])

    def test_selection_payload_is_bounded_to_100_features(self):
        store = self.store()
        layer = store.add_layer("Many", collection(*(point(feature_id=str(i)) for i in range(101))))
        store.set_selection(layer["id"], bbox=[-1, -1, 1, 1])
        result = store.selection()
        self.assertEqual(len(result["features"]), 100)
        self.assertEqual(result["total"], 101)
        self.assertFalse(result["complete"])
        self.assertEqual(len(store.state()["selection"]["feature_ids"]), 101)

    def test_selects_explicit_ids_in_layer_order_and_clears(self):
        store = self.store()
        self.assertEqual(store.selection()["features"], [])
        layer = store.add_layer("IDs", collection(point(feature_id="001"), point(feature_id=1), point(feature_id="1")))
        result = store.set_selection(layer["id"], feature_ids=["1", "001", "001"])
        self.assertEqual(result["feature_ids"], ["001", "1"])
        self.assertIsNone(result["bbox"])
        self.assertEqual([item["id"] for item in store.selection()["features"]], ["001", "1"])
        self.assertEqual(store.set_selection(layer["id"])["feature_ids"], [])
        cleared = store.set_selection(None)
        self.assertEqual(cleared, {"layer_id": None, "feature_ids": [], "bbox": None})
        self.assertEqual(self.store().selection(), {**cleared, "features": [], "total": 0, "complete": True})

    def test_invalid_selection_is_atomic(self):
        store = self.store()
        layer = store.add_layer("IDs", collection(point(feature_id="001")))
        store.set_selection(layer["id"], feature_ids=["001"])
        before = store.state()
        invalid = [
            {"bbox": [1, 1, -1, -1]}, {"bbox": [0, 0, 0, 1]},
            {"bbox": [-181, 0, 1, 1]}, {"bbox": [0, 0, 1, 91]},
            {"bbox": [0, 0, float("nan"), 1]}, {"bbox": [0, 0, float("inf"), 1]},
            {"bbox": [False, 0, 1, 1]}, {"bbox": [0, 0, 1]}, {"bbox": "0001"},
            {"feature_ids": ["missing"]}, {"feature_ids": "001"}, {"feature_ids": [True]},
            {"feature_ids": ["001"], "bbox": [-1, -1, 1, 1]},
        ]
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    store.set_selection(layer["id"], **args)
                self.assertEqual(store.state(), before)
        for layer_id, args in [("missing", {}), ([], {}), (None, {"feature_ids": ["001"]}), (None, {"bbox": [-1, -1, 1, 1]})]:
            with self.subTest(layer_id=layer_id, args=args):
                with self.assertRaises(ValueError):
                    store.set_selection(layer_id, **args)
                self.assertEqual(store.state(), before)

    def test_geojson_crs_declarations_are_rejected(self):
        store = self.store()
        before = store.state()
        for location in ("collection", "feature", "geometry", "nested"):
            data = collection(point())
            target = data if location == "collection" else data["features"][0]
            if location == "geometry":
                target = target["geometry"]
            if location == "nested":
                nested = target["geometry"]
                target["geometry"] = {"type": "GeometryCollection", "geometries": [nested]}
                target = nested
            target["crs"] = {"type": "name", "properties": {"name": "EPSG:4326"}}
            with self.subTest(location=location):
                with self.assertRaisesRegex(ValueError, "CRS"):
                    store.add_layer("CRS", data)
                self.assertEqual(store.state(), before)

    def test_only_finite_json_values_are_stored_and_html_remains_data(self):
        store = self.store()
        html = '<img src=x onerror="alert(1)">; DROP TABLE layers;'
        good = collection(feature({"type": "Point", "coordinates": [0, 0]}, {"html": html, "crs": "a property", "code": "001"}))
        saved = store.add_layer(html, good, source={"identifier": "001", "text": html})
        self.assertEqual(saved["geojson"]["features"][0]["properties"], good["features"][0]["properties"])
        self.assertEqual(saved["name"], html)
        before = store.state()
        invalid = [float("nan"), float("inf"), -float("inf"), object(), {1: "numeric key"}, (1, 2)]
        for value in invalid:
            with self.subTest(kind=type(value).__name__):
                data = collection(feature({"type": "Point", "coordinates": [0, 0]}, {"bad": value}))
                with self.assertRaises(ValueError):
                    store.add_layer("Bad properties", data)
                with self.assertRaises(ValueError):
                    store.add_layer("Bad source", good, source={"bad": value})
                self.assertEqual(store.state(), before)
        cyclic = {}; cyclic["self"] = cyclic
        with self.assertRaises(ValueError):
            store.add_layer("Cycle", good, source=cyclic)
        self.assertEqual(store.state(), before)

    def test_import_limits_features_positions_bytes_and_depth(self):
        store = self.store()
        before = store.state()
        nested = "leaf"
        for _ in range(70):
            nested = [nested]
        oversized = [
            collection(*(point() for _ in range(5001))),
            collection(feature({"type": "LineString", "coordinates": [[i / 100000, 0] for i in range(100001)]})),
            collection(feature({"type": "Point", "coordinates": [0, 0]}, {"payload": "x" * 10000001})),
            collection(feature({"type": "Point", "coordinates": [0, 0]}, {"payload": nested})),
        ]
        for index, data in enumerate(oversized):
            with self.subTest(limit=index):
                with self.assertRaises(ValueError):
                    store.add_layer("Too big", data)
                self.assertEqual(store.state(), before)
        accepted = store.add_layer("At feature limit", collection(*(point() for _ in range(5000))))
        self.assertEqual(accepted["feature_count"], 5000)

    def test_state_without_data_retains_field_summaries(self):
        store = self.store()
        store.add_layer("Fields", collection(feature({"type": "Point", "coordinates": [0, 0]}, {"code": "001", "n": 1}),
                                             feature({"type": "Point", "coordinates": [1, 1]}, {"code": "002", "extra": True})))
        full = store.state()
        compact = store.state(include_data=False)
        self.assertNotIn("geojson", compact["layers"][0])
        self.assertEqual(compact["layers"][0]["fields"], ["code", "extra", "n"])
        self.assertEqual(full["layers"][0]["fields"], compact["layers"][0]["fields"])
        self.assertEqual(compact["revision"], full["revision"])
        full["layers"][0]["geojson"]["features"].clear()
        self.assertEqual(store.state()["layers"][0]["feature_count"], 2)

    def test_update_layer_merges_style_and_persists(self):
        store = self.store()
        layer = store.add_layer("Original", collection(feature({"type": "Point", "coordinates": [0, 0]}, {"category": "A"})))
        self.assertTrue(callable(getattr(store, "update_layer", None)), "update_layer is missing")
        updated = store.update_layer(layer["id"], {"name": "<b>Renamed</b>", "visible": False,
                                                  "style": {"color": "#abcdef", "color_by": "category"}})
        self.assertEqual(updated["name"], "<b>Renamed</b>")
        self.assertFalse(updated["visible"])
        self.assertEqual(updated["style"], {"color": "#abcdef", "opacity": 0.8, "color_by": "category"})
        self.assertEqual(updated["geojson"], layer["geojson"])
        self.assertEqual(self.store().state()["layers"], [updated])
        self.assertEqual(store.state()["revision"], 2)
        updated = store.update_layer(layer["id"], {"style": {"opacity": 0, "color_by": None}})
        self.assertEqual(updated["style"], {"color": "#abcdef", "opacity": 0, "color_by": None})

    def test_layer_updates_have_strict_whitelist_and_atomic_validation(self):
        store = self.store()
        layer = store.add_layer("Original", collection(point()))
        before = store.state()
        invalid = [None, [], {}, {"id": "new"}, {"geojson": collection(point(1, 1))},
                   {"source": {}}, {"visible": 1}, {"name": ""}, {"name": "   "},
                   {"name": 1}, {"name": "x" * 257},
                   {"style": None}, {"style": {"weight": 2}}, {"style": {"color": "red"}},
                   {"style": {"color": "#fff"}}, {"style": {"color": "#gggggg"}},
                   {"style": {"opacity": -0.1}}, {"style": {"opacity": 1.1}},
                   {"style": {"opacity": True}}, {"style": {"opacity": float("nan")}},
                   {"style": {"color_by": 3}}, {"style": {"color_by": "missing"}},
                   {"name": "Partial mutation", "style": {"opacity": 10}}]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    store.update_layer(layer["id"], changes)
                self.assertEqual(store.state(), before)
        with self.assertRaises(ValueError):
            store.update_layer("missing", {"name": "No"})
        self.assertEqual(store.state(), before)

    def test_remove_layer_deletes_only_that_layer_and_its_selection(self):
        store = self.store()
        kept = store.add_layer("Kept", collection(point(1, 1, "a")))
        gone = store.add_layer("Gone", collection(point(2, 2, "b")))
        store.set_selection(gone["id"], ["b"])
        before = store.state()
        for layer_id in ("missing", None, 3):
            with self.subTest(layer_id=layer_id):
                with self.assertRaises(ValueError):
                    store.remove_layer(layer_id)
                self.assertEqual(store.state(), before)
        self.assertEqual(store.remove_layer(gone["id"]), {"removed": gone["id"], "name": "Gone", "feature_count": 1})
        state = self.store().state()
        self.assertEqual(state["layers"], [kept])
        self.assertEqual(state["selection"], {"layer_id": None, "feature_ids": [], "bbox": None})
        self.assertEqual(state["revision"], before["revision"] + 1)
        self.assertEqual(store.selection()["total"], 0)
        store.set_selection(kept["id"], ["a"])
        other = store.add_layer("Other", collection(point(3, 3)))
        store.remove_layer(other["id"])
        self.assertEqual(store.state()["selection"]["feature_ids"], ["a"])

    def test_add_validates_layer_metadata(self):
        store = self.store()
        before = store.state()
        for name in [None, 2, "", " ", "x" * 257]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    store.add_layer(name, collection(point()))
                self.assertEqual(store.state(), before)
        for source in ["file", [], 1, True]:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    store.add_layer("Valid name", collection(point()), source=source)
                self.assertEqual(store.state(), before)

    def test_viewport_updates_are_validated_and_visible_to_other_instances(self):
        store, observer = self.store(), self.store()
        self.assertTrue(callable(getattr(store, "set_viewport", None)), "set_viewport is missing")
        viewport = store.set_viewport([12.5, -30], 4.5)
        self.assertEqual(viewport, {"center": [12.5, -30], "zoom": 4.5})
        self.assertEqual(observer.state()["viewport"], viewport)
        self.assertEqual(observer.state()["revision"], 1)
        before = observer.state()
        invalid = [([181, 0], 2), ([0, 91], 2), ([0], 2), ([0, 0, 0], 2), ("00", 2),
                   ([True, 0], 2), ([float("nan"), 0], 2), ([0, 0], float("inf")),
                   ([0, 0], -1), ([0, 0], 25), ([0, 0], True), ([0, 0], "2")]
        for center, zoom in invalid:
            with self.subTest(center=center, zoom=zoom):
                with self.assertRaises(ValueError):
                    store.set_viewport(center, zoom)
                self.assertEqual(observer.state(), before)

    def test_workspace_name_persists_without_creating_a_path(self):
        store = self.store()
        self.assertTrue(callable(getattr(store, "save_name", None)), "save_name is missing")
        name = "../../<b>My map</b>"
        self.assertEqual(store.save_name(name), name)
        self.assertEqual(self.store().state()["name"], name)
        self.assertEqual(self.store().state()["revision"], 1)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["workspace.sqlite3"])
        before = store.state()
        for name in [None, "", "  ", 1, "x" * 257]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    store.save_name(name)
                self.assertEqual(store.state(), before)

    def test_unrepresentable_coordinates_are_validation_errors(self):
        store = self.store()
        before = store.state()
        for coords in [[10 ** 400, 0], [0, 0, 10 ** 400], [float("nan"), 0], [0, float("inf")]]:
            with self.subTest(kind="coordinate"):
                with self.assertRaises(ValueError):
                    store.add_layer("Invalid", collection(feature({"type": "Point", "coordinates": coords})))
                self.assertEqual(store.state(), before)

    def test_layers_get_sequential_order_and_data_revision(self):
        store = self.store()
        first = store.add_layer("First", collection(point(0, 0, "a")))
        second = store.add_layer("Second", collection(point(1, 1, "b")))
        third = store.add_layer("Third", collection(point(2, 2, "c")))
        self.assertEqual([first["order"], second["order"], third["order"]], [0, 1, 2])
        self.assertEqual([first["data_revision"], second["data_revision"], third["data_revision"]], [1, 2, 3])
        state = store.state()
        self.assertEqual([layer["name"] for layer in state["layers"]], ["First", "Second", "Third"])
        self.assertEqual(state["revision"], 3)
        compact = store.state(include_data=False)
        self.assertEqual([layer["order"] for layer in compact["layers"]], [0, 1, 2])

    def test_summary_matches_state_without_geojson(self):
        store = self.store()
        layer = store.add_layer("Places", collection(point(12, 34, "001")), source={"identifier": "0007"})
        store.set_selection(layer["id"], ["001"])
        self.assertTrue(callable(getattr(store, "summary", None)), "summary is missing")
        summary = store.summary()
        state = store.state()
        self.assertNotIn("geojson", summary["layers"][0])
        for key in ("id", "name", "order", "data_revision", "style", "visible",
                    "feature_count", "fields", "source"):
            self.assertEqual(summary["layers"][0][key], state["layers"][0][key])
        self.assertEqual(summary["selection"], state["selection"])
        self.assertEqual(summary["revision"], state["revision"])
        self.assertEqual(summary["name"], state["name"])
        self.assertEqual(summary["viewport"], state["viewport"])

    def test_layer_accessor_returns_the_stored_layer(self):
        store = self.store()
        layer = store.add_layer("Places", collection(point(1, 2, "001")))
        self.assertTrue(callable(getattr(store, "layer", None)), "layer accessor is missing")
        self.assertEqual(store.layer(layer["id"]), layer)
        self.assertEqual(store.layer(layer["id"])["geojson"]["features"][0]["id"], "001")
        for bad in ("missing", None, 3, []):
            with self.subTest(layer_id=bad):
                with self.assertRaises(ValueError):
                    store.layer(bad)

    def test_reorder_rewrites_draw_order(self):
        store = self.store()
        a = store.add_layer("A", collection(point(0, 0)))
        b = store.add_layer("B", collection(point(1, 1)))
        c = store.add_layer("C", collection(point(2, 2)))
        self.assertTrue(callable(getattr(store, "reorder", None)), "reorder is missing")
        reordered = store.reorder([c["id"], a["id"], b["id"]])
        self.assertEqual([layer["id"] for layer in reordered], [c["id"], a["id"], b["id"]])
        for layer in reordered:
            self.assertNotIn("geojson", layer)
        state = store.state()
        self.assertEqual([layer["id"] for layer in state["layers"]], [c["id"], a["id"], b["id"]])
        self.assertEqual([layer["order"] for layer in state["layers"]], [0, 1, 2])
        before = store.state()
        for bad in ([a["id"]], [a["id"], b["id"], c["id"], "missing"],
                    [a["id"], a["id"], c["id"]], "not a list", [a["id"], 3, c["id"]], None):
            with self.subTest(layer_ids=bad):
                with self.assertRaisesRegex(ValueError, "exactly match"):
                    store.reorder(bad)
                self.assertEqual(store.state(), before)
        store.reorder([c["id"], b["id"], a["id"]])
        self.assertEqual([layer["name"] for layer in store.state()["layers"]], ["C", "B", "A"])

    def test_radius_selection_resolves_and_keeps_locator(self):
        store = self.store()
        layer = store.add_layer("Spots", collection(
            point(0, 0, "origin"), point(0.01, 0, "near"), point(0, 0.04, "north"),
            point(0, 0.05, "beyond"), point(2, 0, "distant")))
        selected = store.set_selection(layer["id"], radius={"center": [0, 0], "meters": 5000})
        expected = {"layer_id": layer["id"], "feature_ids": ["origin", "near", "north"],
                    "bbox": None, "radius": {"center": [0, 0], "meters": 5000}}
        self.assertEqual(selected, expected)
        self.assertEqual(self.store().state()["selection"], expected)
        result = store.selection()
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["radius"], {"center": [0, 0], "meters": 5000})
        before = store.state()
        invalid = [
            {"radius": {"center": [0, 0], "meters": 9}},
            {"radius": {"center": [0, 0], "meters": 5_000_001}},
            {"radius": {"center": [0, 0], "meters": "100"}},
            {"radius": {"center": [0, 0], "meters": True}},
            {"radius": {"center": [0, 0]}},
            {"radius": {"center": [0]}},
            {"radius": {"center": [181, 0], "meters": 100}},
            {"radius": {"center": "0,0", "meters": 100}},
            {"radius": {"meters": 100}},
            {"radius": {"center": [0, 0], "meters": 100}, "bbox": [-1, -1, 1, 1]},
            {"radius": {"center": [0, 0], "meters": 100}, "feature_ids": ["origin"]},
        ]
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    store.set_selection(layer["id"], **args)
                self.assertEqual(store.state(), before)
        with self.assertRaises(ValueError):
            store.set_selection(None, radius={"center": [0, 0], "meters": 100})
        self.assertEqual(store.state(), before)

    def test_spatial_selection_uses_target_layer_union(self):
        store = self.store()
        active = store.add_layer("Active", collection(
            point(0.5, 0.5, "in-a"), point(10.5, 0.5, "in-b"), point(5, 5, "out"),
            square(0.5, 0.5, 2, 1.5, "overlap-a"),
            square(0.2, 0.2, 0.4, 0.4, "inside-a"),
            square(-1, -1, 12, 2, "container")))
        target = store.add_layer("Target", collection(square(0, 0, 1, 1, "a"), square(10, 0, 11, 1, "b")))
        expected = {"intersects": ["in-a", "in-b", "overlap-a", "inside-a", "container"],
                    "within": ["in-a", "in-b", "inside-a"],
                    "contains": ["container"]}
        for op, ids in expected.items():
            with self.subTest(op=op):
                selected = store.set_selection(active["id"], spatial={"op": op, "layer_id": target["id"]})
                self.assertEqual(selected, {"layer_id": active["id"], "feature_ids": ids, "bbox": None})
                self.assertEqual(store.selection()["total"], len(ids))
        store.set_selection(active["id"], spatial={"op": "intersects", "layer_id": target["id"]})
        before = store.state()
        invalid = [
            {"spatial": {"op": "touches", "layer_id": target["id"]}},
            {"spatial": {"layer_id": target["id"]}},
            {"spatial": {"op": "intersects"}},
            {"spatial": {"op": "intersects", "layer_id": "missing"}},
            {"spatial": {"op": "intersects", "layer_id": 3}},
            {"spatial": "target"},
            {"spatial": {"op": "intersects", "layer_id": target["id"]}, "bbox": [-1, -1, 1, 1]},
        ]
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    store.set_selection(active["id"], **args)
                self.assertEqual(store.state(), before)

    def test_invert_selection_takes_the_complement(self):
        store = self.store()
        layer = store.add_layer("IDs", collection(point(feature_id="a"), point(feature_id="b"), point(feature_id="c")))
        selected = store.set_selection(layer["id"], feature_ids=["a"], invert=True)
        self.assertEqual(selected, {"layer_id": layer["id"], "feature_ids": ["b", "c"], "bbox": None})
        self.assertEqual(store.selection()["total"], 2)
        self.assertEqual(store.set_selection(layer["id"], feature_ids=[], invert=True)["feature_ids"], ["a", "b", "c"])
        before = store.state()
        invalid = [
            {"invert": True},
            {"invert": True, "bbox": [-1, -1, 1, 1]},
            {"invert": True, "feature_ids": ["a"], "bbox": [-1, -1, 1, 1]},
            {"invert": "yes", "feature_ids": ["a"]},
        ]
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    store.set_selection(layer["id"], **args)
                self.assertEqual(store.state(), before)
        with self.assertRaises(ValueError):
            store.set_selection(None, feature_ids=["a"], invert=True)
        self.assertEqual(store.state(), before)

    def test_query_conditions_and_limits(self):
        store = self.store()
        layer = store.add_layer("Rows", collection(
            feature({"type": "Point", "coordinates": [0, 0]}, {"name": "alpha", "fuel": "NG", "n": 5}, "001"),
            feature({"type": "Point", "coordinates": [1, 1]}, {"name": "Beta", "fuel": "OIL", "n": "12"}, "002"),
            feature({"type": "Point", "coordinates": [2, 2]}, {"name": "gamma", "n": 3}, "003"),
            feature({"type": "Point", "coordinates": [3, 3]}, {"name": "delta", "fuel": "NG"}, "004")))
        self.assertTrue(callable(getattr(store, "query", None)), "query is missing")
        cases = [
            ([{"field": "n", "op": "eq", "value": 5}], ["001"]),
            ([{"field": "n", "op": "eq", "value": "5"}], ["001"]),
            ([{"field": "n", "op": "eq", "value": "12"}], ["002"]),
            ([{"field": "fuel", "op": "eq", "value": "NG"}], ["001", "004"]),
            ([{"field": "fuel", "op": "eq", "value": "ng"}], []),
            ([{"field": "fuel", "op": "ne", "value": "NG"}], ["002", "003"]),
            ([{"field": "name", "op": "eq", "value": "alpha"}], ["001"]),
            ([{"field": "n", "op": "gt", "value": 4}], ["001", "002"]),
            ([{"field": "n", "op": "gte", "value": 5}], ["001", "002"]),
            ([{"field": "n", "op": "lt", "value": 4}], ["003"]),
            ([{"field": "n", "op": "lte", "value": "3"}], ["003"]),
            ([{"field": "fuel", "op": "in", "value": ["NG", "OIL"]}], ["001", "002", "004"]),
            ([{"field": "n", "op": "in", "value": [3, "12"]}], ["002", "003"]),
            ([{"field": "name", "op": "like", "value": "AM"}], ["003"]),
            ([{"field": "fuel", "op": "isnull", "value": None}], ["003"]),
            ([{"field": "n", "op": "isnull", "value": None}], ["004"]),
            ([{"field": "fuel", "op": "eq", "value": "NG"}, {"field": "n", "op": "gt", "value": 4}], ["001"]),
        ]
        for where, ids in cases:
            with self.subTest(where=where):
                result = store.query(layer["id"], where)
                self.assertEqual(result, {"layer_id": layer["id"], "total": len(ids),
                                          "feature_ids": ids, "complete": True})
        self.assertEqual(store.query(layer["id"])["feature_ids"], ["001", "002", "003", "004"])
        truncated = store.query(layer["id"], limit=2)
        self.assertEqual(truncated, {"layer_id": layer["id"], "total": 4,
                                    "feature_ids": ["001", "002"], "complete": False})
        self.assertEqual(store.query(layer["id"], limit=1000)["complete"], True)

    def test_query_supports_spatial_filters(self):
        store = self.store()
        active = store.add_layer("Points", collection(
            feature({"type": "Point", "coordinates": [0.5, 0.5]}, {"kind": "a"}, "in-a"),
            feature({"type": "Point", "coordinates": [10.5, 0.5]}, {"kind": "b"}, "in-b"),
            feature({"type": "Point", "coordinates": [5, 5]}, {"kind": "a"}, "out")))
        target = store.add_layer("Target", collection(square(0, 0, 1, 1, "a"), square(10, 0, 11, 1, "b")))
        result = store.query(active["id"], spatial={"op": "intersects", "layer_id": target["id"]})
        self.assertEqual(result, {"layer_id": active["id"], "total": 2, "feature_ids": ["in-a", "in-b"], "complete": True})
        within = store.query(active["id"], spatial={"op": "within", "layer_id": target["id"]})
        self.assertEqual(within["feature_ids"], ["in-a", "in-b"])
        combined = store.query(active["id"], where=[{"field": "kind", "op": "eq", "value": "a"}],
                               spatial={"op": "intersects", "layer_id": target["id"]})
        self.assertEqual(combined["feature_ids"], ["in-a"])

    def test_query_validates_input_atomically(self):
        store = self.store()
        layer = store.add_layer("Rows", collection(feature({"type": "Point", "coordinates": [0, 0]}, {"fuel": "NG"}, "001")))
        before = store.state()
        invalid = [
            {"where": [{"field": "missing", "op": "eq", "value": 1}]},
            {"where": [{"field": "fuel", "op": "equals", "value": "NG"}]},
            {"where": [{"field": "fuel", "op": "isnull", "value": "NG"}]},
            {"where": [{"field": "fuel", "op": "in", "value": "NG"}]},
            {"where": [{"field": "fuel", "op": "like", "value": 5}]},
            {"where": [{"field": "fuel", "op": "eq", "value": {"deep": 1}}]},
            {"where": [{"field": "fuel", "op": "eq"}]},
            {"where": [{"field": "fuel", "op": "eq", "value": "NG", "extra": 1}]},
            {"where": "fuel = NG"},
            {"limit": 0}, {"limit": 1001}, {"limit": "5"}, {"limit": 2.5}, {"limit": True},
        ]
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    store.query(layer["id"], **args)
                self.assertEqual(store.state(), before)
        with self.assertRaisesRegex(ValueError, "Unknown query field"):
            store.query(layer["id"], where=[{"field": "missing", "op": "eq", "value": 1}])
        for bad_id in ("missing", None, 3):
            with self.subTest(layer_id=bad_id):
                with self.assertRaises(ValueError):
                    store.query(bad_id)
        self.assertEqual(store.state(), before)

    def test_style_accepts_classification_keys(self):
        store = self.store()
        layer = store.add_layer("Styled", collection(feature({"type": "Point", "coordinates": [0, 0]}, {"category": "A"})))
        updated = store.update_layer(layer["id"], {"style": {
            "class_method": "jenks", "class_count": 5, "palette": "okabe",
            "label_field": "category", "line_width": 3, "circle_radius": 7}})
        self.assertEqual(updated["style"], {"color": "#3388ff", "opacity": 0.8, "color_by": None,
                                            "class_method": "jenks", "class_count": 5, "palette": "okabe",
                                            "label_field": "category", "line_width": 3, "circle_radius": 7})
        updated = store.update_layer(layer["id"], {"style": {"label_field": None, "line_width": 0.5}})
        self.assertIsNone(updated["style"]["label_field"])
        self.assertEqual(updated["style"]["line_width"], 0.5)
        before = store.state()
        invalid = [
            {"style": {"class_method": "kmeans"}},
            {"style": {"class_count": 2}},
            {"style": {"class_count": 8}},
            {"style": {"class_count": "5"}},
            {"style": {"class_count": True}},
            {"style": {"palette": "rainbow"}},
            {"style": {"label_field": 3}},
            {"style": {"line_width": 0.4}},
            {"style": {"line_width": 8.5}},
            {"style": {"line_width": True}},
            {"style": {"circle_radius": 1}},
            {"style": {"circle_radius": 16}},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    store.update_layer(layer["id"], changes)
                self.assertEqual(store.state(), before)


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        try:
            from testpaths import scratch_dir
        except ImportError:
            from tests.testpaths import scratch_dir
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def store(self):
        self.assertIsNotNone(importlib.util.find_spec("mapdesk_core"), "workspace core is missing")
        from mapdesk_core import WorkspaceStore
        return WorkspaceStore(self.root)

    def test_0_1_layer_rows_gain_order_and_data_revision_on_open(self):
        from mapdesk_core import WorkspaceStore
        path = self.root / "workspace.sqlite3"
        # Simulate a 0.1-era database: layers written without order/data_revision.
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        connection.execute("CREATE TABLE layers (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        connection.execute("INSERT INTO workspace VALUES (1, ?)", (json.dumps({
            "revision": 3, "name": "Old map", "viewport": {"center": [0, 0], "zoom": 2},
            "selection": {"layer_id": None, "feature_ids": [], "bbox": None},
        }),))
        legacy = {"id": "legacy-1", "name": "Old layer",
                  "geojson": collection(point(1, 2)),
                  "source": {"kind": "geojson"}, "style": {"color": "#3388ff", "opacity": 0.8, "color_by": None},
                  "visible": True, "feature_count": 1, "fields": []}
        connection.execute("INSERT INTO layers VALUES (?, ?)", ("legacy-1", json.dumps(legacy)))
        connection.commit()
        connection.close()

        store = self.store()  # opening heals the legacy rows
        healed = store.state()["layers"][0]
        self.assertEqual(healed["order"], 0)
        self.assertEqual(type(healed["data_revision"]), int)
        self.assertGreaterEqual(healed["data_revision"], 1)
        self.assertEqual(healed["name"], "Old layer")

        # Reopening a healed store is stable (no further revision churn).
        before = store.state()
        reopened = WorkspaceStore(self.root)
        self.assertEqual(reopened.state()["layers"][0]["data_revision"], healed["data_revision"])
        self.assertEqual(reopened.state()["revision"], before["revision"])


class FrameTests(unittest.TestCase):
    def setUp(self):
        try:
            from testpaths import scratch_dir
        except ImportError:
            from tests.testpaths import scratch_dir
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def store(self):
        self.assertIsNotNone(importlib.util.find_spec("mapdesk_core"), "workspace core is missing")
        from mapdesk_core import WorkspaceStore
        return WorkspaceStore(self.root)

    def test_frame_stores_request_and_bumps_revision(self):
        store = self.store()
        before = store.state()["revision"]
        request = store.frame([-98.8, 29.1, -95.1, 33.1])
        self.assertEqual(request["bbox"], [-98.8, 29.1, -95.1, 33.1])
        self.assertTrue(request["request_id"])
        state = store.state()
        self.assertEqual(state["viewport_request"]["request_id"], request["request_id"])
        self.assertEqual(state["revision"], before + 1)
        # A second frame supersedes the first with a fresh request id.
        second = store.frame([-106.6, 25.8, -93.5, 36.5])
        self.assertNotEqual(second["request_id"], request["request_id"])
        self.assertEqual(store.state()["viewport_request"]["bbox"], second["bbox"])

    def test_frame_rejects_bad_bboxes(self):
        store = self.store()
        for bad in ([-95, 29, -98, 33], [0, 0, 0, 0], [1, 2, 3], [-200, 0, 0, 1], [0, 0, 1, "x"]):
            with self.assertRaises(ValueError):
                store.frame(bad)


if __name__ == "__main__":
    unittest.main()
