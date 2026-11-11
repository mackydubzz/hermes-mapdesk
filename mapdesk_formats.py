"""Bounded file-format imports; every parser emits WGS84 GeoJSON or fails clearly."""
import base64
import binascii
from datetime import date, datetime, time, timezone
import io
import math
import re

import xml.etree.ElementTree as ET
import zipfile

import openpyxl
import pyproj
from pyproj.exceptions import ProjError
from shapely.geometry import Polygon as _ShapelyPolygon
import shapefile


MAX_FEATURES = 5000
MAX_TEXT_BYTES = 10 * 1024 * 1024
MAX_DECODED_BYTES = 8 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024

LATITUDE_HEADERS = ("latitude", "lat")
LONGITUDE_HEADERS = ("longitude", "lon", "lng")
WGS84_NAMES = {
    "epsg:4326", "crs84", "ogc:crs84",
    "urn:ogc:def:crs:ogc:1.3:crs84",
    "urn:ogc:def:crs:epsg::4326", "urn:ogc:def:crs:epsg:6.9:4326",
    "https://www.opengis.net/def/crs/ogc/1.3/crs84",
    "https://www.opengis.net/def/crs/epsg/0/4326",
    "http://www.opengis.net/def/crs/ogc/1.3/crs84",
    "http://www.opengis.net/def/crs/epsg/0/4326",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _result(kind, features, note, **extra):
    source = {"kind": kind, "url": None, "crs": "EPSG:4326", "complete": True,
              "fetched_at": _now(), "note": note, "returned_count": len(features)}
    source.update(extra)
    return {"geojson": {"type": "FeatureCollection", "features": features}, "source": source}


def _require_text(text, kind):
    if not isinstance(text, str):
        raise ValueError(f"{kind} input must be UTF-8 text")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError(f"{kind} input exceeds 10 MiB")
    if "<!doctype" in text.lower():
        raise ValueError(f"{kind} input must not contain a DOCTYPE declaration")
    return text


def _decode_base64(value, kind):
    if not isinstance(value, str):
        raise ValueError(f"{kind} input must be a base64 string")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"{kind} input is not valid base64") from exc
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError(f"{kind} input exceeds 8 MiB decoded")
    return raw


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _localname(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _position(value, positions, kind, bounds):
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise ValueError(f"{kind} positions must contain 2 or 3 numbers")
    if not all(_finite(number) for number in value):
        raise ValueError(f"{kind} coordinates must be finite numbers")
    if bounds and not (-180 <= value[0] <= 180 and -90 <= value[1] <= 90):
        raise ValueError(f"{kind} coordinates must be WGS84 longitude/latitude")
    positions.append(list(value))


def _positions(values, positions, kind, bounds):
    """_position for each entry, in order, with the ordinary case checked inline.

    A list of two finite floats (x - x is 0.0 only for a finite x), in range
    when bounds are enforced, needs none of the other checks; everything
    else takes the full path and is reported exactly as before."""
    for value in values:
        if type(value) is list and len(value) == 2:
            x, y = value
            if (type(x) is float and type(y) is float and x - x == 0.0 and y - y == 0.0
                    and (not bounds or (-180 <= x <= 180 and -90 <= y <= 90))):
                positions.append([x, y])
                continue
        _position(value, positions, kind, bounds)


def _check(geometry, positions, kind, bounds):
    """Structural GeoJSON validation for the supported geometry types."""
    if not isinstance(geometry, dict):
        raise ValueError(f"{kind} feature geometry must be a GeoJSON object")
    if "crs" in geometry:
        raise ValueError("CRS declarations are only allowed on the FeatureCollection")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point":
        _position(coords, positions, kind, bounds)
    elif gtype in ("LineString", "MultiPoint"):
        if gtype == "LineString" and (not isinstance(coords, list) or len(coords) < 2):
            raise ValueError(f"{kind} LineString needs at least two positions")
        if gtype == "MultiPoint" and (not isinstance(coords, list) or not coords):
            raise ValueError(f"{kind} MultiPoint cannot be empty")
        _positions(coords or (), positions, kind, bounds)
    elif gtype in ("Polygon", "MultiLineString"):
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{kind} {gtype} cannot be empty")
        for ring in coords:
            if gtype == "Polygon" and (not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]):
                raise ValueError(f"{kind} polygon rings must be closed with at least four positions")
            if gtype == "MultiLineString" and (not isinstance(ring, list) or len(ring) < 2):
                raise ValueError(f"{kind} LineString needs at least two positions")
            _positions(ring, positions, kind, bounds)
    elif gtype == "MultiPolygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{kind} MultiPolygon cannot be empty")
        for polygon in coords:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError(f"{kind} MultiPolygon needs rings")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError(f"{kind} polygon rings must be closed with at least four positions")
                _positions(ring, positions, kind, bounds)
    else:
        raise ValueError(f"Unsupported {kind} geometry type")


