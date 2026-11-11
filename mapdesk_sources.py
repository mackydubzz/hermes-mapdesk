"""Bounded data imports; attributes are data, never executable instructions."""
import csv
from datetime import datetime, timezone
import io
import json
import http.client
import ipaddress
import os
from pathlib import Path
import socket
import math
import time
import re
from urllib.parse import urlencode, urlsplit


def _arcgis_arguments(url, bbox, limit):
    if (not isinstance(url, str) or not url.startswith("https://")
            or any(character.isspace() or ord(character) < 32 for character in url)
            or any(character in url for character in "?#\\%")):
        raise ValueError("ArcGIS URL must be an HTTPS layer URL without credentials, query or fragment")
    parsed = urlsplit(url)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or not parsed.netloc.isascii()
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+/(?:MapServer|FeatureServer)/[0-9]+", parsed.path)
            and not re.fullmatch(r"/(?:MapServer|FeatureServer)/[0-9]+", parsed.path)
            or any(part in (".", "..") for part in parsed.path.split("/"))):
        raise ValueError("ArcGIS URL must end in MapServer/<number> or FeatureServer/<number>")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("ArcGIS limit must be an integer from 1 to 1000")
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox)
            or not -180 <= bbox[0] < bbox[2] <= 180
            or not -90 <= bbox[1] < bbox[3] <= 90):
        raise ValueError("bbox must be [west, south, east, north] in WGS84 with positive area")
    return parsed, list(bbox)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Direct TLS to an already-validated address, without proxies or DNS reuse."""

    def __init__(self, hostname, records, timeout=15):
        super().__init__(hostname, port=443, timeout=timeout)
        self._records = records

    def connect(self):
        family, kind, proto, canonname, address = self._records[0]
        raw = socket.socket(family, kind, proto)
        try:
            raw.settimeout(self.timeout)
            raw.connect(address)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _resolve_public(hostname):
    records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not records:
        raise ValueError("Layer hostname has no public addresses")
    for family, kind, proto, canonname, address in records:
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                or isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo)):
            raise ValueError("Layer hostname must resolve exclusively to public addresses")
    return records


def _invalid_json_constant(value):
    raise ValueError("Nonfinite JSON numbers are not supported")


def _request_json(parsed, records, params, suffix="/query"):
    connection = _PinnedHTTPSConnection(parsed.hostname, records, timeout=15)
    try:
        connection.request("GET", parsed.path + suffix + "?" + urlencode(params), headers={
            "Accept": "application/json, application/geo+json", "Accept-Encoding": "identity",
            "User-Agent": "MapDesk/0.1 (bounded GIS import)"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"ArcGIS HTTP {response.status}; redirects are not followed")
        if response.getheader("Content-Encoding", "identity") not in (None, "identity"):
            raise ValueError("Compressed ArcGIS response is not supported")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > 10 * 1024 * 1024):
            raise ValueError("ArcGIS response exceeds 10 MiB or has invalid length")
        body = response.read(10 * 1024 * 1024 + 1)
        if len(body) > 10 * 1024 * 1024:
            raise ValueError("ArcGIS response exceeds 10 MiB")
        payload = json.loads(body.decode("utf-8-sig"), parse_constant=_invalid_json_constant)
        if not isinstance(payload, dict):
            raise ValueError("ArcGIS JSON response must be an object")
        if "error" in payload:
            raise ValueError("ArcGIS service returned an error response")
        return payload
    finally:
        connection.close()


MAX_ARCGIS_PAGES = 40


def _feature_key(feature):
    return json.dumps(feature["id"] if "id" in feature else feature, sort_keys=True)


_ARCGIS_WHERE_GRAMMAR = ("field tokens ([A-Za-z_][A-Za-z0-9_]*), comparison operators "
                         "= <> > >= < <=, LIKE 'text', IN ('value','value'), single-quoted "
                         "string literals with '' doubling, numbers, AND/OR "
                         "(case-insensitive), balanced parentheses")

_WHERE_TOKEN = re.compile(
    r"\s+|\(|\)|,|<=|>=|<>|=|<|>|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_]*")


def _arcgis_page_size(metadata):
    """Page size is the service's own maxRecordCount, capped at 1000."""
    max_record_count = metadata.get("maxRecordCount")
    if type(max_record_count) is int and max_record_count > 0:
        return min(max_record_count, 1000)
    return 1000


