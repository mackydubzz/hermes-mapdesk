"""Pure geodesic geometry operations for MapDesk (shapely + pyproj only).

No store access, no I/O, no mutation of inputs. Every function accepts
already-validated WGS84 GeoJSON and raises a single clear ValueError for
bad input (wrong geometry types, empty collections, out-of-range numbers).
"""
import json
import math

from pyproj import Geod, Transformer
from shapely import STRtree, make_valid, transform as _apply_transform
from shapely.errors import ShapelyError
from shapely.geometry import GeometryCollection, MultiPoint, MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union


MAX_OUTPUT_FEATURES = 4000
MAX_OUTPUT_POSITIONS = 60000
MEASURE_FEATURE_LIMIT = 100
CIRCLE_VERTICES = 64

_GEOMETRY_TYPES = {"Point", "LineString", "Polygon", "MultiPoint",
                   "MultiLineString", "MultiPolygon", "GeometryCollection"}

GEOD = Geod(ellps="WGS84")


def _collection(geojson):
    """Validate a WGS84 FeatureCollection and return [(geometry, properties, id)]."""
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise ValueError("Expected a GeoJSON FeatureCollection")
    if "crs" in geojson:
        raise ValueError("CRS declarations are not supported; supply WGS84 GeoJSON")
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("FeatureCollection must contain at least one feature")
    prepared = []
    for item in features:
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise ValueError("Expected GeoJSON Feature objects")
        if "crs" in item:
            raise ValueError("CRS declarations are not supported; supply WGS84 GeoJSON")
        properties = item.get("properties")
        if properties is None:
            properties = {}
        if not isinstance(properties, dict):
            raise ValueError("Feature properties must be an object or null")
        geometry = item.get("geometry")
        if not isinstance(geometry, dict) or "type" not in geometry:
            raise ValueError("Feature geometry must be a GeoJSON geometry object")
        if geometry.get("type") not in _GEOMETRY_TYPES:
            raise ValueError("Unsupported GeoJSON geometry type")
        try:
            geom = shape(geometry)
        except (ShapelyError, TypeError, KeyError, ValueError) as exc:
            raise ValueError("Invalid GeoJSON geometry") from exc
        if geom.is_empty:
            raise ValueError("Features cannot have empty geometry")
        xmin, ymin, xmax, ymax = geom.bounds
        if not (-180 <= xmin and xmax <= 180 and -90 <= ymin and ymax <= 90):
            raise ValueError("Coordinates must be WGS84 longitude/latitude")
        prepared.append((geom, properties, item.get("id")))
    return prepared


def _flatten(geometry):
    """Atomic (Point/LineString/Polygon) parts of any geometry, including collections."""
    kind = geometry.geom_type
    if kind == "GeometryCollection":
        parts = []
        for part in geometry.geoms:
            parts.extend(_flatten(part))
        return parts
    if kind.startswith("Multi"):
        return list(geometry.geoms)
    return [geometry]


def _polygonal(geometry):
    """Keep only the polygonal parts of a geometry (dissolve unions can strand lines)."""
    parts = [part for part in _flatten(geometry) if part.geom_type == "Polygon" and not part.is_empty]
    if not parts:
        return Polygon()
    if len(parts) == 1:
        return parts[0]
    return MultiPolygon(parts)


def _vertices(geometry):
    """All coordinate vertices of a geometry, recursively."""
    kind = geometry.geom_type
    if kind == "Point":
        return [(geometry.x, geometry.y)]
    if kind == "LineString":
        return list(geometry.coords)
    if kind == "Polygon":
        coords = list(geometry.exterior.coords)
        for ring in geometry.interiors:
            coords.extend(ring.coords)
        return coords
    return [vertex for part in geometry.geoms for vertex in _vertices(part)]


def _listify(value):
    if isinstance(value, (list, tuple)):
        return [_listify(item) for item in value]
    if isinstance(value, dict):
        return {key: _listify(item) for key, item in value.items()}
    return value


def _sanitized(geometry):
    """Shapely geometry -> plain-list GeoJSON geometry clamped to WGS84, plus position count."""
    mapped = _listify(mapping(geometry))
    assert isinstance(mapped, dict)
    positions = [0]

    def walk(value):
        if (isinstance(value, list) and value
                and all(isinstance(number, (int, float)) and not isinstance(number, bool)
                        for number in value)):
            value[0] = min(180.0, max(-180.0, float(value[0])))
            value[1] = min(90.0, max(-90.0, float(value[1])))
            positions[0] += 1
        elif isinstance(value, list):
            for item in value:
                walk(item)

    if mapped.get("type") == "GeometryCollection":
        for sub in mapped["geometries"]:
            walk(sub["coordinates"])
    else:
        walk(mapped["coordinates"])
    return mapped, positions[0]