def _walk(geometry, convert):
    """Apply convert(position) to every position, returning a new geometry dict."""
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    if gtype == "Point":
        return {"type": gtype, "coordinates": convert(coords)}
    if gtype in ("LineString", "MultiPoint"):
        return {"type": gtype, "coordinates": [convert(position) for position in coords]}
    if gtype in ("Polygon", "MultiLineString"):
        return {"type": gtype, "coordinates": [[convert(position) for position in ring] for ring in coords]}
    return {"type": gtype, "coordinates": [[[convert(position) for position in ring] for ring in polygon]
                                           for polygon in coords]}


def _crs_name(member):
    if isinstance(member, str) and member.strip():
        return member.strip()
    if isinstance(member, dict):
        if member.get("type") == "link":
            raise ValueError("Link CRS references are not supported; use a named CRS")
        properties = member.get("properties")
        name = properties.get("name") if isinstance(properties, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    raise ValueError("CRS member must be a named coordinate reference system")


def _crs_is_wgs84(crs):
    return crs.to_epsg() == 4326 or crs.equals(pyproj.CRS.from_epsg(4326))


def _is_wgs84_name(name):
    if name.casefold() in {entry.casefold() for entry in WGS84_NAMES}:
        return True
    try:
        return _crs_is_wgs84(pyproj.CRS.from_user_input(name))
    except Exception:
        return False


def _reproject(geometry, transformer, kind):
    def convert(position):
        try:
            x, y = transformer.transform(position[0], position[1])
        except (ProjError, ValueError, TypeError) as exc:
            raise ValueError(f"{kind} coordinates cannot be reprojected") from exc
        if not _finite(x) or not _finite(y):
            raise ValueError(f"{kind} reprojection produced non-finite coordinates")
        result = [x, y]
        if len(position) == 3:
            result.append(position[2])
        return result
    return _walk(geometry, convert)


def _zip_archive(raw, kind):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"{kind} input is not a valid ZIP archive") from exc
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts = info.filename.replace("\\", "/").split("/")
        if info.filename.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", info.filename) or ".." in parts:
            raise ValueError(f"{kind} archive contains unsafe entry names")
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"{kind} archive entry exceeds 64 MiB decoded")
        total += info.file_size
        if total > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"{kind} archive exceeds 64 MiB decoded")
    return archive


def _zip_read(archive, info, kind, cap=MAX_ZIP_MEMBER_BYTES):
    try:
        with archive.open(info) as handle:
            data = handle.read(cap + 1)
    except Exception as exc:
        raise ValueError(f"{kind} archive entry cannot be read") from exc
    if len(data) > cap:
        raise ValueError(f"{kind} archive entry exceeds 64 MiB decoded")
    return data


# --- KML / KMZ ----------------------------------------------------------------

_KML_GEOMETRIES = ("Point", "LineString", "Polygon")


def _kml_coordinates(element, kind):
    for child in element:
        if _localname(child.tag) == "coordinates":
            positions = []
            for token in (child.text or "").split():
                parts = token.split(",")
                if len(parts) not in (2, 3):
                    raise ValueError(f"{kind} coordinates must be lon,lat[,alt]")
                try:
                    numbers = [float(part) for part in parts]
                except ValueError as exc:
                    raise ValueError(f"{kind} coordinates must be numeric") from exc
                if not all(math.isfinite(number) for number in numbers):
                    raise ValueError(f"{kind} coordinates must be finite numbers")
                if not -180 <= numbers[0] <= 180 or not -90 <= numbers[1] <= 90:
                    raise ValueError(f"{kind} coordinates must be WGS84 longitude/latitude")
                positions.append(numbers if len(numbers) == 3 else numbers[:2])
            if not positions:
                raise ValueError(f"{kind} coordinates must be lon,lat[,alt]")
            return positions
    raise ValueError(f"{kind} geometry needs coordinates")


