# MapDesk 0.2 upgrade contracts

Binding contracts for the all-workflow upgrade. Every workstream must conform to
these shapes exactly; integration depends on it. Workstreams touch disjoint files:

- `mapdesk_formats.py` + `tests/test_formats.py` — format parsers + CRS (workstream F)
- `mapdesk_ops.py` + `tests/test_ops.py` — geometry analysis ops (workstream O)
- `mapdesk_sources.py` (where/pagination/geocode) — workstream S
- `mapdesk_core.py`, `mapdesk_runtime.py`, `mapdesk_tools.py`, `dashboard/plugin_api.py`, `plugin.yaml` (wiring) — workstream W
- `desktop/map.html`, `desktop/map_keyless.html` (renderer) — workstream R
- `desktop/plugin.js` (pane) — workstream P

Repo root: the `hermes-mapdesk` checkout (paths below are relative to it).
Test venv: `.venv/Scripts/python.exe` (has shapely, fastapi, httpx, PyYAML, pyproj, openpyxl, pyshp).
Run Python tests: `PYTHONPATH='' .venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -q`
Existing modules import each other with `importlib.util.spec_from_file_location` via
`mapdesk_runtime._load`; follow the existing `tests/test_*.py` import style.

House rules (every workstream):
- Validation-first style: reject early with a single clear `ValueError` message,
  as in `mapdesk_core.py` / `mapdesk_sources.py`. Attributes are untrusted data.
- No new third-party imports beyond: shapely, pyproj, openpyxl, pyshp (Python) —
  stdlib otherwise. Renderer guests may pin external map libraries (below).
- Never weaken the iframe sandbox, never log tokens, never accept `sk.` tokens.
- Tests: real behavior, no mocks of the module under test. Match existing style.
- Do not run `scripts/stage_dev.py` or edit files outside your workstream.

## 1. Layer dict (stored JSON, `mapdesk_core.WorkspaceStore`)

Existing keys unchanged: `id`, `name`, `geojson`, `source`, `style`, `visible`,
`feature_count`, `fields`. New keys:

- `order`: int. Unique ascending draw order. Assigned at add time = max(existing
  orders)+1 (0 for first). `state()` returns layers sorted by `(order, rowid)`.
- `data_revision`: int. Equal to the workspace revision after the layer's INSERT.
  Never changes afterwards (geometry is immutable in place). Used by the pane to
  know when to re-fetch a layer's geojson.

`style` gains keys (all optional, with defaults shown):

```json
{"color": "#3388ff", "opacity": 0.8, "color_by": null,
 "class_method": "auto", "class_count": 5, "palette": "classic",
 "label_field": null, "line_width": 2.2, "circle_radius": 5}
```

- `class_method`: `"auto" | "unique" | "quantile" | "jenks" | "equal" | "continuous"`.
- `class_count`: int 3..7 (ignored by `unique`/`continuous`).
- `palette`: `"classic" | "okabe" | "viridis" | "sunset"`.
- `label_field`: string naming a property, or null.
- `line_width`: number 0.5..8. `circle_radius`: number 2..15.

`_style()` validation must accept exactly the old keys plus these, with those
enums/ranges. Update `_style` accordingly (workstream W).

## 2. Selection shape

`selection = {"layer_id": str|null, "feature_ids": [...], "bbox": [w,s,e,n]|null,
"radius": {"center": [lng, lat], "meters": number}|null}`

Backend `set_selection(layer_id, feature_ids=None, bbox=None, radius=None,
spatial=None, invert=False)`:

- `feature_ids` / `bbox` / `radius` / `spatial` are mutually exclusive.
- `radius`: geodesic circle — 64-gon built with `pyproj.Geod` around
  `center` at `meters` (10..5_000_000), intersected against the layer.
- `spatial`: `{"op": "intersects"|"within"|"contains", "layer_id": target}` —
  select features of the active layer against the (union of) geometries of the
  target layer, using shapely predicates. Target layer must exist.
- `invert: true` with `feature_ids`: select the complement of those ids in the
  layer. `invert` requires `layer_id` and `feature_ids`.