def _emit(results, total=None):
    """Turn [(geometry, properties, source_id)] into the shared output contract.

    Budgets: stop emitting once the output would exceed 4,000 features or
    60,000 coordinate positions; set partial=True and count every input
    result that produced no output feature in skipped. `results` may be a
    lazy iterable (pass `total`): nothing past the budget is then computed.
    """
    features = []
    positions = 0
    skipped = 0
    partial = False
    if total is None:
        total = len(results)
    for index, (geometry, properties, source_id) in enumerate(results):
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        if geometry.is_empty:
            skipped += 1
            continue
        sanitized, count = _sanitized(geometry)
        if len(features) >= MAX_OUTPUT_FEATURES or positions + count > MAX_OUTPUT_POSITIONS:
            partial = True
            skipped += total - index
            break
        emitted = dict(properties)
        emitted["_source_id"] = source_id if source_id is not None else len(features) + 1
        features.append({"type": "Feature", "id": len(features) + 1,
                          "properties": emitted, "geometry": sanitized})
        positions += count
    return {"geojson": {"type": "FeatureCollection", "features": features},
            "partial": partial, "skipped": skipped}


def _distance(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a number between {low} and {high} meters")
    return float(value)


def _field(name):
    if name is not None and (type(name) is not str or not name.strip()):
        raise ValueError("by_field must be a property name or None")


def _count_field(name):
    if type(name) is not str or not name.strip():
        raise ValueError("count_field must be a nonblank string")


def _hashable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True, default=repr)


def _groups(prepared, by_field):
    if by_field is None:
        return [prepared]
    grouped = {}
    for item in prepared:
        key = _hashable(item[1].get(by_field))
        grouped.setdefault(key, []).append(item)
    return list(grouped.values())


_TRANSFORMERS = {}


PROJECTION_GRID_DEGREES = 0.1


def _transformers(longitude, latitude):
    """Cached (forward, inverse) WGS84 -> local AEQD meter projection pair.

    The projection center is snapped to a 0.1 degree grid so neighbouring
    parts share one projection: building a pair costs about a millisecond and
    a layer of 5,000 points used to build 5,000 of them. With the center at
    most ~8 km from the part, the buffer edge stays within 0.1 mm of the
    true geodesic distance at 500 m and within about 2 cm at the 100 km
    maximum (measured); only the positions of the vertices along the edge
    shift slightly."""
    longitude = round(round(longitude / PROJECTION_GRID_DEGREES) * PROJECTION_GRID_DEGREES, 6)
    latitude = max(-90.0, min(90.0, round(round(latitude / PROJECTION_GRID_DEGREES) * PROJECTION_GRID_DEGREES, 6)))
    key = (longitude, latitude)
    cached = _TRANSFORMERS.get(key)
    if cached is None:
        # Geodesic buffering: project into a local azimuthal equidistant
        # (AEQD) CRS centered on the geometry's centroid. AEQD preserves true
        # surface distances from the center point in every direction, so a
        # buffer of N meters around the centroid is a geodesically accurate
        # circle; distortion grows only with the extent of the buffered
        # geometry away from its centroid, not with the buffer distance.
        local = f"+proj=aeqd +lat_0={latitude!r} +lon_0={longitude!r} +datum=WGS84 +units=m"
        cached = (Transformer.from_crs("EPSG:4326", local, always_xy=True),
                  Transformer.from_crs(local, "EPSG:4326", always_xy=True))
        if len(_TRANSFORMERS) >= 4096:
            _TRANSFORMERS.clear()
        _TRANSFORMERS[key] = cached
    return cached


def _reproject(transformer, geometry):
    def apply(coords):
        # shapely.transform hands the transformation one (n, 2|3) coordinate
        # array; pyproj's Transformer consumes the column vectors directly.
        longitude, latitude = transformer.transform(coords[:, 0], coords[:, 1])
        projected = coords.copy()
        projected[:, 0] = longitude
        projected[:, 1] = latitude
        return projected
    return _apply_transform(geometry, apply)


def _buffer_part(part, distance_m):
    longitude, latitude = part.centroid.x, part.centroid.y
    forward, inverse = _transformers(longitude, latitude)
    return _reproject(inverse, _reproject(forward, part).buffer(distance_m))


def _buffer_shape(geometry, distance_m):
    return unary_union([_buffer_part(part, distance_m) for part in _flatten(geometry)])


def buffer(geojson, distance_m, by_field=None):
    """Geodesic buffer in meters; by_field buffers each attribute group as one shape."""
    distance = _distance(distance_m, 1, 100000, "buffer distance")
    prepared = _collection(geojson)
    _field(by_field)
    groups = _groups(prepared, by_field) if by_field is not None else [[item] for item in prepared]

    def buffered():
        for group in groups:
            parts = [part for geom, _, _ in group for part in _flatten(geom)]
            merged = parts[0] if len(parts) == 1 and parts[0].geom_type == "Point" else unary_union(parts)
            first = group[0]
            yield _buffer_shape(merged, distance), first[1], first[2]

    # Lazy: the output budget usually stops long before the last feature, and
    # every buffer computed past it used to be thrown away.
    return _emit(buffered(), total=len(groups))