def _kml_ring(boundary, kind):
    for child in boundary:
        if _localname(child.tag) == "LinearRing":
            positions = _kml_coordinates(child, kind)
            if len(positions) < 3:
                raise ValueError(f"{kind} LinearRing needs at least three positions")
            if (positions[0][0], positions[0][1]) != (positions[-1][0], positions[-1][1]):
                positions.append(list(positions[0]))
            return positions
    raise ValueError(f"{kind} boundary needs a LinearRing")


def _kml_geometry(element, kind):
    name = _localname(element.tag)
    if name == "Point":
        positions = _kml_coordinates(element, kind)
        if len(positions) != 1:
            raise ValueError(f"{kind} Point needs a single coordinate position")
        return {"type": "Point", "coordinates": positions[0]}
    if name == "LineString":
        positions = _kml_coordinates(element, kind)
        if len(positions) < 2:
            raise ValueError(f"{kind} LineString needs at least two positions")
        return {"type": "LineString", "coordinates": positions}
    if name == "Polygon":
        shell, holes = None, []
        for child in element:
            boundary = _localname(child.tag)
            if boundary == "outerBoundaryIs" and shell is None:
                shell = _kml_ring(child, kind)
            elif boundary == "innerBoundaryIs":
                holes.append(_kml_ring(child, kind))
        if shell is None:
            raise ValueError(f"{kind} Polygon needs an outer boundary")
        return {"type": "Polygon", "coordinates": [shell] + holes}
    raise ValueError(f"Unsupported {kind} geometry: {name}")


def _kml_multigeometry(element):
    found = []
    for child in element:
        name = _localname(child.tag)
        if name in _KML_GEOMETRIES:
            found.append(child)
        elif name == "MultiGeometry":
            found.extend(_kml_multigeometry(child))
    return found


def _kml_extended(element, properties):
    for child in element:
        if _localname(child.tag) == "Data":
            key = child.get("name")
            if key:
                for value in child:
                    if _localname(value.tag) == "value":
                        properties[key] = (value.text or "").strip()
        elif _localname(child.tag) == "SchemaData":
            for data in child:
                if _localname(data.tag) == "SimpleData":
                    key = data.get("name")
                    if key:
                        properties[key] = (data.text or "").strip()


def _kml_placemark(element):
    properties, geometries = {}, []
    for child in element:
        name = _localname(child.tag)
        if name == "name" and (child.text or "").strip():
            properties["name"] = child.text.strip()
        elif name == "ExtendedData":
            _kml_extended(child, properties)
        elif name in _KML_GEOMETRIES:
            geometries.append(child)
        elif name == "MultiGeometry":
            geometries.extend(_kml_multigeometry(child))
    return properties, geometries


def _kml_features(text, kind):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{kind} content is not well-formed XML") from exc
    if _localname(root.tag) != "kml":
        raise ValueError(f"{kind} content must be a <kml> document")
    features = []
    for element in root.iter():
        if _localname(element.tag) != "Placemark":
            continue
        properties, geometries = _kml_placemark(element)
        for geometry in geometries:
            if len(features) >= MAX_FEATURES:
                raise ValueError(f"{kind} exceeds 5000 features")
            features.append({"type": "Feature", "properties": dict(properties),
                             "geometry": _kml_geometry(geometry, kind)})
    return features


def parse_kml(text):
    """Parse namespace-agnostic KML text into WGS84 GeoJSON features."""
    features = _kml_features(_require_text(text, "KML"), "KML")
    return _result("kml", features, "Complete uploaded file; parsed KML placemarks")


def parse_kmz(base64_str):
    """Decode a KMZ archive and parse its first .kml entry."""
    raw = _decode_base64(base64_str, "KMZ")
    with _zip_archive(raw, "KMZ") as archive:
        entry = next((info for info in archive.infolist()
                      if not info.is_dir() and info.filename.lower().endswith(".kml")), None)
        if entry is None:
            raise ValueError("KMZ archive contains no .kml entry")
        data = _zip_read(archive, entry, "KMZ")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("KMZ KML entry must be UTF-8 text") from exc
    features = _kml_features(_require_text(text, "KMZ"), "KMZ")
    return _result("kmz", features, "Complete uploaded file; parsed KMZ archive")


# --- GPX ----------------------------------------------------------------------


def _gpx_position(element, kind):
    lat_raw, lon_raw = element.get("lat"), element.get("lon")
    if lat_raw is None or lon_raw is None:
        raise ValueError(f"{kind} point needs lat and lon attributes")
    try:
        lon, lat = float(lon_raw), float(lat_raw)
    except ValueError as exc:
        raise ValueError(f"{kind} point coordinates must be numeric") from exc
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError(f"{kind} point coordinates must be finite numbers")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError(f"{kind} coordinates must be WGS84 longitude/latitude")
    return [lon, lat]