- Stored selection keeps whichever locator was used (`bbox` or `radius`) plus
  resolved `feature_ids`.

## 3. Query conditions (workstream W core; used by `query` action + tool)

```json
{"layer_id": "id", "where": [{"field": "FUEL", "op": "eq", "value": "NG"}],
 "spatial": {"op": "intersects", "layer_id": "other"}, "limit": 200}
```

- Condition ops: `eq, ne, gt, gte, lt, lte, in, like, isnull`.
  `value`: str | number | bool | null | array (for `in`). `like` = case-insensitive
  substring. Comparisons: if both sides coerce finite-number → numeric compare,
  else string compare (eq/ne only); `gt/gte/lt/lte` on non-numeric fall back to
  string compare. `isnull` value must be null. All `where` conditions ANDed.
- `field` must exist in the layer's `fields`, else `ValueError("Unknown query field")`.
- Result: `{"layer_id", "total": exact_count, "feature_ids": first <=limit (default
  200, max 1000), "complete": total <= returned}`.

## 4. Analysis ops (workstream O: `mapdesk_ops.py`)

Pure functions, no store access, import shapely/pyproj only. Signatures:

```python
def buffer(geojson, distance_m, by_field=None) -> {"geojson": ..., "partial": bool, "skipped": int}
def dissolve(geojson, by_field=None) -> same shape
def centroid(geojson) -> same shape
def hull(geojson) -> same shape            # one convex hull over all features
def clip(geojson, overlay_geojson) -> same shape   # intersection, keeps attributes
def count_points(polygons_geojson, points_geojson, count_field="point_count") -> same shape
def measure(geojson) -> {"features": [{"id", "length_km"?, "area_km2"?} ...max 100],
                          "total_length_km": float, "total_area_km2": float}
def circle(center_lng_lat, meters) -> geojson Polygon dict   # 64-gon via pyproj.Geod
```

Contracts for all shape-returning ops:

- Input `geojson` is a validated WGS84 FeatureCollection (features have `id`).
- Output features keep original `id` in properties as `_source_id` and get a
  fresh sequential `id`; properties copied through (dissolve/count_points add
  the named field; clip drops attributes only if geometry empty).
- Output must be valid per the same rules as `mapdesk_core._geojson` (closed
  rings, 2/3-element positions, WGS84 bounds). Use shapely `mapping()` and
  `make_valid`/`buffer(0)` cleanups before emitting.
- Budgets: stop emitting features once output would exceed 4,000 features or
  60,000 coordinate positions; set `"partial": true` and `"skipped": n`.
  Measure: geodesic via pyproj.Geod (km² / km).
- Never mutate the input. No I/O. No exceptions other than ValueError with a
  clear message for bad input (wrong geometry types, empty collection, etc.).

## 5. Format parsers + CRS (workstream F: `mapdesk_formats.py`)

```python
def parse_kml(text) -> {"geojson": FC, "source": {...}}
def parse_kmz(base64_str) -> same            # zip; first *.kml entry
def parse_gpx(text) -> same
def parse_xlsx(base64_str, sheet=None) -> same  # openpyxl; needs lat AND lon columns
def parse_shp_zip(base64_str) -> same         # pyshp; .shp+.dbf(+.prj) inside zip
def normalize_geojson(fc) -> {"geojson": FC_4326_no_crs, "original_crs": str|null,
                              "swapped_warning": bool}
```

Contracts:

- Every parser enforces: <=5,000 features, WGS84 lon/lat ranges, raises
  ValueError with one clear message. Output geometry types only Point,
  LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon. All output
  features have a `properties` dict (possibly empty) — no null properties.
- `source` dict per import kind, e.g. `{"kind": "kml", "crs": "EPSG:4326",
  "complete": true, "fetched_at": iso8601utc}`. For files: `{"kind": ..., "url": null,
  "complete": true, "fetched_at": iso8601utc, "note": "Complete uploaded file; ..."}`.
