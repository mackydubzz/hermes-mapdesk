"""Persistent, process-independent workspace state for MapDesk."""
from contextlib import contextmanager
import json
import math
import re
from pathlib import Path
from pyproj import Geod
import sqlite3
import uuid

import numpy as np
import shapely
from shapely.geometry import box, shape
from shapely.errors import ShapelyError
from shapely.ops import unary_union
from shapely.validation import explain_validity


MAX_FEATURES = 5000
MAX_POSITIONS = 100000
MAX_JSON_BYTES = 10000000
MAX_JSON_DEPTH = 64
NUMERIC_CLASSES = 5
CIRCLE_VERTICES = 64
RECLAIM_BYTES = 4_000_000
SCHEMA_VERSION = 1  # user_version: 1 tables ready, 2/3 write-ahead log on/unavailable, 12/13 the same with split layer storage

_JSON1 = None


def _json1(connection):
    """True when this SQLite build has the JSON functions (standard since 3.38).

    They let metadata reads and small edits skip the feature bodies: a layer
    row is one JSON document and parsing megabytes of coordinates in Python
    to read a name or flip a flag was the cost of every status call."""
    global _JSON1
    if _JSON1 is None:
        try:
            connection.execute("SELECT json_remove('{\"a\":1}','$.a')").fetchone()
            _JSON1 = True
        except sqlite3.Error:
            _JSON1 = False
    return _JSON1

_GEOD = Geod(ellps="WGS84")

QUERY_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "isnull")
CLASS_METHODS = ("auto", "unique", "quantile", "jenks", "equal", "continuous")
PALETTES = ("classic", "okabe", "viridis", "sunset")