def _arcgis_field_names(metadata):
    """Layer field names from the metadata document, for where-clause validation."""
    fields = metadata.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("ArcGIS layer metadata must list its fields before a where clause can be validated")
    names = []
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str) or not field["name"]:
            raise ValueError("ArcGIS layer metadata fields must be objects with a name")
        names.append(field["name"])
    return names


def _where_tokens(where):
    """Split a where clause into grammar tokens; any other character is rejected."""
    tokens, position = [], 0
    while position < len(where):
        match = _WHERE_TOKEN.match(where, position)
        if match is None:
            raise ValueError(f"Invalid where clause near {where[position:position + 12]!r}; "
                             f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
        token = match.group()
        position = match.end()
        if not token.isspace():
            tokens.append(token)
    return tokens


def _where_kind(token):
    if token in ("(", ")", ","):
        return token
    if token in ("=", "<>", ">", ">=", "<", "<="):
        return "op"
    if token.startswith("'"):
        return "string"
    if token[0].isdigit() or (token[0] == "-" and len(token) > 1):
        return "number"
    if token.upper() in ("AND", "OR", "LIKE", "IN"):
        return token.upper()
    return "word"


def _where_field(token, fields):
    name = fields.get(token.lower())
    if name is None:
        raise ValueError(f"Unknown field {token!r} in where clause; layer fields: "
                         f"{', '.join(fields.values())}; allowed: {_ARCGIS_WHERE_GRAMMAR}")
    return name


def _where_value(tokens, position, fields):
    if position >= len(tokens):
        raise ValueError(f"Missing value in where clause; allowed: {_ARCGIS_WHERE_GRAMMAR}")
    token = tokens[position]
    kind = _where_kind(token)
    if kind in ("string", "number"):
        return token, position + 1
    if kind == "word":
        return _where_field(token, fields), position + 1
    raise ValueError(f"Expected a number, string or field in where clause, found {token!r}; "
                     f"allowed: {_ARCGIS_WHERE_GRAMMAR}")


def _where_comparison(tokens, position, fields):
    field = _where_field(tokens[position], fields)
    position += 1
    if position >= len(tokens):
        raise ValueError(f"Missing operator after field {field!r} in where clause; "
                         f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
    token = tokens[position]
    kind = _where_kind(token)
    if kind == "op":
        value, position = _where_value(tokens, position + 1, fields)
        return f"{field} {token} {value}", position
    if kind == "LIKE":
        if position + 1 >= len(tokens) or _where_kind(tokens[position + 1]) != "string":
            raise ValueError(f"LIKE needs a single-quoted string in where clause; "
                             f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
        return f"{field} LIKE {tokens[position + 1]}", position + 2
    if kind == "IN":
        position += 1
        if position >= len(tokens) or tokens[position] != "(":
            raise ValueError(f"IN needs a parenthesized value list in where clause; "
                             f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
        values, position = [], position + 1
        while True:
            value, position = _where_value(tokens, position, fields)
            values.append(value)
            if position >= len(tokens):
                raise ValueError(f"Unterminated IN list in where clause; "
                                 f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
            if tokens[position] == ")":
                return f"{field} IN ({', '.join(values)})", position + 1
            if tokens[position] != ",":
                raise ValueError(f"IN list values must be comma-separated in where clause; "
                                 f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
            position += 1
    raise ValueError(f"Expected an operator after field {field!r} in where clause, "
                     f"found {token!r}; allowed: {_ARCGIS_WHERE_GRAMMAR}")


def _where_primary(tokens, position, fields, depth):
    if position >= len(tokens):
        raise ValueError(f"Truncated where clause; allowed: {_ARCGIS_WHERE_GRAMMAR}")
    token = tokens[position]
    kind = _where_kind(token)
    if kind == "(":
        if depth >= 64:
            raise ValueError(f"Where clause nests too deeply; allowed: {_ARCGIS_WHERE_GRAMMAR}")
        inner, position = _where_expr(tokens, position + 1, fields, depth + 1)
        if position >= len(tokens) or tokens[position] != ")":
            raise ValueError(f"Unbalanced parentheses in where clause; "
                             f"allowed: {_ARCGIS_WHERE_GRAMMAR}")
        return "(" + inner + ")", position + 1
    if kind == "word":
        return _where_comparison(tokens, position, fields)
    raise ValueError(f"Expected a field name or '(' in where clause, found {token!r}; "
                     f"allowed: {_ARCGIS_WHERE_GRAMMAR}")


def _where_and_chain(tokens, position, fields, depth):
    parts = []
    text, position = _where_primary(tokens, position, fields, depth)
    parts.append(text)
    while position < len(tokens) and _where_kind(tokens[position]) == "AND":
        text, position = _where_primary(tokens, position + 1, fields, depth)
        parts.append("AND " + text)
    return " ".join(parts), position


def _where_expr(tokens, position, fields, depth):
    parts = []
    text, position = _where_and_chain(tokens, position, fields, depth)
    parts.append(text)
    while position < len(tokens) and _where_kind(tokens[position]) == "OR":
        text, position = _where_and_chain(tokens, position + 1, fields, depth)
        parts.append("OR " + text)
    return " ".join(parts), position


def _validate_where_clause(where, field_names):
    """Validate an ArcGIS where clause and rewrite fields to the service's casing."""
    fields = {name.lower(): name for name in field_names}
    tokens = _where_tokens(where)
    if not tokens:
        raise ValueError(f"Empty where clause; allowed: {_ARCGIS_WHERE_GRAMMAR}")
    text, position = _where_expr(tokens, 0, fields, 0)
    if position != len(tokens):
        raise ValueError(f"Unexpected {tokens[position]!r} after a complete expression in "
                         f"where clause; allowed: {_ARCGIS_WHERE_GRAMMAR}")
    return text


def fetch_arcgis(url, bbox, limit=1000, where=None, max_features=5000):
    """Fetch a bounded layer query; complete only describes the requested bbox."""
    parsed, bbox = _arcgis_arguments(url, bbox, limit)
    if type(max_features) is not int or not 1 <= max_features <= 5000:
        raise ValueError("ArcGIS max_features must be an integer from 1 to 5000")
    if where is not None:
        if not isinstance(where, str):
            raise ValueError("ArcGIS where clause must be a string")
        where = where.strip() or None
    records = _resolve_public(parsed.hostname)
    page_size = 1000
    if where is not None:
        # Validate the clause against the layer's own field list before querying.
        metadata = _request_json(parsed, records, {"f": "json"}, suffix="")
        page_size = _arcgis_page_size(metadata)
        where = _validate_where_clause(where, _arcgis_field_names(metadata))
    spatial = {"where": where if where is not None else "1=1",
               "geometry": ",".join(str(value) for value in bbox),
               "geometryType": "esriGeometryEnvelope", "inSR": "4326",
               "spatialRel": "esriSpatialRelIntersects"}
    count_response = _request_json(parsed, records, dict(spatial, f="json", returnCountOnly="true"))
    count = count_response.get("count")
    if type(count) is not int or count < 0:
        raise ValueError("ArcGIS count response must contain a nonnegative integer count")
    # A service may cap one response far below the page size (50 is common) and say
    # so with exceededTransferLimit. Only then is another page requested.
    geojson, original_crs, seen, pages, offset = None, None, set(), 0, 0
    while True:
        collected = len(geojson["features"]) if geojson else 0
        requested = min(limit, page_size, max_features - collected)
        params = dict(spatial, f="geojson", outSR="4326", outFields="*", returnGeometry="true",
                      resultRecordCount=str(requested))
        if geojson:
            params["resultOffset"] = str(offset)
        page = _request_json(parsed, records, params)
        if page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise ValueError("ArcGIS must return a GeoJSON FeatureCollection")
        if len(page["features"]) > requested:
            raise ValueError("ArcGIS returned more features than the requested limit")
        if any(not isinstance(feature, dict) or feature.get("type") != "Feature" for feature in page["features"]):
            raise ValueError("ArcGIS must return GeoJSON Feature objects")
        page_crs = page.get('crs')
        if page_crs is not None:
            if page_crs not in ({'type': 'name', 'properties': {'name': 'EPSG:4326'}},
                                {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}}):
                raise ValueError('ArcGIS returned an unsupported CRS despite outSR=4326')
            page = {key: value for key, value in page.items() if key != 'crs'}
        exceeded = page.get("exceededTransferLimit", page.get("properties", {}).get("exceededTransferLimit"))
        pages += 1
        if geojson is None:
            geojson, original_crs, fresh = page, page_crs, page["features"]
            offset = len(page["features"])
            seen.update(_feature_key(feature) for feature in fresh)
        else:
            # A service that ignores resultOffset repeats its first page; never store duplicates.
            fresh = [feature for feature in page["features"] if _feature_key(feature) not in seen]
            seen.update(_feature_key(feature) for feature in fresh)
            geojson["features"].extend(fresh)
            offset += len(page["features"])
        returned = len(geojson["features"])
        if (exceeded is not True or not fresh or returned >= min(max_features, count)
                or pages >= MAX_ARCGIS_PAGES):
            break
    complete = returned == count and exceeded is not True
    return {"geojson": geojson, "source": {
        "kind": "arcgis", "crs": "EPSG:4326", "original_crs": original_crs,
        "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "bbox": bbox, "limit": limit, "where": where, "requested_max": max_features,
        "total_count": count, "returned_count": returned,
        "exceededTransferLimit": exceeded, "complete": complete, "pages": pages,
        "sample": not complete, "completeness_basis": "count_query"}}


def _csv_rows(reader):
    try:
        yield from reader
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV row {reader.line_num}") from exc


def csv_to_geojson(text, latitude_column, longitude_column):
    """Map explicit CSV coordinate columns to WGS84 points, retaining text."""
    if not isinstance(text, str):
        raise ValueError("CSV input must be UTF-8 text")
    if len(text.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("CSV input exceeds 10 MiB")
    if (not isinstance(latitude_column, str) or not latitude_column
            or not isinstance(longitude_column, str) or not longitude_column
            or latitude_column == longitude_column):
        raise ValueError("Specify distinct latitude and longitude columns")
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    headers = reader.fieldnames
    if (not headers or any(not name for name in headers) or len(set(headers)) != len(headers)
            or latitude_column not in headers or longitude_column not in headers):
        raise ValueError("CSV needs unique nonempty headers and the specified coordinate columns")
    features = []
    for row in _csv_rows(reader):
        if len(features) >= 5000:
            raise ValueError("CSV input exceeds 5000 rows")
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Malformed CSV row {reader.line_num}: field count mismatch")
        try:
            longitude, latitude = float(row[longitude_column]), float(row[latitude_column])
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError("Coordinates outside WGS84 bounds")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid coordinates at CSV row {reader.line_num}") from exc
        features.append({"type": "Feature", "properties": row, "geometry": {
            "type": "Point", "coordinates": [longitude, latitude]}})
    return {"geojson": {"type": "FeatureCollection", "features": features}, "source": {
        "kind": "csv", "url": None, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "complete": True, "returned_count": len(features),
        "latitude_column": latitude_column, "longitude_column": longitude_column}}


_NOMINATIM_HOST = "nominatim.openstreetmap.org"
_NOMINATIM_MIN_INTERVAL = 1.1
_NOMINATIM_TIMEOUT = 20
_GEOCODE_BATCH_MAX = 25
_GEOCODE_ADDRESS_MAX = 300
_GEOCODE_BUDGET_SECONDS = 60.0
_geocode_last_request = 0.0


def _geocode_throttle():
    """Nominatim asks for at most one request per second; stay comfortably above."""
    global _geocode_last_request
    due = _geocode_last_request + _NOMINATIM_MIN_INTERVAL
    now = time.monotonic()
    if now < due:
        time.sleep(due - now)
        now = time.monotonic()
    _geocode_last_request = now


def _geocode_cache_path(cache_dir):
    return cache_dir / "geocode_cache.json"


def _geocode_load_cache(cache_dir):
    """Read the address cache tolerantly; any problem means starting fresh."""
    try:
        with open(_geocode_cache_path(cache_dir), encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(cache, dict):
        return {}
    entries = {}
    for address, entry in cache.items():
        if not isinstance(address, str) or not isinstance(entry, dict):
            continue
        try:
            entries[address] = {"lon": float(entry["lon"]), "lat": float(entry["lat"])}
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def _geocode_save_cache(cache_dir, cache):
    """Best-effort atomic cache write; failures never break a batch."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _geocode_cache_path(cache_dir)
        temporary = cache_dir / (path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(cache, handle)
        os.replace(temporary, path)
    except OSError:
        pass


def _geocode_one(address):
    """Resolve one address to WGS84 lon/lat or return None; never raises."""
    connection = http.client.HTTPSConnection(_NOMINATIM_HOST, timeout=_NOMINATIM_TIMEOUT)
    try:
        connection.request("GET", "/search?" + urlencode(
            {"format": "jsonv2", "limit": "1", "q": address}), headers={
            "Accept": "application/json", "Accept-Encoding": "identity",
            "Accept-Language": "en",
            "User-Agent": "MapDesk/0.2 (Hermes Desktop plugin; GIS import)"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        if response.getheader("Content-Encoding", "identity") not in (None, "identity"):
            return None
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            return None
        matches = json.loads(body.decode("utf-8-sig"))
        if not isinstance(matches, list) or not matches or not isinstance(matches[0], dict):
            return None
        lon, lat = float(matches[0]["lon"]), float(matches[0]["lat"])
        if not (math.isfinite(lon) and -180 <= lon <= 180 and math.isfinite(lat) and -90 <= lat <= 90):
            return None
        return {"lon": lon, "lat": lat}
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def geocode_batch(addresses, cache_dir=None):
    """Rate-limited Nominatim geocoding of up to 25 addresses, cached and null-padded."""
    if not isinstance(addresses, (list, tuple)) or not 1 <= len(addresses) <= _GEOCODE_BATCH_MAX:
        raise ValueError(f"geocode_batch takes a list of 1 to {_GEOCODE_BATCH_MAX} addresses")
    if any(not isinstance(address, str) or len(address) > _GEOCODE_ADDRESS_MAX for address in addresses):
        raise ValueError(f"Each address must be a string of at most {_GEOCODE_ADDRESS_MAX} characters")
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
    cache = _geocode_load_cache(cache_dir) if cache_dir is not None else {}
    started = time.monotonic()
    results = []
    for address in addresses:
        if address in cache:
            results.append(dict(cache[address]))
            continue
        if time.monotonic() - started >= _GEOCODE_BUDGET_SECONDS:
            results.append(None)
            continue
        _geocode_throttle()
        found = _geocode_one(address)
        if found is not None:
            cache[address] = found
        results.append(found)
    if cache_dir is not None:
        _geocode_save_cache(cache_dir, cache)
    return {"results": results}