- KML: namespace-agnostic parse (match on local names). Placemark → one
  feature (or more for MultiGeometry parts); `<name>` → property `name`;
  ExtendedData `<Data><value>` and `<SchemaData><SimpleData>` → properties;
  Point → Point, LineString → LineString, Polygon (outerBoundaryIs;
  innerBoundaryIs holes) → Polygon, coordinates "lon,lat[,alt]" whitespace-separated.
- GPX: `wpt` → Point (props: name, desc, ele, time as text), `trk`/`trkseg` →
  LineString (multi-segment track → MultiLineString), `rte` → LineString.
- XLSX: header row = first row; unique non-empty headers; require explicit
  latitude/longitude column names in headers (suggest common ones in the error
  if missing: latitude/lat/longitude/lon/lng); cell values → text (None → ""),
  numbers keep numeric type when finite. Same row caps as CSV.
- SHP zip: read via zipfile (reject path traversal entries, reject >64 MiB
  decoded), pyshp from extracted bytes; shape types 1/3/5/8/11/13/15/18 →
  Point/LineString/Polygon/(Z variants); polygons: rings → shell + holes
  (shapely orientation not required, but rings must close); DBF attributes →
  properties (dates → ISO text, 'D'/'F'/'N' typed properly); `.prj` present and
  not WGS84 → reproject with pyproj and record `original_crs`; missing shapes
  (null geometry records) are skipped and counted in `source["skipped"]`.
- `normalize_geojson(fc)`: accepts a FeatureCollection that may carry a legacy
  `"crs"` member. If name is EPSG:4326 / CRS84 → strip, done. Other EPSG/URN →
  reproject all coordinates to 4326 via pyproj Transformer (always_xy=True),
  strip crs. Unknown CRS name → ValueError naming it. `swapped_warning`: true
  when >=90% of "longitude" values are within ±90 AND (>=1 "latitude" value
  beyond ±90 OR median |latitude| > median |longitude|) — heuristic only.
  Returns geometry untouched when no crs member and no warning trigger.
- KMZ/XLSX/SHP inputs are base64 str; validate base64 strictly (stdlib base64,
  reject non-strict decode errors with a clear message), size cap 8 MiB decoded
  (base64 inflates ~4/3; the /command endpoint caps at 12 MiB JSON).

## 6. ArcGIS where + pagination + geocode (workstream S: `mapdesk_sources.py`)

`fetch_arcgis(url, bbox, limit=1000, where=None, max_features=5000)`:

- `where`: optional string. Validate BEFORE querying: fetch layer metadata
  `<url>?f=json` (same pinned HTTPS connection class, 10 MiB cap) and require
  every field name token in `where` to exist in `metadata["fields"][*]["name"]`.
  Grammar (regex-validated, then passed through to the service): field tokens
  ([A-Za-z_][A-Za-z0-9_]*), operators `= <> > >= < <= LIKE IN`, single-quoted
  string literals (with '' escaping), numbers, AND/OR (case-insensitive),
  balanced parentheses. Field names compared case-insensitively to metadata and
  rewritten to the service's casing. Reject anything else with a message that
  shows the allowed grammar.
- Pagination: request pages with `resultOffset` increments of
  `min(service maxRecordCount, 1000)` until a page returns fewer than the page
  size, `exceededTransferLimit` is falsy, or `max_features` (1..5000) reached.
  Geometry+where applied to every page. `complete` = returned == total_count
  (count query uses the same where). Source metadata gains `where`,
  `requested_max` and keeps existing fields; if truncated by max_features,
  `complete=false`, `sample=true`.
- Keep all existing SSRF/TLS pinning; metadata request reuses it.

`geocode_batch(addresses)`:

- Input list of strings, 1..25, each <=300 chars; reject otherwise.
- Nominatim `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=...`
  via the pinned-public-HTTPS approach adapted to this host (resolve public,
  no redirects... NOTE: nominatim.openstreetmap.org is a known public host —
  use `http.client.HTTPSConnection` directly, no proxy reuse; 20 s timeout;
  UA header `MapDesk/0.2 (Hermes Desktop plugin; GIS import)` plus
  `Accept-Language: en`; encode q with urlencode).
