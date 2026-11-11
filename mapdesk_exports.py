"""Local export writers: GeoPackage, zipped shapefile and KMZ (WS-E).

Pure stdlib + pyshp + shapely; no I/O beyond what is passed in — outputs are
bytes returned to the caller, never written to disk or to the store.
"""
import io
import json
import math
import re
import sqlite3
import struct
import time
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

PROVENANCE_URI = 'https://hermes-mapdesk.local/provenance'
PROVENANCE_MIME = 'application/json'
PROVENANCE_ENTRY = 'mapdesk_provenance.json'
BASE64_CAP = 12_000_000
WGS84_WKT = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
             '298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
             'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
             'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
             'AUTHORITY["EPSG","4326"]]')
ESRI_WGS84_WKT = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                  'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                  'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
_KML_NS = 'http://www.opengis.net/kml/2.2'
_KIND = {'Point': 'point', 'MultiPoint': 'point', 'LineString': 'line',
         'MultiLineString': 'line', 'Polygon': 'polygon', 'MultiPolygon': 'polygon'}


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _provenance_text(provenance_json):
    if isinstance(provenance_json, str):
        return provenance_json
    return json.dumps(provenance_json, ensure_ascii=False)


def make_provenance(workspace, layer_records):
    """Build the provenance sidecar dict: exported_at/workspace/layers."""
    return {'exported_at': _now_iso(), 'workspace': workspace,
            'layers': [{'name': record['name'], 'source': record.get('source') or {},
                        'feature_count': record.get('feature_count', 0),
                        'fields': list(record.get('fields') or [])}
                       for record in layer_records]}


def sanitize_stem(name):
    """Workspace name -> filename stem: keep [A-Za-z0-9._ -], spaces become
    underscores, at most 60 characters, never blank."""
    if not isinstance(name, str) or not name.strip():
        name = 'mapdesk'
    cleaned = name.replace(' ', '_')
    cleaned = re.sub(r'[^A-Za-z0-9._-]', '_', cleaned)
    cleaned = cleaned[:60].strip(' .-_') or 'mapdesk'
    return cleaned


def export_filename(stem, extension):
    """Sanitized stem + -YYYYMMDD-HHMM + extension."""
    return '{}-{}.{}'.format(sanitize_stem(stem), time.strftime('%Y%m%d-%H%M'), extension)


def _snake(name):
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'layer'


def _unique(base, used, limit=60):
    candidate = base[:limit] or 'layer'
    suffix = 2
    while candidate in used:
        tail = '_{}'.format(suffix)
        candidate = (base[:limit - len(tail)] + tail) or 'layer'
        suffix += 1
    used.add(candidate)
    return candidate


_GPKG_GEOMETRY = {'Point': 'POINT', 'MultiPoint': 'MULTIPOINT', 'LineString': 'LINESTRING',
                  'MultiLineString': 'MULTILINESTRING', 'Polygon': 'POLYGON',
                  'MultiPolygon': 'MULTIPOLYGON', 'GeometryCollection': 'GEOMETRYCOLLECTION'}


def _features(geojson):
    features = (geojson or {}).get('features') or []
    return [item for item in features if isinstance(item, dict)]


def _properties(feature):
    return feature.get('properties') or {}


# ---------------------------------------------------------------- GeoPackage

def _gpkg_blob(geometry, bounds=None):
    """GeoPackageBinary: 'GP' magic, version 0, flags with xy envelope
    (little-endian header), int32 LE srs_id, xy envelope doubles LE, WKB LE."""
    from shapely import wkb as shapely_wkb
    minx, miny, maxx, maxy = bounds if bounds is not None else geometry.bounds
    header = b'GP' + struct.pack('<BBi', 0, 0x02, 4326) + struct.pack('<dddd', minx, maxx, miny, maxy)
    return header + shapely_wkb.dumps(geometry, byte_order=1)


def _column_type(values):
    present = [value for value in values if value is not None]
    if not present:
        return 'TEXT'
    if all(type(value) is bool for value in present):
        return 'INTEGER'  # GPKG has no boolean; 0/1 round-trips
    if all(type(value) is int for value in present):
        return 'INTEGER'
    if all(type(value) in (int, float) and (type(value) is int or math.isfinite(value)) for value in present):
        return 'REAL'
    return 'TEXT'


def _sqlite_value(value):
    if value is None:
        return None
    if type(value) is bool:
        return int(value)
    if type(value) in (int, float) and type(value) is float and not math.isfinite(value):
        return str(value)
    return value