def _json(value):
    """Serialize only ordinary JSON data; never coerce keys or objects."""
    active = set()

    def visit(item, depth=0):
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds 64 levels")
        kind = type(item)
        if kind in (dict, list):
            identity = id(item)
            if identity in active:
                raise ValueError("JSON cannot contain cycles")
            active.add(identity)
            if kind is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError("JSON object keys must be strings")
                values = item.values()
            else:
                values = item
            deep = depth + 1 > MAX_JSON_DEPTH
            for child in values:
                if deep:
                    raise ValueError("JSON nesting exceeds 64 levels")
                child_kind = type(child)
                if child_kind is float:  # scalars checked inline: one call per container, not per number
                    if not math.isfinite(child):
                        raise ValueError("JSON numbers must be finite")
                elif child is None or child_kind is str or child_kind is int or child_kind is bool:
                    continue
                elif (child_kind is list and depth + 2 <= MAX_JSON_DEPTH and 2 <= len(child) <= 3
                      and type(child[0]) is float and type(child[1]) is float
                      and child[0] - child[0] == 0.0 and child[1] - child[1] == 0.0
                      and (len(child) == 2 or (type(child[2]) is float and child[2] - child[2] == 0.0))):
                    continue  # a coordinate position: finite floats (x - x is 0.0 only for a finite x), nothing to recurse into
                else:
                    visit(child, depth + 1)
            active.remove(identity)
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
        elif item is not None and kind not in (str, int, bool):
            raise ValueError("Only JSON data is accepted")

    try:
        visit(value)
        # One-shot dumps uses the C encoder (iterencode is pure Python, ~5x slower); same output.
        text = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        if len(text) > MAX_JSON_BYTES or len(text.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("JSON exceeds 10000000 UTF-8 bytes")
        return text
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Invalid JSON data") from exc


def _name(value):
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise ValueError("Name must be a nonblank string of at most 256 characters")
    return value


def _style(value, fields):
    if type(value) is not dict or not set(value) <= {"color", "opacity", "color_by", "color_scale",
                                                    "class_method", "class_count", "palette",
                                                    "label_field", "line_width", "circle_radius"}:
        raise ValueError("Style only accepts color, opacity, color_by, color_scale, class_method, "
                         "class_count, palette, label_field, line_width, circle_radius")
    if "color_scale" in value and value["color_scale"] not in ("categorical", "numeric"):
        raise ValueError("color_scale must be categorical or numeric")
    if "color" in value and (type(value["color"]) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}", value["color"])):
        raise ValueError("Color must be #RRGGBB")
    if "opacity" in value and (type(value["opacity"]) not in (int, float) or not 0 <= value["opacity"] <= 1):
        raise ValueError("Opacity must be a finite number between 0 and 1")
    if "color_by" in value and value["color_by"] is not None:
        if type(value["color_by"]) is not str or value["color_by"] not in fields:
            raise ValueError("color_by must name an existing property or be null")
    if "class_method" in value and value["class_method"] not in CLASS_METHODS:
        raise ValueError("class_method must be auto, unique, quantile, jenks, equal or continuous")
    if "class_count" in value and (type(value["class_count"]) is not int or not 3 <= value["class_count"] <= 7):
        raise ValueError("class_count must be an integer from 3 to 7")
    if "palette" in value and value["palette"] not in PALETTES:
        raise ValueError("palette must be classic, okabe, viridis or sunset")
    if "label_field" in value and value["label_field"] is not None and type(value["label_field"]) is not str:
        raise ValueError("label_field must name a property or be null")
    if "line_width" in value and (type(value["line_width"]) not in (int, float) or not 0.5 <= value["line_width"] <= 8):
        raise ValueError("line_width must be a number from 0.5 to 8")
    if "circle_radius" in value and (type(value["circle_radius"]) not in (int, float) or not 2 <= value["circle_radius"] <= 15):
        raise ValueError("circle_radius must be a number from 2 to 15")


def _number(value):
    """A finite number from a JSON number or numeric text (CSV values stay text)."""
    if type(value) in (int, float):
        return float(value) if math.isfinite(value) else None
    if type(value) is str and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _text(value):
    """Comparable text for a JSON scalar; None stays None, containers are not comparable."""
    if value is None:
        return None
    if type(value) is str:
        return value
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return json.dumps(value)
    return None


def _scalar_match(actual, op, value):
    """Numeric compare when both sides coerce, else text compare (gt/gte/lt/lte fall back)."""
    left, right = _number(actual), _number(value)
    if op in ("eq", "ne"):
        if left is not None and right is not None:
            equal = left == right
        else:
            equal = _text(actual) == _text(value)
        return equal if op == "eq" else not equal
    if left is not None and right is not None:
        first, second = left, right
    else:
        first, second = _text(actual), _text(value)
        if first is None or second is None:
            return False
    if op == "gt":
        return first > second
    if op == "gte":
        return first >= second
    if op == "lt":
        return first < second
    return first <= second


def _condition_match(properties, condition):
    actual = properties.get(condition["field"])
    op, value = condition["op"], condition["value"]
    if op == "isnull":
        return actual is None
    if op == "in":
        return any(_scalar_match(actual, "eq", entry) for entry in value)
    if op == "like":
        text = _text(actual)
        return text is not None and value.lower() in text.lower()
    return _scalar_match(actual, op, value)


def _condition(condition, fields):
    if not isinstance(condition, dict) or set(condition) != {"field", "op", "value"}:
        raise ValueError("A query condition must contain exactly field, op and value")
    if type(condition["field"]) is not str or condition["field"] not in fields:
        raise ValueError("Unknown query field")
    if condition["op"] not in QUERY_OPS:
        raise ValueError("Query op must be eq, ne, gt, gte, lt, lte, in, like or isnull")
    value = condition["value"]
    if condition["op"] == "isnull":
        if value is not None:
            raise ValueError("isnull requires a null value")
    elif condition["op"] == "in":
        if not isinstance(value, list):
            raise ValueError("in requires an array value")
    elif condition["op"] == "like":
        if type(value) is not str:
            raise ValueError("like requires a string value")
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError("Query value must be a string, number, boolean or null")
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("Query values must be finite")
    return condition


def _numeric_scale(style, features):
    """Derive quantile class breaks for a numeric ramp; a plain style carries none."""
    if style.get("color_scale") != "numeric" or style.get("color_by") is None:
        for key in ("color_scale", "breaks", "range"):
            style.pop(key, None)
        return
    values = sorted(number for number in (_number((item.get("properties") or {}).get(style["color_by"]))
                                          for item in features) if number is not None)
    if not values:
        raise ValueError("A numeric color scale needs a field with numeric values")
    breaks = []
    for step in range(1, NUMERIC_CLASSES):
        candidate = values[min(len(values) - 1, len(values) * step // NUMERIC_CLASSES)]
        if candidate > values[0] and (not breaks or candidate > breaks[-1]):
            breaks.append(candidate)
    style["breaks"], style["range"] = breaks, [values[0], values[-1]]


def _position(value, budget=None):
    if budget is not None:
        budget[0] += 1
        if budget[0] > MAX_POSITIONS:
            raise ValueError("GeoJSON exceeds 100000 coordinate positions")
    if not isinstance(value, list) or len(value) not in (2, 3):
        raise ValueError("A coordinate position must contain 2 or 3 numbers")
    try:
        finite = all(type(number) in (int, float) and math.isfinite(number) for number in value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("Coordinates must be finite numbers")
    if not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90:
        raise ValueError("Coordinates must be WGS84 longitude/latitude")


def _positions(coords, budget):
    """_position for each entry, in order, with the ordinary case checked inline.

    A two-float position inside the WGS84 range needs none of the other
    checks (a NaN or infinity fails the range test and takes the full path,
    which reports it exactly as before)."""
    for position in coords:
        if type(position) is list and len(position) == 2:
            x, y = position
            if type(x) is float and type(y) is float and -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0:
                budget[0] += 1
                if budget[0] > MAX_POSITIONS:
                    raise ValueError("GeoJSON exceeds 100000 coordinate positions")
                continue
        _position(position, budget)


def _geometry(value, budget):
    if not isinstance(value, dict):
        raise ValueError("Feature geometry must be a GeoJSON geometry object")
    if "crs" in value:
        raise ValueError("CRS declarations are not supported; supply WGS84 GeoJSON")
    kind = value.get("type")
    coords = value.get("coordinates")
    if kind == "Point":
        _position(coords, budget)
    elif kind == "LineString":
        if not isinstance(coords, list) or len(coords) < 2:
            raise ValueError("LineString needs at least two positions")
        _positions(coords, budget)
    elif kind == "Polygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError("Polygon needs rings")
        for ring in coords:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Polygon rings must be closed with at least four positions")
            _positions(ring, budget)
    elif kind in ("MultiPoint", "MultiLineString", "MultiPolygon"):
        if not isinstance(coords, list) or not coords:
            raise ValueError("Multi-geometries cannot be empty")
        for part in coords:
            _geometry({"type": kind[5:], "coordinates": part}, budget)
    elif kind == "GeometryCollection":
        geometries = value.get("geometries")
        if not isinstance(geometries, list) or not geometries:
            raise ValueError("GeometryCollection needs geometries")
        for geometry in geometries:
            _geometry(geometry, budget)
    else:
        raise ValueError("Unsupported GeoJSON geometry type")
    try:
        geometry = shape(value)
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("Invalid geometry: " + explain_validity(geometry))
    except (ShapelyError, TypeError, KeyError) as exc:
        raise ValueError("Invalid GeoJSON geometry") from exc


def _geojson(value):
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise ValueError("Expected a GeoJSON FeatureCollection")
    if "crs" in value:
        raise ValueError("CRS declarations are not supported; supply WGS84 GeoJSON")
    features = value.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("FeatureCollection must contain at least one feature")
    if len(features) > MAX_FEATURES:
        raise ValueError("GeoJSON exceeds 5000 features")
    budget = [0]
    for item in features:
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise ValueError("Expected GeoJSON Feature objects")
        if not isinstance(item.get("properties"), dict) and item.get("properties") is not None:
            raise ValueError("Feature properties must be an object or null")
        if "crs" in item:
            raise ValueError("CRS declarations are not supported; supply WGS84 GeoJSON")
        _geometry(item.get("geometry"), budget)


def _id_key(value):
    if type(value) not in (str, int, float):
        raise ValueError("Feature IDs must be strings or finite numbers")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("Feature IDs must be strings or finite numbers")
    return (isinstance(value, str), value)


def _assign_ids(geojson):
    features = geojson["features"]
    reserved = {_id_key(item["id"]) for item in features if "id" in item}
    used = set()
    for index, item in enumerate(features):
        if "id" not in item or _id_key(item["id"]) in used:
            candidate = f"mapdesk:{index + 1}"
            while _id_key(candidate) in reserved or _id_key(candidate) in used:
                candidate += ":generated"
            item["id"] = candidate
        used.add(_id_key(item["id"]))


def _circle(center, meters):
    """A geodesic 64-gon around center; the only radius geometry MapDesk builds itself."""
    ring = []
    for index in range(CIRCLE_VERTICES):
        longitude, latitude, _ = _GEOD.fwd(center[0], center[1], 360.0 * index / CIRCLE_VERTICES, meters)
        if not (math.isfinite(longitude) and math.isfinite(latitude)):
            raise ValueError("Radius circle is not representable at this center and distance")
        ring.append([longitude, max(-90.0, min(90.0, latitude))])
    ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def _spatial_filter(value):
    if not isinstance(value, dict) or set(value) != {"op", "layer_id"}:
        raise ValueError('spatial must be {"op": "intersects"|"within"|"contains", "layer_id": target}')
    if value["op"] not in ("intersects", "within", "contains"):
        raise ValueError('spatial op must be "intersects", "within" or "contains"')
    if type(value["layer_id"]) is not str:
        raise ValueError("spatial layer_id must be a string")
    return {"op": value["op"], "layer_id": value["layer_id"]}


def _geometry_union(layer):
    try:
        return unary_union([shape(item["geometry"]) for item in layer["geojson"]["features"]])
    except (ShapelyError, TypeError, KeyError) as exc:
        raise ValueError("Target layer geometry could not be combined") from exc


def _shapes(features):
    """Shapely geometries for stored features, as one array (points are built in one call)."""
    geometries = [item["geometry"] for item in features]
    kinds = {item.get("type") if type(item) is dict else None for item in geometries}
    try:
        if kinds == {"Point"}:
            return shapely.points([item["coordinates"][:2] for item in geometries])
        if kinds == {"LineString"}:
            flat = [position[:2] for item in geometries for position in item["coordinates"]]
            owner = np.repeat(np.arange(len(geometries)), [len(item["coordinates"]) for item in geometries])
            return shapely.linestrings(flat, indices=owner)
        if kinds == {"Polygon"} and all(len(item["coordinates"]) == 1 for item in geometries):
            flat = [position[:2] for item in geometries for position in item["coordinates"][0]]
            owner = np.repeat(np.arange(len(geometries)), [len(item["coordinates"][0]) for item in geometries])
            return shapely.polygons(shapely.linearrings(flat, indices=owner))
    except (ValueError, TypeError, KeyError, IndexError, ShapelyError):
        pass  # anything irregular takes the per-feature path below
    shapes = np.empty(len(geometries), dtype=object)
    for index, item in enumerate(geometries):
        shapes[index] = shape(item)
    return shapes


def _intersecting(features, geometry):
    """Features intersecting one geometry (bbox and radius selection), tested as a batch."""
    if not features:
        return []
    shapely.prepare(geometry)
    hits = shapely.intersects(geometry, _shapes(features))
    return [item for item, hit in zip(features, hits) if hit]


def _spatial_matches(features, op, target_layer):
    """Features whose geometry is `op` the union of the target layer's features.

    Same answers as testing every feature against unary_union(target), which
    is what this replaced. An index over the target features answers
    intersects without building the union at all, and for within/contains it
    rules out or settles most features so the union is only built, once and
    prepared, for the ones that need it."""
    if not features:
        return []
    geometries = _shapes(features)
    try:
        targets = _shapes(target_layer["geojson"]["features"])
        tree = shapely.STRtree(targets)
    except (ShapelyError, TypeError, KeyError) as exc:
        raise ValueError("Target layer geometry could not be combined") from exc
    union = []

    def combined():
        if not union:
            try:
                merged = unary_union(list(targets))
            except (ShapelyError, TypeError) as exc:
                raise ValueError("Target layer geometry could not be combined") from exc
            shapely.prepare(merged)
            union.append(merged)
        return union[0]

    matched = np.zeros(len(features), dtype=bool)
    touching = np.unique(tree.query(geometries, predicate="intersects")[0])
    if op == "intersects":
        matched[touching] = True
    elif op == "within":
        # Inside one target feature means inside the union; a feature that
        # touches the target but sits in no single feature may still span
        # several, so those are checked against the union itself.
        if len(touching):
            inside_one = np.unique(tree.query(geometries[touching], predicate="within")[0])
            matched[touching[inside_one]] = True
            rest = np.setdiff1d(touching, touching[inside_one])
            if len(rest):
                matched[rest] = shapely.contains(combined(), geometries[rest])
    else:  # contains: the feature must cover the whole target, so its bounds must cover the target's bounds
        bounds = shapely.bounds(targets)
        if not np.isnan(bounds).all():
            low_x, low_y = np.nanmin(bounds[:, 0]), np.nanmin(bounds[:, 1])
            high_x, high_y = np.nanmax(bounds[:, 2]), np.nanmax(bounds[:, 3])
            own = shapely.bounds(geometries)
            candidates = np.nonzero((own[:, 0] <= low_x) & (own[:, 1] <= low_y)
                                    & (own[:, 2] >= high_x) & (own[:, 3] >= high_y))[0]
            if len(candidates):
                matched[candidates] = shapely.contains(geometries[candidates], combined())
    return [item for item, hit in zip(features, matched) if hit]


def _assemble(data, meta):
    """A stored row as the layer dict: id, name, geojson, then the other fields."""
    if meta is None:
        return json.loads(data)  # a row from before the split holds the whole layer in one document
    rest = json.loads(meta)
    layer = {"id": rest.pop("id"), "name": rest.pop("name"), "geojson": json.loads(data)}
    layer.update(rest)
    return layer


class WorkspaceStore:
    """Each operation opens and closes its own SQLite connection.

    A layer is stored in two parts: `layers.data` holds the feature
    collection (megabytes) and `layer_meta.meta` holds everything else (a few
    hundred bytes: name, style, visibility, order, source). Status reads and
    name/visibility/style/order edits touch only the small part. A `layers`
    row with no `layer_meta` row is a whole-layer document from before the
    split; readers accept it and writers split it first."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workspace.sqlite3"
        # A store is opened for every command. Setup and the legacy-row scan
        # (which parses every layer) run once per database, recorded in
        # user_version, instead of inside a write transaction on every call.
        version = self._schema_version()
        if version in (12, 13):
            self._wal = version == 12
            return
        self._wal = version == 2
        if version == 0:
            with self._connection(write=True) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS layers (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
                connection.execute("INSERT OR IGNORE INTO workspace VALUES (1, ?)", (json.dumps({
                    "revision": 0, "name": "Untitled map", "viewport": {"center": [0, 0], "zoom": 2},
                    "selection": {"layer_id": None, "feature_ids": [], "bbox": None},
                }),))
                self._migrate_legacy_layers(connection)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            version = 1
        if version == 1:
            self._enable_wal()
        with self._connection(write=True) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS layer_meta (id TEXT PRIMARY KEY, meta TEXT NOT NULL)")
            self._split_rows(connection)
            connection.execute(f"PRAGMA user_version={12 if self._wal else 13}")

    @staticmethod
    def _split_rows(connection, layer_id=None):
        """Move whole-layer documents (rows with no layer_meta entry) into the two-part form."""
        query = "SELECT l.id FROM layers l WHERE NOT EXISTS (SELECT 1 FROM layer_meta m WHERE m.id=l.id)"
        ids = [row[0] for row in (connection.execute(query + " AND l.id=?", (layer_id,)) if layer_id is not None
                                  else connection.execute(query))]
        for item in ids:
            if _json1(connection):
                try:  # SQLite splits the document without Python parsing the coordinates
                    done = connection.execute(
                        "INSERT INTO layer_meta (id, meta) SELECT id, json_remove(data,'$.geojson') FROM layers "
                        "WHERE id=? AND json_type(data,'$.geojson')='object'", (item,)).rowcount
                    if done:
                        connection.execute("UPDATE layers SET data=json_extract(data,'$.geojson') WHERE id=?", (item,))
                    continue
                except sqlite3.Error:
                    connection.execute("DELETE FROM layer_meta WHERE id=?", (item,))
            layer = json.loads(connection.execute("SELECT data FROM layers WHERE id=?", (item,)).fetchone()[0])
            if type(layer) is not dict or type(layer.get("geojson")) is not dict:
                continue  # not a layer document this store understands; leave it untouched
            body = json.dumps(layer.pop("geojson"), allow_nan=False, ensure_ascii=False, separators=(",", ":"))
            connection.execute("INSERT INTO layer_meta (id, meta) VALUES (?, ?)", (item, _json(layer)))
            connection.execute("UPDATE layers SET data=? WHERE id=?", (body, item))

    @staticmethod
    def _meta(connection, layer_id):
        """A layer without its features; reads only the small part of a split row."""
        if not isinstance(layer_id, str):
            raise ValueError("Layer ID must be a string")
        row = connection.execute("SELECT meta FROM layer_meta WHERE id=?", (layer_id,)).fetchone()
        if row is not None:
            return json.loads(row[0])
        row = connection.execute("SELECT data FROM layers WHERE id=?", (layer_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown layer ID")
        layer = json.loads(row[0])
        layer.pop("geojson", None)
        return layer

    def _enable_wal(self):
        """Switch the database to a write-ahead log, once.

        The pane polls while the agent and the map write. With the rollback
        journal every reader blocks every writer and each small write pays
        two fsyncs; with the log, reads never block writes. Filesystems that
        cannot do it (some network shares) keep the journal and are recorded
        as such so the switch is not retried on every command."""
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self._wal = str(mode).lower() == "wal"
            connection.execute(f"PRAGMA user_version={2 if self._wal else 3}")
        except sqlite3.Error:
            self._wal = False  # busy or read-only right now: stay on the journal, try again on a later open
        finally:
            connection.close()

    def _reclaim(self):
        """Hand emptied pages back to the filesystem once they are most of the file.

        SQLite keeps deleted pages for reuse, so a workspace that once held
        large layers stayed that size on disk forever (18 MB with no layers)."""
        try:
            connection = sqlite3.connect(self.path, timeout=1, isolation_level=None)
            try:
                free = connection.execute("PRAGMA freelist_count").fetchone()[0]
                total = connection.execute("PRAGMA page_count").fetchone()[0]
                size = connection.execute("PRAGMA page_size").fetchone()[0]
                if free * size >= RECLAIM_BYTES and free * 2 >= total:
                    connection.execute("VACUUM")
            finally:
                connection.close()
        except sqlite3.Error:
            pass  # busy right now; the next removal tries again

    def _schema_version(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            return connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()

    def _migrate_legacy_layers(self, connection):
        """Heal 0.1-era layer rows: assign missing order/data_revision so the
        pane (which fetches geojson only for a finite data_revision) still
        draws workspaces created before the 0.2 upgrade."""
        rows = connection.execute("SELECT id, data, rowid FROM layers").fetchall()
        dirty = False
        for layer_id, raw, rowid in rows:
            layer = json.loads(raw)
            changed = False
            if type(layer.get("order")) is not int or not 0 <= layer["order"] <= 10_000_000:
                layer["order"] = rowid - 1
                changed = True
            if type(layer.get("data_revision")) is not int or layer["data_revision"] < 1:
                layer["data_revision"] = self._metadata(connection).get("revision", 0) + 1
                changed = True
            if changed:
                connection.execute("UPDATE layers SET data=? WHERE id=?", (_json(layer), layer_id))
                dirty = True
        if dirty:
            self._save_metadata(connection, self._metadata(connection))

    @contextmanager
    def _connection(self, write=False):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            if self._wal:  # safe with the write-ahead log: a crash can lose the last write, never corrupt the file
                connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _metadata(connection):
        return json.loads(connection.execute("SELECT data FROM workspace WHERE id=1").fetchone()[0])

    @staticmethod
    def _save_metadata(connection, data):
        data["revision"] += 1
        connection.execute("UPDATE workspace SET data=? WHERE id=1", (json.dumps(data),))

    def state(self, include_data=True):
        with self._connection() as connection:
            result = self._metadata(connection)
            # The feature column is read only when asked for (or for a row that has no separate metadata).
            rows = connection.execute(
                "SELECT m.meta, CASE WHEN m.meta IS NULL OR ? THEN l.data END FROM layers l "
                "LEFT JOIN layer_meta m ON m.id=l.id ORDER BY l.rowid", (1 if include_data else 0,)).fetchall()
        # Parse after the read transaction closes so a slow parse never holds the lock.
        layers = []
        for meta, data in rows:
            if meta is not None and not include_data:
                layers.append(json.loads(meta))
                continue
            layer = _assemble(data, meta)
            if not include_data:
                layer.pop("geojson")
            layers.append(layer)
        layers.sort(key=lambda layer: layer.get("order", 0))
        result["layers"] = layers
        return result

    def summary(self):
        """Workspace metadata, selection and layers without stored geojson bodies."""
        return self.state(include_data=False)

    def layer(self, layer_id):
        with self._connection() as connection:
            return self._layer(connection, layer_id)

    @staticmethod
    def _layer(connection, layer_id):
        if not isinstance(layer_id, str):
            raise ValueError("Layer ID must be a string")
        row = connection.execute("SELECT l.data, m.meta FROM layers l LEFT JOIN layer_meta m ON m.id=l.id WHERE l.id=?",
                                 (layer_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown layer ID")
        return _assemble(*row)

    def layer_reply_text(self, layer_id):
        """The pane's layer reply ({layer_id, data_revision, geojson}) as JSON text.

        The stored feature text is passed through as is, so serving a layer
        body costs no parse and no re-encode of its coordinates."""
        if not isinstance(layer_id, str):
            raise ValueError("Layer ID must be a string")
        with self._connection() as connection:
            row = connection.execute("SELECT l.data, m.meta FROM layers l LEFT JOIN layer_meta m ON m.id=l.id WHERE l.id=?",
                                     (layer_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown layer ID")
        data, meta = row
        if meta is None:
            layer = json.loads(data)
            return json.dumps({"layer_id": layer["id"], "data_revision": layer.get("data_revision"),
                               "geojson": layer["geojson"]}, ensure_ascii=False, separators=(",", ":"))
        rest = json.loads(meta)
        head = json.dumps({"layer_id": rest["id"], "data_revision": rest.get("data_revision")},
                          ensure_ascii=False, separators=(",", ":"))
        return f'{head[:-1]},"geojson":{data}}}'

    def reorder(self, layer_ids):
        if not isinstance(layer_ids, list) or any(type(item) is not str for item in layer_ids):
            raise ValueError("Layer IDs must exactly match the workspace layers")
        with self._connection(write=True) as connection:
            self._split_rows(connection)
            metas = dict(connection.execute("SELECT id, meta FROM layer_meta").fetchall())
            known = [row[0] for row in connection.execute("SELECT id FROM layers")]
            if len(known) != len(layer_ids) or set(known) != set(layer_ids) or set(metas) != set(known):
                raise ValueError("Layer IDs must exactly match the workspace layers")
            ordered = []
            for index, layer_id in enumerate(layer_ids):
                # Only the order number changes, and it lives in the small metadata row.
                meta = json.loads(metas[layer_id])
                meta["order"] = index
                encoded = _json(meta)
                connection.execute("UPDATE layer_meta SET meta=? WHERE id=?", (encoded, layer_id))
                ordered.append(json.loads(encoded))
            self._save_metadata(connection, self._metadata(connection))
        return ordered

    def set_selection(self, layer_id, feature_ids=None, bbox=None, radius=None, spatial=None, invert=False,
                      detail=False):
        if feature_ids is not None and not isinstance(feature_ids, list):
            raise ValueError("feature_ids must be an array")
        if type(invert) is not bool:
            raise ValueError("invert must be a boolean")
        if sum(value is not None for value in (feature_ids, bbox, radius, spatial)) > 1:
            raise ValueError("Specify only one of feature_ids, bbox, radius or spatial")
        if invert and (feature_ids is None or layer_id is None):
            raise ValueError("invert requires a layer and feature_ids")
        if layer_id is None and (bbox is not None or radius is not None or spatial is not None or feature_ids):
            raise ValueError("A layer is required for a selection")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError("bbox must be [west, south, east, north]")
            _position(bbox[:2])
            _position(bbox[2:])
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                raise ValueError("bbox must have positive width/height and cannot wrap the antimeridian")
            bbox = list(bbox)
        if radius is not None:
            if not isinstance(radius, dict) or set(radius) != {"center", "meters"}:
                raise ValueError('radius must be {"center": [longitude, latitude], "meters": number}')
            center = radius["center"]
            if not isinstance(center, list) or len(center) != 2:
                raise ValueError("radius center must be [longitude, latitude]")
            _position(center)
            meters = radius["meters"]
            if type(meters) not in (int, float) or not math.isfinite(meters) or not 10 <= meters <= 5_000_000:
                raise ValueError("radius meters must be a number from 10 to 5000000")
            radius = {"center": [center[0], center[1]], "meters": meters}
        if spatial is not None:
            spatial = _spatial_filter(spatial)
        with self._connection(write=True) as connection:
            chosen = []
            if layer_id is not None:
                layer = self._layer(connection, layer_id)
                features = layer["geojson"]["features"]
                if spatial is not None:
                    chosen = _spatial_matches(features, spatial["op"], self._layer(connection, spatial["layer_id"]))
                elif radius is not None:
                    chosen = _intersecting(features, shape(_circle(radius["center"], radius["meters"])))
                elif bbox is not None:
                    chosen = _intersecting(features, box(*bbox))
                else:
                    requested = {_id_key(value) for value in (feature_ids or [])}
                    available = {_id_key(item["id"]) for item in features}
                    if not requested <= available:
                        raise ValueError("Unknown feature ID")
                    if invert:
                        chosen = [item for item in features if _id_key(item["id"]) not in requested]
                    else:
                        chosen = [item for item in features if _id_key(item["id"]) in requested]
            selected = {"layer_id": layer_id, "feature_ids": [item["id"] for item in chosen], "bbox": bbox}
            if radius is not None:
                selected["radius"] = radius
            data = self._metadata(connection)
            data["selection"] = selected
            self._save_metadata(connection, data)
        if detail:
            # What selection() would return, without reading and parsing the layer a second time.
            return {**json.loads(json.dumps(selected)), "features": chosen[:100], "total": len(chosen),
                    "complete": len(chosen) <= 100}
        return selected

    def selection(self):
        with self._connection() as connection:
            selected = self._metadata(connection)["selection"]
            if selected["layer_id"] is None:
                return {**selected, "features": [], "total": 0, "complete": True}
            layer = self._layer(connection, selected["layer_id"])
            keys = {_id_key(value) for value in selected["feature_ids"]}
            features = [item for item in layer["geojson"]["features"] if _id_key(item["id"]) in keys]
            return {**selected, "features": features[:100], "total": len(features), "complete": len(features) <= 100}

    def query(self, layer_id, where=None, spatial=None, limit=200):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Query limit must be an integer from 1 to 1000")
        if where is None:
            where = []
        if not isinstance(where, list):
            raise ValueError("where must be an array of conditions")
        if spatial is not None:
            spatial = _spatial_filter(spatial)
        with self._connection() as connection:
            layer = self._layer(connection, layer_id)
            fields = set(layer["fields"])
            for condition in where:
                _condition(condition, fields)
            features = layer["geojson"]["features"]
            if spatial is not None:
                features = _spatial_matches(features, spatial["op"], self._layer(connection, spatial["layer_id"]))
            ids = [item["id"] for item in features
                   if all(_condition_match(item.get("properties") or {}, condition) for condition in where)]
        return {"layer_id": layer_id, "total": len(ids), "feature_ids": ids[:limit],
                "complete": len(ids) <= limit}

    def save_name(self, name):
        _name(name)
        with self._connection(write=True) as connection:
            data = self._metadata(connection)
            data["name"] = name
            self._save_metadata(connection, data)
        return name

    def set_viewport(self, center, zoom):
        if type(center) is not list or len(center) != 2:
            raise ValueError("Viewport center must be [longitude, latitude]")
        _position(center)
        if type(zoom) not in (int, float) or not 0 <= zoom <= 24:
            raise ValueError("Zoom must be a finite number between 0 and 24")
        viewport = {"center": list(center), "zoom": zoom}
        with self._connection(write=True) as connection:
            data = self._metadata(connection)
            data["viewport"] = viewport
            # A camera saved after a frame request supersedes it. The pane's
            # write-back after fitting lands here, so the request is spent;
            # left in place it replayed on every pane open and threw away the
            # camera the user had moved to.
            data.pop("viewport_request", None)
            self._save_metadata(connection, data)
        return viewport

    def frame(self, bbox):
        """Register a bbox the pane should fit the camera to (its pixel size
        decides the zoom). The pane applies each request_id once and the
        camera it saves afterwards clears the request (see set_viewport), so a
        frame does not fight later user panning or replay on the next open."""
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox)
                or not -180 <= bbox[0] < bbox[2] <= 180
                or not -90 <= bbox[1] < bbox[3] <= 90):
            raise ValueError("bbox must be [west, south, east, north] in WGS84 with positive area")
        request = {"bbox": list(bbox), "request_id": str(uuid.uuid4())}
        with self._connection(write=True) as connection:
            data = self._metadata(connection)
            data["viewport_request"] = request
            self._save_metadata(connection, data)
        return request

    def update_layer(self, layer_id, changes, include_data=True):
        if type(changes) is not dict or not changes or not set(changes) <= {"name", "visible", "style"}:
            raise ValueError("Layer changes must contain only name, visible, style")
        if "name" in changes:
            _name(changes["name"])
        if "visible" in changes and type(changes["visible"]) is not bool:
            raise ValueError("visible must be a boolean")
        with self._connection(write=True) as connection:
            if not isinstance(layer_id, str):
                raise ValueError("Layer ID must be a string")
            self._split_rows(connection, layer_id)
            meta = self._meta(connection, layer_id)
            geojson = None

            def features():
                nonlocal geojson
                if geojson is None:
                    geojson = json.loads(connection.execute("SELECT data FROM layers WHERE id=?", (layer_id,)).fetchone()[0])
                return geojson["features"]

            for key, value in changes.items():
                if key == "style":
                    _style(value, meta["fields"])
                    merged = {**meta["style"], **value}
                    if value.get("color_scale") == "numeric" and merged["color_by"] is None:
                        raise ValueError("A numeric color scale needs color_by")
                    meta["style"].update(value)
                    # Only numeric class breaks are derived from the feature values; nothing else loads them.
                    numeric = meta["style"].get("color_scale") == "numeric" and meta["style"].get("color_by") is not None
                    _numeric_scale(meta["style"], features() if numeric else ())
                else:
                    meta[key] = value
            encoded = _json(meta)
            connection.execute("UPDATE layer_meta SET meta=? WHERE id=?", (encoded, layer_id))
            self._save_metadata(connection, self._metadata(connection))
            if include_data:
                features()
        updated = json.loads(encoded)
        if not include_data:
            return updated
        layer = {"id": updated.pop("id"), "name": updated.pop("name"), "geojson": geojson}
        layer.update(updated)
        return layer

    def remove_layer(self, layer_id):
        with self._connection(write=True) as connection:
            layer = self._meta(connection, layer_id)
            connection.execute("DELETE FROM layers WHERE id=?", (layer_id,))
            connection.execute("DELETE FROM layer_meta WHERE id=?", (layer_id,))
            data = self._metadata(connection)
            if data["selection"]["layer_id"] == layer_id:
                data["selection"] = {"layer_id": None, "feature_ids": [], "bbox": None}
            self._save_metadata(connection, data)
        self._reclaim()
        return {"removed": layer_id, "name": layer["name"], "feature_count": layer["feature_count"]}

    def add_layer(self, name, geojson, source=None):
        _name(name)
        if source is not None and type(source) is not dict:
            raise ValueError("source must be a JSON object")
        geojson = json.loads(_json(geojson))
        _geojson(geojson)
        _assign_ids(geojson)
        layer = {
            "id": str(uuid.uuid4()), "name": name, "geojson": geojson,
            "source": source if source is not None else {},
            "style": {"color": "#3388ff", "opacity": 0.8, "color_by": None},
            "visible": True, "feature_count": len(geojson["features"]),
            "fields": sorted({key for item in geojson["features"] for key in (item.get("properties") or {})}),
        }
        geojson_part = layer.pop("geojson")
        with self._connection(write=True) as connection:
            self._split_rows(connection)
            orders = [json.loads(row[0]).get("order", 0) for row in connection.execute("SELECT meta FROM layer_meta")]
            layer["order"] = max(orders, default=-1) + 1
            data = self._metadata(connection)
            layer["data_revision"] = data["revision"] + 1
            # `geojson_part` is this method's own copy, already proven to be
            # plain JSON by the round trip above: it is encoded once more by
            # the C encoder and stored beside the small metadata document.
            meta = _json(layer)
            body = json.dumps(geojson_part, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
            if len(meta.encode("utf-8")) + len(body.encode("utf-8")) + 11 > MAX_JSON_BYTES:
                raise ValueError("JSON exceeds 10000000 UTF-8 bytes")
            connection.execute("INSERT INTO layers VALUES (?, ?)", (layer["id"], body))
            connection.execute("INSERT INTO layer_meta (id, meta) VALUES (?, ?)", (layer["id"], meta))
            self._save_metadata(connection, data)
        rest = json.loads(meta)
        stored = {"id": rest.pop("id"), "name": rest.pop("name"), "geojson": geojson_part}
        stored.update(rest)
        return stored