- Rate limit: module-level monotonic-clock guard, >=1.1 s between requests.
- Results: `{"results": [{"lon": float, "lat": float} | null, ...]}` aligned
  to input order; null = not found / error for that address. Persist a
  JSON cache `geocode_cache.json` in the plugin data dir (create via the same
  plugin_data_dir import used by mapdesk_runtime — accept a `cache_dir: Path`
  parameter instead, default None = no cache) mapping address → result, so
  repeat imports are free. Cache read/write tolerant (bad JSON → start fresh).
- Never raise for a single bad address; raise ValueError only for input-shape
  errors. Time budget: fail remaining with nulls if the batch exceeds 60 s.

## 7. Runtime actions (workstream W: `mapdesk_runtime.execute`)

Existing actions unchanged in shape. New:

- `summary` → workspace metadata + selection + layers WITHOUT `geojson`
  (each layer keeps id, name, order, data_revision, style, visible,
  feature_count, fields, source). Same shape as `state` otherwise.
- `layer` `{layer_id}` → `{"layer_id", "data_revision", "geojson"}`.
- `import_kml` `{name, text}` / `import_kmz` `{name, base64}` /
  `import_gpx` `{name, text}` / `import_xlsx` `{name, base64, sheet?}` /
  `import_shp` `{name, base64}` — delegate to `mapdesk_formats`, then
  `store.add_layer` (which validates). For kml/kmz/gpx/xlsx/shp the source dict
  comes from the parser. `import_geojson` now runs `normalize_geojson` first and
  stores `original_crs` + `swapped_warning` into source.
- `geocode_batch` `{addresses}` → delegates to sources.
- `reorder` `{layer_ids: [...]}` — exact full ordered list; unknown or missing
  ids → ValueError("Layer IDs must exactly match the workspace layers").
- `query` `{layer_id, where?, spatial?, limit?}` → contract in §3.
- `analyze` `{op, layer_id, params?, output_name?}` with
  `op in buffer|dissolve|centroid|hull|clip|count_points|measure`:
  reads the layer's geojson, runs `mapdesk_ops`, and for all ops except
  `measure` stores the result via `store.add_layer(name=output_name or
  f"{layer.name} · {op}", geojson, source={"kind": "analysis", "op": op,
  "from_layer": layer_id, "params": params, "complete": not partial,
  "fetched_at": now_iso})`. `measure` returns the ops result directly.
  `buffer` params `{"distance_m": number 1..100_000}`; `dissolve` params
  `{"by_field": str|null}`; `clip`/`count_points` params name the other layer
  (`overlay_layer_id` / `points_layer_id`), whose geojson is read from the store.
- `select` extended per §2. `selection` unchanged.

## 8. REST endpoints (workstream W: `dashboard/plugin_api.py`)

- `GET /status` unchanged. `GET /bootstrap` → `{"token": pk_or_empty, "basemap":
  "mapbox" if token else "keyless"}` (no-store).
- `GET /renderer` → `{"html": <file text>, "kind": "mapbox"|"keyless"}` chosen
  by token presence — NEVER by a load failure. Serves `desktop/map.html` or
  `desktop/map_keyless.html`.
- `GET /state` unchanged (full; used for workspace snapshots).
- `GET /summary` → `runtime.execute('summary', {})`.
- `GET /layer/{layer_id}` → 400 on unknown layer.
- `POST /geocode` `{addresses: [...]}` → delegates; 400 on shape errors.
- `POST /command` unchanged envelope; add the new actions.
- Bump the command JSON cap to 16 MiB (binary imports arrive base64).

`plugin.yaml`: version `0.2.0`; python_dependencies add `"pyproj>=3.7,<4"`,
`"openpyxl>=3.1,<4"`, `"pyshp>=2.3,<3"`.

## 9. Agent tool schemas (workstream W: `mapdesk_tools.py`)

- `mapdesk_import`: `kind` enum gains `kml, kmz, gpx, xlsx, shp`. New optional
  params: `base64` (binary kinds), `sheet` (xlsx), `where` (arcgis string),
  and `limit` max becomes 5000 (arcgis max_features). All descriptions keep the
  untrusted-data and provenance language style.