def write_gpkg(layers, provenance_json, filename_stem='mapdesk'):
    """Write a GeoPackage from ordered {name, geojson} layers.

    Returns (bytes, table_names)."""
    from shapely.geometry import shape

    layers = [layer for layer in layers if isinstance(layer, dict)]
    provenance = _provenance_text(provenance_json)
    last_change = _now_iso()
    connection = sqlite3.connect(':memory:')
    connection.execute('PRAGMA application_id = 0x47504B47')
    connection.execute('PRAGMA user_version = 10200')
    connection.executescript("""
        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL);
        CREATE TABLE gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT UNIQUE,
            description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
            srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        CREATE TABLE gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            CONSTRAINT fk_geom_cols FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        CREATE TABLE gpkg_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            md_scope TEXT NOT NULL DEFAULT 'dataset',
            md_standard_uri TEXT,
            mime_type TEXT,
            metadata TEXT NOT NULL);
        CREATE TABLE gpkg_metadata_reference (
            reference_scope TEXT NOT NULL,
            table_name TEXT,
            column_name TEXT,
            row_id_value INTEGER,
            timestamp DATETIME,
            md_file_id INTEGER NOT NULL,
            md_parent_id INTEGER);
    """)
    connection.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?)",
                       ('Undefined geographic SRS', -1, 'undefined', -1, 'undefined'))
    connection.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?)",
                       ('WGS 84 geodetic', 4326, 'EPSG', 4326, WGS84_WKT))
    tables = []
    used_tables = set()
    for layer in layers:
        features = _features(layer.get('geojson'))
        table = _unique(_snake(layer.get('name') or 'layer'), used_tables)
        tables.append(table)
        columns, used_columns = [], set()
        keys = []
        for feature in features:
            for key in _properties(feature):
                if key not in keys:
                    keys.append(key)
        for key in keys:
            name = _unique(_snake(key), used_columns)
            values = [_properties(feature).get(key) for feature in features]
            columns.append((name, key, _column_type(values)))
        column_sql = ', '.join('"{}" {}'.format(name, kind) for name, _key, kind in columns)
        connection.execute('CREATE TABLE "{}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, '
                           'geom BLOB, {})'.format(table, column_sql or 'id INTEGER'))
        types, bounds = set(), [None, None, None, None]
        property_keys = [key for _name, key, _kind in columns]
        rows = []
        for feature in features:
            geometry = feature.get('geometry')
            shapely_geometry = shape(geometry) if geometry else None
            if geometry is not None and geometry.get('type'):
                types.add(geometry['type'])
            if shapely_geometry is None:
                row = [None]
            else:
                extent = shapely_geometry.bounds  # read once: the blob header and the layer extent both use it
                row = [_gpkg_blob(shapely_geometry, extent)]
                if not shapely_geometry.is_empty:
                    minx, miny, maxx, maxy = extent
                    if bounds[0] is None:
                        bounds = [minx, miny, maxx, maxy]
                    else:
                        bounds = [min(bounds[0], minx), min(bounds[1], miny),
                                  max(bounds[2], maxx), max(bounds[3], maxy)]
            properties = _properties(feature)
            for key in property_keys:
                row.append(_sqlite_value(properties.get(key)))
            rows.append(row)
        # One prepared statement for the whole layer instead of building and running one per feature.
        column_list = ', '.join(['"geom"'] + ['"{}"'.format(name) for name, _key, _kind in columns])
        placeholders = ','.join('?' * (1 + len(columns)))
        connection.executemany('INSERT INTO "{}" ({}) VALUES ({})'.format(table, column_list, placeholders), rows)
        geometry_type = ('GEOMETRY' if len(types) != 1
                         else _GPKG_GEOMETRY.get(types.pop(), 'GEOMETRY'))
        connection.execute('INSERT INTO gpkg_contents (table_name, data_type, identifier, '
                           'description, last_change, min_x, min_y, max_x, max_y, srs_id) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (table, 'features', layer.get('name') or table, '', last_change,
                            bounds[0], bounds[1], bounds[2], bounds[3], 4326))
        connection.execute('INSERT INTO gpkg_geometry_columns VALUES (?, ?, ?, ?, ?, ?)',
                           (table, 'geom', geometry_type, 4326, 0, 0))
    connection.execute('INSERT INTO gpkg_metadata (md_scope, md_standard_uri, mime_type, metadata) '
                       'VALUES (?, ?, ?, ?)',
                       ('dataset', PROVENANCE_URI, PROVENANCE_MIME, provenance))
    if tables:
        connection.execute('INSERT INTO gpkg_metadata_reference (reference_scope, table_name, '
                           'column_name, row_id_value, timestamp, md_file_id, md_parent_id) '
                           "VALUES ('table', ?, NULL, NULL, ?, 1, NULL)", (tables[0], last_change))
    connection.commit()
    payload = connection.serialize()
    connection.close()
    return payload, tables


# -------------------------------------------------------------- Shapefile

def _dbf_name(key, used):
    base = (key[:10] or 'FIELD').upper()
    suffix = 1
    candidate = base
    while candidate in used:
        candidate = (base[:9] + str(suffix)).upper()
        suffix += 1
    used.add(candidate)
    return candidate