def _gpx_properties(element, keys):
    properties = {}
    for child in element:
        name = _localname(child.tag)
        if name in keys:
            properties[name] = (child.text or "").strip()
    return properties


def _gpx_track(element, kind):
    segments = []
    for child in element:
        if _localname(child.tag) != "trkseg":
            continue
        points = [_gpx_position(point, kind) for point in child if _localname(point.tag) == "trkpt"]
        if len(points) >= 2:
            segments.append(points)
    if not segments:
        return None
    if len(segments) == 1:
        return {"type": "LineString", "coordinates": segments[0]}
    return {"type": "MultiLineString", "coordinates": segments}


def _gpx_route(element, kind):
    points = [_gpx_position(point, kind) for point in element if _localname(point.tag) == "rtept"]
    if len(points) < 2:
        return None
    return {"type": "LineString", "coordinates": points}


def parse_gpx(text):
    """Parse GPX waypoints, tracks, and routes into WGS84 GeoJSON features."""
    kind = "GPX"
    _require_text(text, kind)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{kind} content is not well-formed XML") from exc
    if _localname(root.tag) != "gpx":
        raise ValueError(f"{kind} content must be a <gpx> document")
    features = []
    for element in root.iter():
        name = _localname(element.tag)
        if name == "wpt":
            geometry = {"type": "Point", "coordinates": _gpx_position(element, kind)}
            properties = _gpx_properties(element, ("name", "desc", "ele", "time"))
        elif name == "trk":
            geometry = _gpx_track(element, kind)
            properties = {}
        elif name == "rte":
            geometry = _gpx_route(element, kind)
            properties = {}
        else:
            continue
        if geometry is None:
            continue
        if len(features) >= MAX_FEATURES:
            raise ValueError(f"{kind} exceeds 5000 features")
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    return _result("gpx", features, "Complete uploaded file; parsed GPX waypoints, tracks, and routes")


# --- XLSX ---------------------------------------------------------------------


def _header_index(headers, wanted):
    for index, header in enumerate(headers):
        if header.casefold() in wanted:
            return index
    return None


def _cell(row, index):
    return row[index] if index < len(row) else None


def _xlsx_number(cell, message):
    if isinstance(cell, bool) or cell is None:
        raise ValueError(message)
    if isinstance(cell, (int, float)):
        if math.isfinite(cell):
            return float(cell)
        raise ValueError(message)
    if isinstance(cell, str) and cell.strip():
        try:
            number = float(cell.strip())
        except ValueError:
            raise ValueError(message) from None
        if math.isfinite(number):
            return number
    raise ValueError(message)


def _xlsx_value(cell):
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return cell
    if isinstance(cell, (datetime, date, time)):
        return cell.isoformat()
    if isinstance(cell, float):
        return cell if math.isfinite(cell) else ""
    if isinstance(cell, int):
        return cell
    if isinstance(cell, str):
        return cell
    return str(cell)


def parse_xlsx(base64_str, sheet=None):
    """Parse an XLSX workbook sheet into WGS84 point features."""
    kind = "XLSX"
    raw = _decode_base64(base64_str, kind)
    if sheet is not None and not isinstance(sheet, str):
        raise ValueError(f"{kind} sheet must be a string name")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as exc:
        raise ValueError(f"{kind} input is not a valid workbook") from exc
    try:
        if not workbook.sheetnames:
            raise ValueError(f"{kind} workbook has no sheets")
        title = sheet if sheet is not None else workbook.sheetnames[0]
        if title not in workbook.sheetnames:
            raise ValueError(f"Unknown {kind} sheet: {sheet}")
        rows = workbook[title].iter_rows(values_only=True)
        header_row = next(rows, None)
        if header_row is None:
            raise ValueError(f"{kind} sheet has no header row")
        headers = [str(cell).strip() if cell is not None else "" for cell in header_row]
        if any(not header for header in headers) or len(set(headers)) != len(headers):
            raise ValueError(f"{kind} header row must contain unique non-empty column names")
        lat_index = _header_index(headers, LATITUDE_HEADERS)
        lon_index = _header_index(headers, LONGITUDE_HEADERS)
        if lat_index is None or lon_index is None:
            raise ValueError(f"{kind} needs latitude and longitude columns; "
                             "expected headers like latitude, lat, longitude, lon, lng")
        features = []
        for number, row in enumerate(rows, start=2):
            if all(cell is None or cell == "" for cell in row):
                continue
            if len(features) >= MAX_FEATURES:
                raise ValueError(f"{kind} exceeds 5000 data rows")
            message = f"Invalid {kind} coordinates at row {number}"
            longitude = _xlsx_number(_cell(row, lon_index), message)
            latitude = _xlsx_number(_cell(row, lat_index), message)
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError(f"{message}: outside WGS84 bounds")
            properties = {headers[index]: _xlsx_value(_cell(row, index)) for index in range(len(headers))}
            features.append({"type": "Feature", "properties": properties,
                             "geometry": {"type": "Point", "coordinates": [longitude, latitude]}})
    finally:
        workbook.close()
    return _result("xlsx", features, f"Complete uploaded file; parsed XLSX sheet '{title}'")