- `mapdesk_query` (new): `{layer_id, where: [{field, op, value}], spatial:
  {op, layer_id}, limit}` → contract §3. Description must say: read-only, ids
  not a draw filter, set selection explicitly via mapdesk_selection if needed.
- `mapdesk_analyze` (new): `{op, layer_id, overlay_layer_id?, points_layer_id?,
  distance_m?, by_field?, output_name?}` → creates a layer (measure returns
  stats). Description warns: outputs are new layers; removal is permanent.
- `mapdesk_selection`: gains `radius {center, meters}`, `spatial {op, layer_id}`,
  `invert` boolean.
- `mapdesk_layer`: `changes.style` gains the §1 style keys; new action
  `reorder` taking `layer_ids`.

## 10. Renderer guests (workstream R)

Two standalone guest files with the SAME message protocol and the same
feature set (duplication accepted; both must implement everything below):

- `desktop/map.html` — Mapbox GL JS 3.33.0 (keep the verified classic-worker
  hook and existing SRI pins exactly as-is; add features around it).
- `desktop/map_keyless.html` — MapLibre GL JS (pin an exact recent v5 from
  unpkg or jsdelivr, compute SRI hashes yourself with `curl | openssl dgst
  -sha384 -binary | base64`), style `https://tiles.openfreemap.org/styles/dark_matter`
  (verify the style URL exists with curl first; if that style name is wrong,
  pick from OpenFreeMap's published styles and record the choice in a comment).
  No token. MapLibre v4+ has no web worker — do not add workerClass. Keep
  `attributionControl: true`.

Both guests:

- PNG capture reads the canvas inside a `render` callback after `triggerRepaint()`; no `preserveDrawingBuffer` (MapLibre 5 ignores it and it costs frame time).
- Add `ScaleControl` (bottom-left) and a bottom-right coordinate readout bar:
  `mousemove` → `12.3456°, -97.8765°` (4 decimals) + zoom; also a keyboard focus
  target is not required (guest is decorative chrome).
- Classification v2 in `classify(layer)`:
  - Read `style.class_method` (`auto` keeps today's heuristic), `class_count`
    (3..7, default 5), `palette` key → color array:
    - `classic`: `['#6ed8d0','#e7ad67','#aab1ff','#ef86a4','#8ac887','#f2d37e','#72b8f0','#ba9bce']`
    - `okabe`: `['#0072B2','#E69F00','#009E73','#D55E00','#CC79A7','#56B4E9','#F0E442','#111111']`
    - `viridis`: `['#440154','#46327E','#365C8D','#277F8E','#1FA187','#4AC16B','#A0DA39','#FDE725']`
    - `sunset`: `['#2C7BB6','#00A6CA','#79D1A4','#C9E892','#F4C46B','#ED8A4C','#D7191C','#7B0D2B']`
  - `unique`: first 24 distinct values (today's behavior), fallback color `#88949f`.
  - `quantile` / `equal` / `jenks` (natural breaks; implement classic Jenks
    algorithm, cap at 2,000 numeric values — beyond that fall back to quantile)
    → `class_count` steps with palette colors, legend shows class ranges +
    counts. Missing/blank values → fallback color, legend row `(blank)`.
  - `continuous`: mapbox `interpolate` expression over [min, max] ramping
    through the palette; legend shows a gradient bar with min/max labels.
- Per-layer styling honors `line_width`, `circle_radius`; polygons keep
  `fill-opacity` at `opacity*0.45` and outlines colored `color` at `line_width`.
- Labels: when `style.label_field` is set and the field exists, add a symbol
  layer per data layer: `text-field ['get', label_field]`, size 11, offset
  [0, 0.8], halo (background color), visibility follows `visible`. Mapbox guest
  font: `['DIN Offc Pro Medium', 'Arial Unicode MS Regular']`; keyless guest:
  `['Noto Sans Regular']`. Hidden when label_field is null.
- Draw order: data layers must be added to the style in the order of
  `state.layers` (which is now sorted by `order`); on reorder, remove and
  re-add mapdesk layers to match the new sequence.
- GeoJSON cache: render(state) receives layers that may omit `geojson` (slim
  layers carry `data_revision`). Guest keeps `Map` from layer.id → {revision,
  geojson}. Missing geojson + cache hit → use cache. Cache key: layer.id +
  `client_revision ?? data_revision`. Never request data from the parent.
- Hover: `mousemove` → small tooltip (div near cursor) with the topmost
  feature's layer name + `name`-like property (first of: name, title, label,
  id) + up to 3 property rows; hidden when nothing hovered. Click behavior
  unchanged (popup + emit select).
- Measure mode: parent toggles via message `mapdesk:mode {mode:
  'select'|'measure'}`. In measure mode: clicks add vertices to a temporary
  GeoJSON source; a running label near the cursor shows total geodesic length
  (haversine, meters/km); double-click or Enter finishes and shows area
  (spherical excess or shoelace on mercator approx — document choice in a
  comment) if >=3 vertices; Escape clears; switching back to select mode
  clears. No emits needed. Keep the #hint text updated per mode.
- Radius select: in select mode, Ctrl+drag draws a circle (center = mousedown,
  radius = distance to cursor) in the #box style; on mouseup emits
  `mapdesk:select {layer_id, radius: {center: [lng, lat], meters}}`.
  Shift+drag box select unchanged. Escape cancels both.
- PNG export: on `mapdesk:export-png` message, reply
  `emit('mapdesk:png', {dataUrl: <canvas.toDataURL('image/png')>})` once the
  map has rendered (queue until 'load' + first render if needed).
- Legend (drawLegend) extended: shows the classification of the topmost
  visible `color_by` layer (as today) plus for `continuous` a gradient bar;
  note line for class method (e.g. "Quantile classes, not a continuous ramp."
  / "Natural breaks (Jenks), N classes.").
- Keep every existing emit type and shape identical (`mapdesk:ready`,
  `mapdesk:loaded`, `mapdesk:needs-token`, `mapdesk:error`,
  `mapdesk:viewport`, `mapdesk:select`, `mapdesk:rendered`). The keyless
  guest emits the same set (with `mapdesk:loaded` on its own successful load;
  it never emits `needs-token`).

Update `tests/test_renderer.py` (and any browser test that pins guest
behavior) so the suite covers: classification methods (feed a synthetic state),
cache-with-slim-layers, and both guests' protocol surface. Keep browser/live
tests opt-in (they need Edge); `test_renderer.py` is the required gate.

## 11. Pane (workstream P: `desktop/plugin.js`)

Keep the existing architecture (SandboxedFrame, lifecycle, scoped commands).
Changes:

1. Perf state model: poll `GET /summary` (5 s) instead of `/state`; fetch the
   ACTIVE layer's geojson via `GET /layer/{id}` with a React Query key
   `['mapdesk-layer', id, data_revision]` (cached per revision). Only the
   active layer (selection/table target) needs full data. `GET /state` is
   still used for workspace snapshots (saveMapSnapshot) and restoreMap.
   The renderer state push sends slim layers for non-active layers (omit
   geojson) and full geojson for the active layer — guests cache (§10).
2. Per-layer filter: `filters` = {layerId: string} state. The AttributeTable
   uses the active layer's filter; switching the active layer does NOT carry
   the filter over. Each layer keeps its own filter while it is active.
   shownState replaces only the active layer's features and sets
   `client_revision: 'f'+hash` on that layer so the guest updates. Add a
   "Clear filter" button next to the filter input. Keep the "N of M on the
   map" line semantics.
3. Import intake: drag-and-drop onto the pane (highlight on dragover) and a
   "Paste data" details box (textarea; detect GeoJSON JSON / CSV text /
   https://…/FeatureServer|MapServer/N URL) reusing the existing draft/
   review flow. Route by extension: .kml/.gpx/.geojson/.json/.csv as text
   drafts; .kmz/.xlsx/.zip as binary drafts (ArrayBuffer → base64 →
   `import_kmz`/`import_xlsx`/`import_shp`; for .zip require a note that it
   must contain .shp+.dbf). After any import: auto-suggest `color_by` —
   pick the top-ranked field from the existing `styleFields` ranking with
   2..24 distinct values, call update_layer once, and report it in the
   notice ("Imported X · colored by Y").
4. Geocode UI (CSV drafts): if the chosen file has no lat/lon columns,
   offer an "Address column" select (or paste flow). On import: loop batches
   of 25 via `POST /geocode`, showing "Geocoding 25/240…" progress with a
   Cancel button; assemble a new CSV text with columns `mapdesk_lon`,
   `mapdesk_lat` for resolved rows only (report skipped count), then run the
   normal `import_csv`. Cancel = stop cleanly, import nothing.
5. Source catalog: a "Public data" section with a small curated list. Each
   entry {label, url, where?, note} prefills the ArcGIS form (url + where).
   VERIFY each URL with curl (a count query, `?where=...&returnCountOnly=true&f=json`)
   before shipping it; ship only entries that returned a count. Candidates
   (verify, drop failures): TIGERweb State_County MapServer 0
   (`https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0`,
   where `STATE='48'` for Texas counties); TIGERweb States
   (find the right layer); HIFLD (find a stable public FeatureServer, e.g.
   substations or pipelines — verify); keep an editable custom row as today.
   The ArcGIS form gains a "Where (optional)" text input validated
   client-side as non-empty plain string (backend validates grammar).
6. Layer cards: add Rename (inline input on a details/summary), ▲/▼ reorder
   buttons (call `reorder` with the new full id order), Export GeoJSON
   (fetch `/layer/{id}`, download all features), Export CSV (client-side
   from the same fetch; properties flattened), and a Labels field select
   (existing fieldMenu) + classification controls (class_method select:
   Auto/Unique/Quantile/Natural breaks/Equal interval/Continuous; class_count
   select 3–7; palette select with the four names).
7. Saved maps: add Rename (prompt-free inline input) and Delete (rewrite the
   index without the entry; the archive file is left on disk — note that in
   the confirm copy) on each entry.
8. Undo toast: `openBlank` returns the archived summary; render a dismissible
   banner "Blank map started — previous map archived. [Undo]" for 30 s;
   Undo calls restoreMap(archived.id). If no archive was created, no banner.
9. Map area header: buttons "Select" / "Measure" (post `mapdesk:mode`),
   "Snapshot PNG" (post `mapdesk:export-png`, download the returned dataUrl),
   "Export workspace" (GET /state → JSON download). Keep existing Zoom etc.
10. Table: add "Invert selection" (compute complement client-side from the
    active layer's ids, set selection) and "Select by location" (Select
    component: target layer + op intersects/within/contains → `select`
    action with `spatial`).
11. Update hint copy: "Click to inspect · Shift-drag box select · Ctrl-drag
    radius select · Measure mode in the pane header".
12. Desktop-plugin SDK constraints (HARD rules — the file loads uncompiled):
    - Only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` imports.
    - No JSX syntax; `jsx(Component, props, key)` — key is the THIRD argument.
    - No native `<select>`; use SDK Select components (sentinel for empty).
    - Theme vars only, never hardcoded colors; keep the existing border/MUTED style objects.
    - Handlers read state imperatively via refs/atoms, never render closures.
    - `host.notify`, `host.navigate`, `useQuery`/`useQueryClient` as today.
    - Do not change the `openBlank` contract (archive → verify-cleared → open).
13. After editing, run and pass: `npm --prefix dev run test:desktop` and
    `npm --prefix dev run test:imports` (update those tests only if helper
    behavior changed — prefer keeping all existing assertions green).

## 12. Definition of done (integration, run by architect)

- All Python tests green (`unittest discover tests`), desktop contract green,
  import helpers green, renderer tests green.
- `hermes plugins validate .` passes in a disposable environment.
- Guests verified in an isolated `allow-scripts` opaque iframe (Edge harness)
  AND live in the installed pane: basemap renders with and without a token,
  a GeoJSON layer draws, select/measure/classify/labels respond.
- Skills `hermes-mapdesk` and `hermes-desktop-plugins` updated for the new
  contracts (architect).
