"""Store fast paths: the schema gate and the SQLite-JSON metadata paths must match the full-parse paths."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import mapdesk_core as core
from mapdesk_core import WorkspaceStore


def collection(count):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": f"é \"q\" {index}", "value": index, "kind": "ab"[index % 2]},
         "geometry": {"type": "Point", "coordinates": [index * 0.01, 10 + index * 0.01]}}
        for index in range(count)]}


class StoreFastPathTests(unittest.TestCase):
    def setUp(self):
        try:
            from testpaths import scratch_dir
        except ImportError:
            from tests.testpaths import scratch_dir
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(setattr, core, "_JSON1", None)

    def filled(self):
        store = WorkspaceStore(self.root)
        ids = [store.add_layer(name, collection(5 + index))["id"] for index, name in enumerate(("A", "B", "C"))]
        return store, ids

    def raw(self, layer_id):
        connection = sqlite3.connect(self.root / "workspace.sqlite3")
        try:
            data = connection.execute("SELECT data FROM layers WHERE id=?", (layer_id,)).fetchone()[0]
            meta = connection.execute("SELECT meta FROM layer_meta WHERE id=?", (layer_id,)).fetchone()
            return core._assemble(data, meta[0] if meta else None)
        finally:
            connection.close()

    def test_json_functions_are_available_here(self):
        store, _ = self.filled()
        with store._connection() as connection:
            self.assertTrue(core._json1(connection), "this SQLite build should take the fast paths")

    def test_reopening_does_not_write(self):
        store, _ = self.filled()
        before = store.state()
        connection = sqlite3.connect(self.root / "workspace.sqlite3")
        self.assertIn(connection.execute("PRAGMA user_version").fetchone()[0], (12, 13))
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.close()
        self.assertEqual(mode, "wal" if store._wal else "delete")
        calls = []
        original = WorkspaceStore._migrate_legacy_layers
        WorkspaceStore._migrate_legacy_layers = lambda self, connection: calls.append(1)
        self.addCleanup(setattr, WorkspaceStore, "_migrate_legacy_layers", original)
        reopened = WorkspaceStore(self.root)
        self.assertEqual(calls, [], "an initialized store must not rescan every layer on open")
        self.assertEqual(reopened.state(), before)

    def test_store_initialized_before_the_log_switch_is_upgraded_once(self):
        store, ids = self.filled()
        connection = sqlite3.connect(self.root / "workspace.sqlite3", isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA user_version=1")
        connection.close()
        reopened = WorkspaceStore(self.root)
        connection = sqlite3.connect(self.root / "workspace.sqlite3")
        self.assertIn(connection.execute("PRAGMA user_version").fetchone()[0], (12, 13))
        connection.close()
        self.assertEqual([layer["id"] for layer in reopened.summary()["layers"]], ids)
        reopened.set_viewport([1, 2], 3)
        self.assertEqual(WorkspaceStore(self.root).summary()["viewport"], {"center": [1, 2], "zoom": 3})

    def whole_document_database(self, version):
        """A database as the unsplit builds left it: each layers row holds the whole layer document."""
        store, ids = self.filled()
        store.update_layer(ids[0], {"style": {"color_by": "kind"}, "name": "Renamed é"})
        expected = store.state()
        connection = sqlite3.connect(self.root / "workspace.sqlite3", isolation_level=None)
        for layer in expected["layers"]:
            connection.execute("UPDATE layers SET data=? WHERE id=?", (json.dumps(layer, ensure_ascii=False), layer["id"]))
        connection.execute("DROP TABLE layer_meta")
        connection.execute(f"PRAGMA user_version={version}")
        connection.close()
        return ids, expected

    def test_whole_document_rows_are_split_on_open_without_changing_the_workspace(self):
        for json1 in (None, False):
            with self.subTest(sqlite_json=json1 is None):
                core._JSON1 = json1
                ids, expected = self.whole_document_database(2)
                store = WorkspaceStore(self.root)
                self.assertEqual(store.state(), expected)
                connection = sqlite3.connect(self.root / "workspace.sqlite3")
                rows = connection.execute("SELECT l.data, m.meta FROM layers l JOIN layer_meta m ON m.id=l.id").fetchall()
                self.assertIn(connection.execute("PRAGMA user_version").fetchone()[0], (12, 13))
                connection.close()
                self.assertEqual(len(rows), len(expected["layers"]))
                for data, meta in rows:
                    self.assertEqual(set(json.loads(data)), {"type", "features"}, "the big column holds only the features")
                    self.assertNotIn("geojson", json.loads(meta))
                self.assertEqual(store.update_layer(ids[1], {"visible": False}, include_data=False)["visible"], False)
                self.assertEqual(store.layer(ids[1])["geojson"], expected["layers"][1]["geojson"])
                core._JSON1 = None
                for layer in store.summary()["layers"]:
                    store.remove_layer(layer["id"])

    def test_a_whole_document_row_added_later_is_read_and_then_split_by_a_write(self):
        store, ids = self.filled()
        late = dict(store.layer(ids[0]), id="late-row", name="Late", order=7)
        connection = sqlite3.connect(self.root / "workspace.sqlite3", isolation_level=None)
        connection.execute("INSERT INTO layers VALUES (?, ?)", ("late-row", json.dumps(late)))
        connection.close()
        self.assertEqual(store.layer("late-row"), late)
        self.assertEqual(store.summary()["layers"][-1], {key: value for key, value in late.items() if key != "geojson"})
        self.assertEqual(json.loads(store.layer_reply_text("late-row")),
                         {"layer_id": "late-row", "data_revision": late["data_revision"], "geojson": late["geojson"]})
        self.assertEqual(store.update_layer("late-row", {"name": "Late renamed"}, include_data=False)["name"], "Late renamed")
        self.assertEqual(self.raw("late-row"), dict(late, name="Late renamed"))
        self.assertEqual(store.remove_layer("late-row")["name"], "Late renamed")
        self.assertEqual(len(store.summary()["layers"]), 3)

    def test_layer_reply_text_is_the_layer_action_reply(self):
        store, ids = self.filled()
        layer = store.layer(ids[2])
        self.assertEqual(json.loads(store.layer_reply_text(ids[2])),
                         {"layer_id": layer["id"], "data_revision": layer["data_revision"], "geojson": layer["geojson"]})
        with self.assertRaisesRegex(ValueError, "Unknown layer ID"):
            store.layer_reply_text("nope")
        with self.assertRaisesRegex(ValueError, "Layer ID must be a string"):
            store.layer_reply_text(3)

    def test_summary_matches_full_state_without_bodies(self):
        store, _ = self.filled()
        full = store.state()
        for layer in full["layers"]:
            layer.pop("geojson")
        self.assertEqual(store.summary(), full)
        self.assertEqual([list(layer) for layer in store.summary()["layers"]], [list(layer) for layer in full["layers"]])

    def assert_same_as_slow_path(self, changes):
        store, ids = self.filled()
        fast = store.update_layer(ids[1], changes, include_data=False)
        fast_row = self.raw(ids[1])
        core._JSON1 = False
        other = tempfile.TemporaryDirectory(dir=self.root.parent)
        self.addCleanup(other.cleanup)
        slow_store = WorkspaceStore(Path(other.name))
        slow_ids = [slow_store.add_layer(name, collection(5 + index))["id"] for index, name in enumerate(("A", "B", "C"))]
        slow = slow_store.update_layer(slow_ids[1], changes)
        core._JSON1 = None

        def comparable(layer):
            # each call adds three more layers to this test's store, so position fields differ by design
            return {key: value for key, value in layer.items() if key not in ("id", "geojson", "order", "data_revision")}
        self.assertEqual(comparable(fast), comparable(slow))
        self.assertNotIn("geojson", fast)
        self.assertEqual(comparable(fast_row), comparable(slow))
        self.assertEqual(fast_row["geojson"], slow["geojson"], "feature bodies must survive a metadata edit untouched")
        self.assertEqual(list(fast_row), list(slow), "stored key order is unchanged")

    def test_visibility_name_and_style_edits_match_the_slow_path(self):
        self.assert_same_as_slow_path({"visible": False})
        self.assert_same_as_slow_path({"name": "Renamed é \"quoted\" \\ name"})
        self.assert_same_as_slow_path({"style": {"color": "#112233", "opacity": 0.5, "color_by": "kind",
                                                 "class_method": "unique", "label_field": "name", "line_width": 3}})
        self.assert_same_as_slow_path({"name": "N", "visible": False, "style": {"color_by": None}})

    def test_numeric_scale_still_derives_breaks_from_features(self):
        store, ids = self.filled()
        updated = store.update_layer(ids[2], {"style": {"color_by": "value", "color_scale": "numeric"}}, include_data=False)
        self.assertEqual(updated["style"]["range"], [0.0, 6.0])
        self.assertTrue(updated["style"]["breaks"])
        # leaving the numeric scale drops the derived keys again, via the fast path
        cleared = store.update_layer(ids[2], {"style": {"color_scale": "categorical"}}, include_data=False)
        self.assertNotIn("breaks", cleared["style"])
        self.assertNotIn("color_scale", cleared["style"])

    def test_fast_update_rejects_what_the_slow_path_rejects(self):
        store, ids = self.filled()
        for changes, message in (({"style": {"color": "red"}}, "Color must be #RRGGBB"),
                                 ({"style": {"color_by": "missing"}}, "color_by must name an existing property or be null"),
                                 ({"style": {"color_scale": "numeric"}}, "A numeric color scale needs color_by")):
            with self.assertRaisesRegex(ValueError, message):
                store.update_layer(ids[0], changes, include_data=False)
        with self.assertRaisesRegex(ValueError, "Unknown layer ID"):
            store.update_layer("nope", {"visible": False}, include_data=False)
        with self.assertRaisesRegex(ValueError, "Layer ID must be a string"):
            store.update_layer(7, {"visible": False}, include_data=False)
        self.assertEqual(store.summary()["revision"], 3, "a rejected edit must not bump the revision")

    def test_reorder_remove_and_new_layer_order(self):
        store, ids = self.filled()
        bodies = {layer_id: self.raw(layer_id)["geojson"] for layer_id in ids}
        order = [ids[2], ids[0], ids[1]]
        result = store.reorder(order)
        self.assertEqual([layer["id"] for layer in result], order)
        self.assertEqual([layer["order"] for layer in result], [0, 1, 2])
        self.assertTrue(all("geojson" not in layer for layer in result))
        self.assertEqual([layer["id"] for layer in store.summary()["layers"]], order)
        self.assertEqual({layer_id: self.raw(layer_id)["geojson"] for layer_id in ids}, bodies)
        removed = store.remove_layer(ids[0])
        self.assertEqual(removed, {"removed": ids[0], "name": "A", "feature_count": 5})
        with self.assertRaisesRegex(ValueError, "Unknown layer ID"):
            store.remove_layer(ids[0])
        self.assertEqual(store.add_layer("D", collection(2))["order"], 3)

    def test_add_layer_returns_exactly_what_it_stored(self):
        store = WorkspaceStore(self.root)
        source = {"kind": "geojson", "note": "é \"q\"", "nested": {"values": [1, 2.5, None, True]}}
        added = store.add_layer("Näme \"x\"", collection(4), source=source)
        self.assertEqual(added, self.raw(added["id"]))
        self.assertEqual(list(added), list(self.raw(added["id"])), "returned and stored key order match")
        self.assertEqual(list(added)[:3], ["id", "name", "geojson"])
        self.assertIsNot(added["source"], source)
        self.assertEqual(store.state()["layers"][0], added)
        with self.assertRaisesRegex(ValueError, "Only JSON data is accepted"):
            store.add_layer("bad source", collection(1), source={"when": object()})
        self.assertEqual(store.summary()["layer_count"] if "layer_count" in store.summary() else len(store.summary()["layers"]), 1)

    def test_a_saved_camera_spends_the_frame_request(self):
        store, _ = self.filled()
        request = store.frame([-102.4, 31.6, -101.1, 32.6])
        self.assertEqual(store.summary()["viewport_request"], request)
        store.save_name("still pending")
        self.assertEqual(store.summary()["viewport_request"], request, "only a camera write spends it")
        store.set_viewport([-101.7, 32.1], 8.5)
        self.assertNotIn("viewport_request", store.summary())
        self.assertEqual(store.summary()["viewport"], {"center": [-101.7, 32.1], "zoom": 8.5})

    def test_everything_still_works_without_sqlite_json(self):
        core._JSON1 = False
        store, ids = self.filled()
        self.assertEqual([layer["order"] for layer in store.summary()["layers"]], [0, 1, 2])
        self.assertFalse(store.update_layer(ids[0], {"visible": False}, include_data=False)["visible"])
        self.assertEqual([layer["id"] for layer in store.reorder(ids[::-1])], ids[::-1])
        self.assertEqual(store.remove_layer(ids[1])["name"], "B")
        self.assertEqual(store.add_layer("D", collection(2))["order"], 3)


class EncoderTests(unittest.TestCase):
    def test_encoder_output_and_rejections_are_unchanged(self):
        value = {"a": [1, 2.5, "é", None, True, {"b": [[0.1, -0.2], [1e-7, 3]]}]}
        self.assertEqual(core._json(value), '{"a":[1,2.5,"é",null,true,{"b":[[0.1,-0.2],[1e-07,3]]}]}')
        for bad, message in (({"a": float("nan")}, "JSON numbers must be finite"),
                             ({"a": [float("inf")]}, "JSON numbers must be finite"),
                             ({1: "x"}, "JSON object keys must be strings"),
                             ({"a": (1, 2)}, "Only JSON data is accepted"),
                             ({"a": [object()]}, "Only JSON data is accepted")):
            with self.assertRaisesRegex(ValueError, message):
                core._json(bad)
        cycle = []
        cycle.append(cycle)
        with self.assertRaisesRegex(ValueError, "JSON cannot contain cycles"):
            core._json(cycle)

        def nested(levels, leaf):
            value = leaf
            for _ in range(levels):
                value = [value]
            return value
        core._json(nested(64, []))  # an empty container at depth 64 is allowed
        with self.assertRaisesRegex(ValueError, "JSON nesting exceeds 64 levels"):
            core._json(nested(65, []))
        with self.assertRaisesRegex(ValueError, "JSON nesting exceeds 64 levels"):
            core._json(nested(65, 1))  # a scalar one level too deep is rejected, as before


if __name__ == "__main__":
    unittest.main()