def _dbf_field(values):
    present = [value for value in values if value is not None]
    if not present:
        return ('C', 254, 0)
    if all(type(value) is bool for value in present):
        return ('L', 1, 0)
    if all(type(value) is int for value in present):
        return ('N', 18, 0)
    if all(type(value) in (int, float) and (type(value) is int or math.isfinite(value)) for value in present):
        return ('N', 20, 10)
    return ('C', 254, 0)


def _dbf_value(value):
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) in (int, float):
        return value
    return str(value)[:254]


def _shp_parts(geometry):
    """Shapefile parts for one feature, or None when the geometry cannot be
    represented in a shapefile (GeometryCollection/unknown)."""
    from shapely.geometry import shape
    from shapely.geometry.polygon import orient

    kind = geometry.get('type')
    g = shape(geometry)
    if kind == 'Point':
        return ('point', [[g.x, g.y]], False)
    if kind == 'MultiPoint':
        return ('point', [list(point.coords)[0] for point in g.geoms], True)
    if kind in ('LineString', 'MultiLineString'):
        parts = [list(part.coords) for part in (g.geoms if hasattr(g, 'geoms') else [g])]
        return ('line', parts, False)
    if kind in ('Polygon', 'MultiPolygon'):
        polygons = list(g.geoms) if hasattr(g, 'geoms') else [g]
        parts = []
        for polygon in polygons:
            oriented = orient(polygon, sign=-1.0)  # shapefile winding: CW outer, CCW holes
            parts.extend(list(ring.coords) for ring in (oriented.exterior, *oriented.interiors))
        return ('polygon', parts, False)
    return None


def write_shp(name, geojson, provenance_json, filename_stem='mapdesk'):
    """Write a zipped shapefile set for one layer, split by geometry type.

    Returns (bytes, names) where names lists the shapefile base names (with
    _point/_line/_polygon suffixes when more than one geometry type is present)
    plus a 'skipped:N' entry for features that cannot be represented."""
    import shapefile

    features = _features(geojson)
    keys = []
    for feature in features:
        for key in _properties(feature):
            if key not in keys:
                keys.append(key)
    used, fields = set(), []
    for key in keys:
        dbf = _dbf_name(key, used)
        kind, size, decimals = _dbf_field([_properties(feature).get(key) for feature in features])
        fields.append((key, dbf, (kind, size, decimals)))
    buckets = {}
    skipped = 0
    for feature in features:
        geometry = feature.get('geometry')
        parts = _shp_parts(geometry) if geometry else None
        if parts is None:
            skipped += 1
            continue
        buckets.setdefault(parts[0], []).append((parts[1], parts[2], _properties(feature)))
    suffixes = {'point': '_point', 'line': '_line', 'polygon': '_polygon'}
    if len(buckets) <= 1:
        suffixes = {kind: '' for kind in suffixes}
    names, entries = [], []
    for kind in ('point', 'line', 'polygon'):
        if kind not in buckets:
            continue
        base = (sanitize_stem(filename_stem) or 'mapdesk') + suffixes[kind]
        names.append(base)
        shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
        if kind == 'point' and any(entry[1] for entry in buckets[kind]):
            shape_type = shapefile.MULTIPOINT  # multipoints present: all parts become multipoints
        else:
            shape_type = {'point': shapefile.POINT, 'line': shapefile.POLYLINE,
                          'polygon': shapefile.POLYGON}[kind]
        writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type)
        for _key, dbf_name, (field_type, size, decimals) in fields:
            writer.field(dbf_name, field_type, size, decimals)
        for coords, is_multi, properties in buckets[kind]:
            if kind == 'point':
                if is_multi:
                    writer.multipoint(coords)
                else:
                    writer.point(*coords[0])
            elif kind == 'line':
                writer.line(coords)
            else:
                writer.poly(coords)
            writer.record(*[_dbf_value(properties.get(key)) for key, _dbf, _spec in fields])
        writer.close()
        for stream, extension in ((shp, '.shp'), (shx, '.shx'), (dbf, '.dbf')):
            entries.append((base + extension, stream.getvalue()))
        entries.append((base + '.prj', ESRI_WGS84_WKT.encode('utf-8')))
    if skipped:
        names.append('skipped:{}'.format(skipped))
    entries.append((PROVENANCE_ENTRY, _provenance_text(provenance_json).encode('utf-8')))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for entry, data in entries:
            archive.writestr(entry, data)
    return buffer.getvalue(), names


# --------------------------------------------------------------------- KMZ

