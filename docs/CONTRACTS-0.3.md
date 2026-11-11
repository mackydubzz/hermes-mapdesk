# MapDesk 0.3 upgrade contracts (Tier-1 renderer, exports, live sync)

Binding addendum to docs/CONTRACTS.md. Same house rules (validation-first
ValueErrors, no new deps beyond pyshp/pyproj/shapely/openpyxl, tests required,
no commits, touch ONLY your files). Workstreams:

- WS-R renderer: `desktop/map.html`, `desktop/map_keyless.html`, `tests/test_renderer.py`
- WS-E exports: `mapdesk_exports.py` (new), `mapdesk_runtime.py`, `tests/test_exports.py` (new), `README.md` (disclosure section only)
- WS-X live sync: `dashboard/plugin_api.py`, `tests/test_api.py`
- WS-P pane: `desktop/plugin.js`, `tests/desktop-contract.cjs`

Repo: the `hermes-mapdesk` checkout (paths below are relative to its root).
Venv: `.venv/Scripts/python.exe` (shapely, pyproj, openpyxl, pyshp, fastapi,
httpx, PyYAML). Python tests:
`PYTHONPATH='' .venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -q`
Node: `npm --prefix dev run test:desktop` / `test:imports`.

## E1. Export actions (WS-E)

New module `mapdesk_exports.py` (pure stdlib + pyshp; no I/O beyond what is
passed in). New runtime actions (WS-E wires them in `mapdesk_runtime.execute`,
reachable through the existing POST /command; NO agent tool changes):

- `export_gpkg` `{layer_ids: [...]}`  → `{"base64": str, "filename": str,
  "layers": [names], "feature_count": int, "bytes": int}`
- `export_shp` `{layer_id: str}` → same result shape
- `export_kmz` `{layer_ids: [...]}` → same result shape

Shared contract:

- `layer_ids` must be existing layer ids (unknown → ValueError naming it);
  empty list → ValueError("Select at least one layer").
- Input geojson comes from the store (runtime reads each layer's geojson and
  passes it in; `mapdesk_exports` never touches the store or sqlite).
- Whole result must fit: if the base64 string would exceed 12,000,000 chars,
  raise ValueError("Export exceeds 12 MB; remove layers or filter first").
- Filename: sanitize the workspace name (`[A-Za-z0-9._ -]`, spaces→underscores,
  max 60 chars) + `-YYYYMMDD-HHMM` + extension. Runtime supplies the name; the
  module takes `filename_stem` as an argument.
- Provenance sidecar, ALWAYS included: `mapdesk_provenance.json` — a JSON
  object `{"exported_at": iso8601utc, "workspace": name, "layers": [{name,
  source: <the layer's full source dict>, feature_count, fields}]}`. In GPKG
  it is ALSO written as a standard-ish `gpkg_metadata` row (see below); in
  the SHP zip and KMZ it is a file entry.

### GeoPackage writer (`write_gpkg(layers, provenance_json, filename_stem) -> (bytes, names)`)