def dissolve(geojson, by_field=None):
    """Merge (union) features, optionally grouped by an attribute value."""
    prepared = _collection(geojson)
    _field(by_field)
    for geom, _, _ in prepared:
        if any(part.geom_type != "Polygon" for part in _flatten(geom)):
            raise ValueError("Dissolve accepts only polygon features")
    results = []
    for group in _groups(prepared, by_field):
        merged = _polygonal(unary_union([part for geom, _, _ in group for part in _flatten(geom)]))
        first = group[0]
        properties = dict(first[1])
        if by_field is not None:
            properties[by_field] = first[1].get(by_field)
        results.append((merged, properties, first[2]))
    return _emit(results)


def centroid(geojson):
    """Replace every feature with its centroid point."""
    prepared = _collection(geojson)
    return _emit([(geom.centroid, properties, feature_id)
                  for geom, properties, feature_id in prepared])


def hull(geojson):
    """One convex hull over all features."""
    prepared = _collection(geojson)
    points = [vertex for geom, _, _ in prepared for vertex in _vertices(geom)]
    hull = MultiPoint(points).convex_hull
    first = prepared[0]
    return _emit([(hull, first[1], first[2])])


def clip(geojson, overlay_geojson):
    """Intersect features with the union of the overlay polygons, keeping attributes."""
    prepared = _collection(geojson)
    overlay = _collection(overlay_geojson)
    parts = []
    for geom, _, _ in overlay:
        for part in _flatten(geom):
            if part.geom_type != "Polygon":
                raise ValueError("Clip overlay accepts only polygon features")
            parts.append(part)
    # Index the overlay instead of dissolving all of it up front: each feature
    # is cut by the union of only the overlay polygons it actually meets.
    tree = STRtree(parts)

    def clipped():
        for geom, properties, feature_id in prepared:
            hits = sorted(int(index) for index in tree.query(geom, predicate="intersects"))
            if not hits:
                yield GeometryCollection(), properties, feature_id
                continue
            mask = parts[hits[0]] if len(hits) == 1 else unary_union([parts[index] for index in hits])
            yield geom.intersection(mask), properties, feature_id

    return _emit(clipped(), total=len(prepared))


def count_points(polygons_geojson, points_geojson, count_field="point_count"):
    """Add a per-polygon count of intersecting point features as an attribute."""
    _count_field(count_field)
    prepared = _collection(polygons_geojson)
    points = _collection(points_geojson)
    flat_points = []
    for geom, _, _ in points:
        for part in _flatten(geom):
            if part.geom_type != "Point":
                raise ValueError("count_points accepts only point features")
            flat_points.append(part)
    for geom, _, _ in prepared:
        if any(part.geom_type != "Polygon" for part in _flatten(geom)):
            raise ValueError("count_points accepts only polygon features")
    tree = STRtree(flat_points)
    results = []
    for geom, properties, feature_id in prepared:
        counted = dict(properties)
        counted[count_field] = len(tree.query(geom, predicate="intersects"))
        results.append((geom, counted, feature_id))
    return _emit(results)


def measure(geojson):
    """Geodesic per-feature length (km) for lines and area (km2) for polygons."""
    prepared = _collection(geojson)
    entries = []
    total_length = 0.0
    total_area = 0.0
    for position, (geom, _, feature_id) in enumerate(prepared):
        parts = _flatten(geom)
        length = 0.0
        area = 0.0
        for part in parts:
            kind = part.geom_type
            if kind == "LineString":
                length += abs(GEOD.geometry_length(part))
            elif kind == "Polygon":
                area += abs(GEOD.geometry_area_perimeter(part)[0])
        entry = {"id": feature_id if feature_id is not None else position + 1}
        if any(part.geom_type == "LineString" for part in parts):
            entry["length_km"] = round(length / 1000.0, 6)
        if any(part.geom_type == "Polygon" for part in parts):
            entry["area_km2"] = round(area / 1e6, 6)
        entries.append(entry)
        total_length += length
        total_area += area
    return {"features": entries[:MEASURE_FEATURE_LIMIT],
            "total_length_km": round(total_length / 1000.0, 6),
            "total_area_km2": round(total_area / 1e6, 6)}


def _normalize_longitude(value):
    return ((value + 180.0) % 360.0) - 180.0


def circle(center_lng_lat, meters):
    """A closed 64-gon geodesic circle around [longitude, latitude] as a Polygon dict."""
    if not isinstance(center_lng_lat, (list, tuple)) or len(center_lng_lat) != 2:
        raise ValueError("Circle center must be [longitude, latitude]")
    longitude, latitude = center_lng_lat
    for value in (longitude, latitude):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Circle center must be [longitude, latitude]")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("Circle center must be WGS84 longitude/latitude")
    radius = _distance(meters, 10, 5000000, "circle radius")
    ring = []
    for step in range(CIRCLE_VERTICES):
        point = GEOD.fwd(longitude, latitude, step * (360.0 / CIRCLE_VERTICES), radius)
        ring.append([_normalize_longitude(point[0]), point[1]])
    ring.append(list(ring[0]))
    polygon = Polygon(ring)
    if not polygon.is_valid:
        polygon = make_valid(polygon)
    polygon = orient(polygon, 1.0)
    sanitized, _ = _sanitized(polygon)
    return sanitized