# --- Zipped shapefile -----------------------------------------------------------


def _dbf_value(value, code):
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if code == "D":
        text = str(value)
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if re.fullmatch(r"\d{8}", text) else text
    if code in ("F", "N"):
        if isinstance(value, (int, float)) and math.isfinite(value):
            return value
        if isinstance(value, str) and value.strip():
            try:
                number = float(value)
            except ValueError:
                return value.strip()
            if math.isfinite(number):
                return number
            return value.strip()
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return str(value)


def _ring_within(ring, shell):
    try:
        return _ShapelyPolygon(shell).covers(_ShapelyPolygon(ring))
    except Exception:
        return False


def _shp_polygons(rings):
    """Orientation-agnostic ring grouping: rings inside the last shell are holes."""
    groups = []
    for ring in rings:
        if groups and _ring_within(ring, groups[-1][0]):
            groups[-1][1].append(ring)
        else:
            groups.append((ring, []))
    polygons = [[shell] + holes for shell, holes in groups]
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _shp_geometry(shape, kind):
    shape_type = shape.shapeType
    points = [[point[0], point[1]] for point in shape.points]
    parts = list(shape.parts)
    if shape_type == 0:
        return None
    if shape_type in (1, 11):
        if not points:
            return None
        return {"type": "Point", "coordinates": points[0]}
    if shape_type in (3, 13):
        lines = []
        for start, end in zip(parts, parts[1:] + [len(points)]):
            line = points[start:end]
            if len(line) >= 2:
                lines.append(line)
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}
    if shape_type in (5, 15):
        rings = []
        for start, end in zip(parts, parts[1:] + [len(points)]):
            ring = points[start:end]
            if len(ring) >= 3 and ring[0] != ring[-1]:
                ring = ring + [list(ring[0])]
            if len(ring) >= 4:
                rings.append(ring)
        if not rings:
            return None
        return _shp_polygons(rings)
    if shape_type in (8, 18):
        if not points:
            return None
        return {"type": "MultiPoint", "coordinates": points}
    raise ValueError(f"Unsupported {kind} geometry type: {shape_type}")