- `layers`: ordered list of `{name, geojson}` dicts (runtime assembles).
- Standard tables: `gpkg_spatial_ref_sys` (rows for 4326 and -1, correct WKT),
  `gpkg_contents` (table_name, data_type 'features', srs_id 4326, minx/miny/
  maxx/maxy from the layer's bounds, last_change iso8601 Z, identifier=name),
  `gpkg_geometry_columns` per feature table.
- One feature table per layer: sanitized unique snake_case table_name
  (`re.sub(r'[^a-z0-9]+','_', name.lower()).strip('_')`, suffix `_2`, `_3` on
  collision, max 60 chars). Columns: `fid INTEGER PRIMARY KEY AUTOINCREMENT`,
  `geom BLOB`, then a column per property field. Property column types:
  int→INTEGER, finite float→DOUBLE/REAL, bool→INTEGER, else TEXT (dates are
  text). Column names sanitized like table names, unique, keep a mapping.
- Geometry blobs: GeoPackageBinary — magic `GP`, version 0, flags byte
  (envelope code 1 = 4-value xy envelope, little-endian header), srs_id int32
  LE, then xy envelope doubles LE, then WKB (little-endian, shapely
  `wkb.dumps(g, byte_order=1)`). Build geometry with shapely `shape()`;
  points→POINT, polygons→POLYGON, mixed types per layer are allowed
  (GPNGEOMETRY has no per-table type constraint beyond declared geometry type
  name — declare `GEOMETRY` in gpkg_geometry_columns for mixed layers, else
  the concrete type).
- `gpkg_metadata` + `gpkg_metadata_reference`: one row, ISO-19139 not
  required — write `md_scope='dataset'`, `metadata='{"iso19139": false}'`?
  NO — keep it simple and honest: write the provenance JSON as a row in a
  `gpkg_metadata` table (md_standard_uri
  'https://hermes-mapdesk.local/provenance', mime 'application/json') and a
  reference row. QGIS ignores unknown metadata tables harmlessly; verify the
  file opens with OGR if available in the venv (`fiona`/`gdal` are NOT
  dependencies — instead assert structurally: sqlite table list + a WKB
  round-trip through shapely).
- Property values must round-trip: str stays str, int stays int, float stays
  float, bool stays int (GPKG has no bool), None → NULL. Only JSON-safe
  values exist (store guarantees).

### Zipped shapefile writer (`write_shp(name, geojson, provenance_json, filename_stem)`)

- pyshp `Writer`. Split by geometry type: one .shp set per type present
  (suffixes `_point`, `_line`, `_polygon`; single type → no suffix).
  Unknown/GeometryCollection → skip those features, count them, include
  `skipped` in the returned names list.
- DBF fields: property keys truncated to 10 chars, uppercased, deduplicated;
  types: int→N(18,0), finite float→N(20,10) or F(24,12), bool→L, else C(254)
  (truncate values to 254). A field named `FID` in properties collides with
  nothing (shapefile has no FID column — fine).
- `.prj` with WGS84 WKT (the standard 4326 ESRI WKT string). Zip entries:
  .shp/.shx/.dbf/.prj per set + `mapdesk_provenance.json`. Use
  `zipfile.ZipFile` in memory, deflate.

### KMZ writer (`write_kmz(layers, provenance_json, filename_stem)`)

- KML 2.2 doc: `<Document>` with one `<Folder>` per layer; `<Style>` per
  layer from its style (line/polygon color from `#RRGGBB` → KML `aabbggrr`
  with opacity from the layer style or 0.8 default). `<Placemark>` per
  feature: `<name>` from the first of name/label-ish property or index;
  `<ExtendedData><Data name=…><value>` for EVERY property (stringify;
  escape XML); Point/LineString/Polygon geometry (Polygon: outerBoundaryIs
  only — holes flattened into the outer ring list is NOT acceptable; write
  innerBoundaryIs when present). GeometryCollections → one Placemark per part.
- Zip: single `doc.kml` + `mapdesk_provenance.json`, deflate.

### Runtime wiring

- Actions read layers from the store, assemble inputs, call the module,
  base64-encode (standard `base64.b64encode`), and return the result shape.
- Tests (`tests/test_exports.py`, real behavior): GPKG opens as sqlite with
  the standard tables, a WKB round-trip via shapely reproduces coordinates,
  provenance row present; SHP zip read back with pyshp reproduces attributes
  and geometry counts (mixed-type layer → two sets); KMZ read back with
  zipfile/ElementTree reproduces placemarks, properties escaped properly
  (ampersand case); 12 MB cap rejection; unknown layer ValueError; empty
  list ValueError; filename sanitization.

## X1. Live sync (WS-X) — `dashboard/plugin_api.py` only

- Add `GET ws /events`: an asyncio websocket endpoint in the plugin router
  (FastAPI `@router.websocket('/events')`). On connect send
  `{"type":"hello","revision":<current>}`. Then push
  `{"type":"revision","revision":N}` whenever the workspace changes.
- Change detection: in-process pub/sub. After a successful mutating
  `/command` (any execute() that did not raise), read the current revision
  (add `runtime().execute('summary', {})`-lite or a cheap helper — the store's
  state() metadata; prefer calling `execute('status', {})` ONCE per command
  is heavy; instead add a small `_current_revision()` that opens the store and
  reads workspace metadata — via the existing runtime module's `_store()`),
  then `asyncio`-safe broadcast to all connected sockets (keep a module-level
  set of websockets; guard with try/except; non-asyncio contexts are not a
  concern — this router runs in the plugin host's event loop).
- Do NOT change any other endpoint's behavior. /command must not slow down
  measurably: broadcast is fire-and-forget (`asyncio.create_task` or direct
  `send_json` coroutines gathered).
- Tests (`tests/test_api.py` extensions): starlette TestClient websocket
  connect receives hello; a POST /command (e.g. save name) results in a
  revision push within a short window; error commands push nothing.

## R1. Renderer guests (WS-R) — both guests, identical protocol

New parent→guest messages (same version/channel envelope as §10 of the main
contracts; guests ignore unknown message types gracefully):

- `mapdesk:init` gains optional `{basemap?: 'dark'|'streets'|'satellite',
  terrain?: bool}` — the style MUST be chosen at map construction, so the
  pane sends the stored preference with init. Defaults: dark, terrain off.
- `mapdesk:fit {bbox:[w,s,e,n]}` → `map.fitBounds([[w,s],[e,n]],
  {padding:24, maxZoom:16, linear:false})`. Queue until loaded like other
  actions. (The camera that results is persisted by the existing moveend
  → viewport write-back; the pane no longer computes zoom itself.)
- `mapdesk:style {basemap}` → map.setStyle(newStyle, {diff:false}); on the
  style load completing, re-run the existing render(state) so data layers and
  the legend survive, and re-apply terrain if it was on.
- `mapdesk:terrain {on}` → add/enable or disable terrain:
  - Mapbox guest: raster-dem source `mapbox-dem` with
    `mapbox://mapbox.mapbox-terrain-dem-v1` (encoding mapbox, tileSize 512,
    maxzoom 14) + `map.setTerrain({'source':'mapbox-dem'})` + a subtle sky
    via the v3 sky API (`map.setSky` or the constructor sky object — use the
    documented v3 approach). Disable: `setTerrain(null)`.
  - Keyless guest: raster-dem source from AWS terrain tiles
    `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`,
    `encoding:'terrarium'`, tileSize 512, maxzoom 13. No sky (MapLibre 5
    sky/atmosphere: use the documented `map.setSky` if available, else skip —
    terrain alone is the feature).
- `mapdesk:heatmap {on}` → when on, for every VISIBLE data source whose
  layer's features include points, add `*-heatmap` layers (type 'heatmap',
  below the data layers; weight 1, radius ['interpolate',['linear'],
  ['zoom'], 0, 8, 12, 24]; color ramp from the active palette: 5 steps at
  0/0.25/0.5/0.75/1 with increasing intensity; opacity from layer opacity).
  When off, remove them. Non-point layers unaffected.
- `mapdesk:cluster {on}` → when on, every point data source must be (re)added
  with `cluster:true, clusterRadius:60, clusterMaxZoom:14`; add per-source
  `*-clusters` (circle, layer color, radius ['step',['zoom'], 12, 8, 18)
  scaled by point_count — ['step', ['get','point_count'], 14, 25, 20, 100,
  28]) and `*-cluster-count` (symbol, white text, size 11) layers; the plain
  circle layer gets filter `['!',['has','point_count']]`. Cluster click →
  `getClusterExpansionZoom` → easeTo. Toggling off restores sources without
  cluster (re-add source with the cached geojson, keep data_revision cache
  keying intact). Draw order preserved.

Basemap styles per guest (verify URLs/styles with curl where applicable):

- Mapbox guest: dark = `mapbox://styles/mapbox/dark-v11` (current),
  light = `mapbox://styles/mapbox/light-v11`,
  streets = `mapbox://styles/mapbox/streets-v12`,
  satellite = `mapbox://styles/mapbox/satellite-streets-v12`.
- Keyless guest: dark = OpenFreeMap `fiord` (current), light = OpenFreeMap
  `positron`, streets = OpenFreeMap
  `liberty` (curl-verify), satellite = an INLINE style object with a single
  raster source `esri-imagery`:
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
  (maxzoom 19, attribution 'Esri, Maxar, Earthstar Geographics') and an empty
  glyph/sprite minimal config that still supports symbol labels — verify a
  labels-carrying raster alternative if trivial; otherwise plain imagery is
  acceptable, say so in a comment. (Shipped with labels: the Esri reference
  services `Reference/World_Transportation` and
  `Reference/World_Boundaries_and_Places` are drawn over the imagery as two
  more raster layers, so the free satellite shows roads, borders and place
  names like the Mapbox one.)

Renderer upgrades (both guests, keeping every existing emit/behavior):

- Zoom-scaled symbology: `line-width` and `circle-radius` become zoom
  interpolations anchored on the style values: line-width =
  ['interpolate',['exponential',1.6],['zoom'],4, max(0.5, line_width*0.45),
  10, line_width, 14, line_width*1.8] (clamped 0.5..8 per the style
  contract). circle-radius = ['interpolate',['exponential',1.6],['zoom'],4,
  max(2, circle_radius*0.5), 10, circle_radius, 13, circle_radius*1.6]
  (clamped 2..15).
- Hover highlight: track the currently hovered topmost DATA feature; use
  `map.setFeatureState({source: sid, id}, {hover:true})` / previous
  `{hover:false}`. Paint expressions read ['feature-state','hover'] to bump
  the color lightness (color-mix is not available in paint — instead bump
  circle-radius by +1.5 and line-width by +1 when hovered. The bump goes
  INSIDE each stop output of the zoom curve, ['+', stop, ['case',
  ['boolean',['feature-state','hover'],false], delta, 0]]: both GL libraries
  only accept ['zoom'] as the input of the top-level step/interpolate, and a
  wrapped curve makes addLayer refuse the layer) and set the canvas cursor
  to 'pointer' over features, '' otherwise. The renderer's per-feature ids
  (index-based) already exist — feature-state works with numeric ids.
- Source perf knobs: data sources get `buffer: 40, tolerance: 2` for lines
  and polygons, `buffer: 64, tolerance: 0.375` for points, and
  `maxzoom: 18` on sources. No source `minzoom`: MapLibre's style spec has
  no such geojson option and refuses the whole source.
- Pitch: NavigationControl gains `visualizePitch: true`.
- 2x snapshot: if `map.setPixelRatio` exists on the guest's map instance
  (guard with typeof), on `mapdesk:export-png` with `{scale:2}`: temporarily
  `setPixelRatio(devicePixelRatio*2)`, wait one render ('render' once after
  resize), `toDataURL`, restore pixel ratio. If the method does not exist,
  fall back to the current 1x path (do not fight it).
- Legend: unchanged. Emissions: unchanged plus nothing new (fit/style/
  terrain/heatmap/cluster are inbound only).

Tests (`tests/test_renderer.py` extensions): message handling for the five
new inbound types on both guests (static or jsdom execution per the file's
existing approach), zoom-interpolation expression construction, hover
case-expression, cluster source config, basemap style maps, inline satellite
style for the keyless guest, pixel-ratio guard behavior.

## P1. Pane (WS-P)

- Frame: replace the local `cameraForBounds`-on-frame path — on a new
  viewport_request, post `mapdesk:fit {bbox}` (keep the import-time
  zoomToLayer behavior but ALSO switch it to post mapdesk:fit with the
  layer's computed bounds — factor a boundsForGeojson helper; moveend
  write-back persists the resulting camera, unchanged).
- Map header controls (inside the existing header row or a second row):
  - Basemap SDK Select (Dark/Streets/Satellite), persisted in plugin storage
    ('basemap'), sent at init (extend the init payload) and via
    mapdesk:style on change; replayed to fresh guests on ready.
  - Terrain toggle button (Terrain 3D), persisted ('terrain'), replayed on
    ready + message on toggle.
  - Heatmap and Cluster toggle buttons (labels 'Heatmap', 'Cluster'), NOT
    persisted (per-session visual modes), disabled when no point layers are
    visible; message on toggle.
  - 'Fit to selection' button: computes the bbox of the selected feature ids
    from the ACTIVE layer data (client-side; empty selection → button
    disabled) and posts mapdesk:fit.
- Export menu per layer card: replace the two Export buttons with a
  details/summary 'Export ▾' containing: GeoJSON (existing), CSV (existing),
  GeoPackage, Shapefile, KMZ. The last three call POST /command with
  export_gpkg/export_shp/export_kmz and the result `{base64, filename}`:
  decode (`atob` → Uint8Array → Blob with the right mime:
  application/geopackage+sqlite3 / application/zip / application/vnd.google-
  earth.kmz) and download via the existing downloadBlob helper. Busy state on
  the layer card during the call; surface backend error strings in the pane
  error area.
- Workspace header: 'Export GeoPackage' (all layers) next to Export workspace.
- Live sync: subscribe once with `ctx.socket('/events', message => { ... })`
  — on a revision message, refetch summary + selection queries (guard: parse
  JSON if the message is a string). KEEP the 5s polling untouched (socket is
  a no-op on remotes; polling is the fallback). Store the disposer if
  returned.
- Keyboard: in the attributes panel, `Escape` in the filter input clears it
  and blurs; pressing `/` while the attributes panel area has focus (but not
  inside a text input) focuses the filter input. Use a ref for the filter
  input; imperative handlers only.
- Contract test additions (`tests/desktop-contract.cjs`): source includes
  `'mapdesk:fit'`, `'mapdesk:style'`, `'mapdesk:terrain'`, `'mapdesk:heatmap'`,
  `'mapdesk:cluster'`, `'export_gpkg'`, `'export_shp'`, `'export_kmz'`,
  `'/events'`, `'Fit to selection'`.
- SDK rules unchanged (no JSX, third-arg keys, SDK selects, theme vars,
  imperative reads). Keep every existing assertion green.

## Definition of done

Full Python suite + desktop contract + import helpers green; exports verified
by read-back tests; websocket verified via TestClient; both guests' protocol
surfaces covered in test_renderer; README export disclosure updated (new
file formats produced locally, no network); then the integrator stages,
runs `hermes plugins doctor mapdesk`, live-verifies, and builds the two O&G
demo workspaces.