def _kml_color(style):
    color = (style or {}).get('color') or '#3388ff'
    opacity = (style or {}).get('opacity')
    if type(opacity) is not float and type(opacity) is not int:
        opacity = 0.8
    opacity = min(1.0, max(0.0, opacity))
    digits = re.sub(r'[^0-9a-fA-F]', '', str(color))[:6].rjust(6, '3')[-6:]
    red, green, blue = digits[0:2], digits[2:4], digits[4:6]
    return '{:02x}{}{}{}'.format(int(round(opacity * 255)), blue, green, red).upper()


def _kml_coords(ring):
    return ' '.join([f'{point[0]},{point[1]}' for point in ring])


def _kml_geometries(geometry):
    """KML geometry elements for one geojson geometry; collections flatten
    into one element per part (within the same Placemark)."""
    if not isinstance(geometry, dict):
        return []
    kind = geometry.get('type')
    coordinates = geometry.get('coordinates')
    if kind == 'Point' and coordinates:
        return ['<Point><coordinates>{},{}</coordinates></Point>'.format(coordinates[0], coordinates[1])]
    if kind == 'MultiPoint' and coordinates:
        return ['<Point><coordinates>{},{}</coordinates></Point>'.format(point[0], point[1])
                for point in coordinates]
    if kind == 'LineString' and coordinates:
        return ['<LineString><coordinates>{}</coordinates></LineString>'.format(_kml_coords(coordinates))]
    if kind == 'MultiLineString' and coordinates:
        return ['<LineString><coordinates>{}</coordinates></LineString>'.format(_kml_coords(line))
                for line in coordinates]
    if kind == 'Polygon' and coordinates:
        rings = []
        if coordinates:
            rings.append('<outerBoundaryIs><LinearRing><coordinates>{}</coordinates>'
                         '</LinearRing></outerBoundaryIs>'.format(_kml_coords(coordinates[0])))
        for hole in coordinates[1:]:
            rings.append('<innerBoundaryIs><LinearRing><coordinates>{}</coordinates>'
                         '</LinearRing></innerBoundaryIs>'.format(_kml_coords(hole)))
        return ['<Polygon>{}</Polygon>'.format(''.join(rings))]
    if kind == 'MultiPolygon' and coordinates:
        parts = []
        for polygon in coordinates:
            parts.extend(_kml_geometries({'type': 'Polygon', 'coordinates': polygon}))
        return parts
    if kind == 'GeometryCollection' and geometry.get('geometries'):
        parts = []
        for part in geometry['geometries']:
            parts.extend(_kml_geometries(part))
        return parts
    return []


def _kml_placemark_name(feature, index):
    properties = _properties(feature)
    for key in properties:
        if key.lower() in ('name', 'title', 'label') and properties.get(key) is not None:
            return str(properties[key])
    if feature.get('id') is not None:
        return str(feature['id'])
    return 'Feature {}'.format(index + 1)


def write_kmz(layers, provenance_json, filename_stem='mapdesk'):
    """Write a KMZ from ordered {name, geojson, style?} layers.

    Returns (bytes, layer_names)."""
    layers = [layer for layer in layers if isinstance(layer, dict)]
    document = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<kml xmlns="{}"><Document><name>{}</name>'.format(
                    _KML_NS, escape(sanitize_stem(filename_stem) or 'mapdesk'))]
    folders = []
    for index, layer in enumerate(layers):
        style_id = 'layer-{}'.format(index)
        color = _kml_color(layer.get('style'))
        document.append('<Style id="{}"><LineStyle><color>{}</color><width>2</width></LineStyle>'
                        '<PolyStyle><color>{}</color></PolyStyle></Style>'.format(style_id, color, color))
        features = _features(layer.get('geojson'))
        folder = ['<Folder><name>{}</name>'.format(escape(layer.get('name') or 'Layer'))]
        for number, feature in enumerate(features):
            name = escape(_kml_placemark_name(feature, number))
            data = ''.join('<Data name="{}"><value>{}</value></Data>'.format(
                escape(key, {'"': '&quot;'}), escape('' if value is None else str(value)))
                for key, value in sorted(_properties(feature).items()))
            # one Placemark per atomic part; collections and multi-geometries split
            for geometry_element in _kml_geometries(feature.get('geometry')):
                folder.append('<Placemark><name>{}</name><styleUrl>#{}</styleUrl>'.format(name, style_id))
                if data:
                    folder.append('<ExtendedData>{}</ExtendedData>'.format(data))
                folder.append(geometry_element)
                folder.append('</Placemark>')
        folder.append('</Folder>')
        folders.append(''.join(folder))
    document.extend(folders)
    document.append('</Document></kml>')
    kml = '\n'.join(document).encode('utf-8')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('doc.kml', kml)
        archive.writestr(PROVENANCE_ENTRY, _provenance_text(provenance_json).encode('utf-8'))
    return buffer.getvalue(), [layer.get('name') or 'Layer' for layer in layers]