def parse_shp_zip(base64_str):
    """Parse a zipped shapefile (.shp+.dbf, optional .prj) into WGS84 features."""
    kind = "SHP"
    raw = _decode_base64(base64_str, kind)
    with _zip_archive(raw, kind) as archive:
        entries = {info.filename.lower(): info for info in archive.infolist() if not info.is_dir()}
        shp_info = next((info for name, info in entries.items()
                         if name.endswith(".shp") and not name.endswith(".shp.xml")), None)
        if shp_info is None:
            raise ValueError(f"{kind} archive must contain matching .shp and .dbf files")
        stem = shp_info.filename.lower()[:-4]
        dbf_info = entries.get(stem + ".dbf")
        if dbf_info is None:
            raise ValueError(f"{kind} archive must contain matching .shp and .dbf files")
        shx_info, prj_info = entries.get(stem + ".shx"), entries.get(stem + ".prj")
        shp_bytes = _zip_read(archive, shp_info, kind)
        dbf_bytes = _zip_read(archive, dbf_info, kind)
        shx_bytes = _zip_read(archive, shx_info, kind) if shx_info else None
        prj_bytes = _zip_read(archive, prj_info, kind) if prj_info else None
    try:
        reader_kwargs = {"shp": io.BytesIO(shp_bytes), "dbf": io.BytesIO(dbf_bytes)}
        if shx_bytes:
            reader_kwargs["shx"] = io.BytesIO(shx_bytes)
        reader = shapefile.Reader(**reader_kwargs)
    except Exception as exc:
        raise ValueError(f"{kind} files could not be read as a shapefile") from exc
    transformer, original_crs = None, None
    if prj_bytes:
        prj_text = prj_bytes.decode("utf-8-sig", errors="replace").strip()
        try:
            crs = pyproj.CRS.from_user_input(prj_text)
        except Exception as exc:
            raise ValueError(f"Unsupported .prj CRS in {kind} archive: {prj_text[:64]}") from exc
        if not _crs_is_wgs84(crs):
            transformer = pyproj.Transformer.from_crs(crs, pyproj.CRS.from_epsg(4326), always_xy=True)
            original_crs = crs.to_string()
    fields = reader.fields[1:]
    features, skipped = [], 0
    for shape_record in reader.iterShapeRecords():
        geometry = _shp_geometry(shape_record.shape, kind)
        if geometry is None:
            skipped += 1
            continue
        if len(features) >= MAX_FEATURES:
            raise ValueError(f"{kind} exceeds 5000 features")
        if transformer is not None:
            geometry = _reproject(geometry, transformer, kind)
        _check(geometry, [], kind, bounds=True)
        properties = {field[0]: _dbf_value(value, field[1])
                      for field, value in zip(fields, shape_record.record)}
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    note = "Complete uploaded file; parsed zipped shapefile"
    if original_crs:
        note += f"; reprojected from {original_crs}"
    return _result("shp", features, note, skipped=skipped, original_crs=original_crs)


# --- GeoJSON CRS normalization ---------------------------------------------------


def _swapped_trigger(positions):
    """Definite swapped-lon/lat signal only: most longitudes inside ±90 while
    some latitude lies outside. In-range ambiguity (e.g. most of Europe has
    |lat| > |lon|) is never auto-swapped — that would corrupt valid data."""
    if not positions:
        return False
    longitudes = [position[0] for position in positions]
    latitudes = [position[1] for position in positions]
    within = sum(-90 <= value <= 90 for value in longitudes) / len(longitudes)
    return within >= 0.9 and any(not -90 <= value <= 90 for value in latitudes)


def normalize_geojson(fc):
    """Strip/reproject a legacy CRS member and flag swapped lon/lat coordinates."""
    kind = "GeoJSON"
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise ValueError("Expected a GeoJSON FeatureCollection")
    features = fc.get("features")
    if not isinstance(features, list):
        raise ValueError("FeatureCollection must contain a features array")
    if len(features) > MAX_FEATURES:
        raise ValueError("GeoJSON exceeds 5000 features")
    positions = []
    for item in features:
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise ValueError("Expected GeoJSON Feature objects")
        if "crs" in item:
            raise ValueError("CRS declarations are only allowed on the FeatureCollection")
        properties = item.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ValueError("Feature properties must be an object or null")
        _check(item.get("geometry"), positions, kind, bounds=False)
    items = [dict(item, properties=item.get("properties") or {}) for item in features]
    original_crs = None
    member = fc.get("crs")
    if member is not None:
        name = _crs_name(member)
        if not _is_wgs84_name(name):
            try:
                crs = pyproj.CRS.from_user_input(name)
            except Exception as exc:
                raise ValueError(f"Unsupported CRS: {name}") from exc
            transformer = pyproj.Transformer.from_crs(crs, pyproj.CRS.from_epsg(4326), always_xy=True)
            items = [dict(item, geometry=_reproject(item["geometry"], transformer, kind)) for item in items]
            original_crs = name
            positions = []
            for item in items:
                _check(item["geometry"], positions, kind, bounds=False)
    swapped = _swapped_trigger(positions)
    if swapped:
        def flip(position):
            return [position[1], position[0]] + list(position[2:])
        items = [dict(item, geometry=_walk(item["geometry"], flip)) for item in items]
        for item in items:
            _check(item["geometry"], [], kind, bounds=True)
    else:
        # The geometries were checked structurally above and have not changed
        # since, so only the range test is left; it reads the positions that
        # pass already collected instead of walking every geometry again.
        for position in positions:
            if not (-180 <= position[0] <= 180 and -90 <= position[1] <= 90):
                raise ValueError(f"{kind} coordinates must be WGS84 longitude/latitude")
    output = dict(fc)
    output.pop("crs", None)
    output["features"] = items
    return {"geojson": output, "original_crs": original_crs, "swapped_warning": swapped}
