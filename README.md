# MapDesk

A general-purpose GIS workspace for Hermes Desktop, controlled through the same
Hermes conversation: import data, style and query layers, run geodesic analysis
and export, from chat or from the map pane. It works with no account or token.
Version 0.3.0.

## Status and verification

- Full package passes `hermes plugins validate . --install-deps`, including
  isolated registration of all seven tools and the desktop-surface/security checks.
- 217 Python tests pass: real SQLite persistence, geometry intersection,
  schema/coordinate validation, CSV and ArcGIS bounds/SSRF defenses, HTTP/tool
  shared state, and rejection of secret Mapbox tokens at the renderer boundary.
  The indexed and SQLite-JSON fast paths are each tested against the plain
  definition they replaced.
- Both map guests are loaded in a real sandboxed browser by
  `npm --prefix dev run test:draw`, which fails unless points, lines, polygons,
  heatmap and clusters are accepted by the GL library and actually drawn, the
  snapshot PNG is a picture, and an unchanged update does not rebuild tiles.
- Real Mapbox rendering and imported public GIS data were verified inside Hermes
  Desktop. The pinned 3.33.0 renderer uses an opaque iframe and Mapbox's documented
  classic-worker configuration; no host patches or relaxed iframe isolation.
- The pane is driven in an isolated Edge harness with real React/React Query,
  the actual HTTP/SQLite backend and the real keyless map
  (`test:pane`, `test:pane-more`): every import path with its review step,
  styling, selection, measure, basemaps, exports, saved maps, clear and undo.
  It uses a temporary workspace, never a real profile or token. The same
  click-through was also run on the Mapbox map as an opt-in live test. This is
  UI integration evidence, not a catalog installation test.
- Setup distinguishes a stored token from a verified basemap. CSV/GeoJSON imports
  support review, explicit confirmation, cancellation and data-centered zoom.
- Isolated CLI installation copies the correct runtime files without a Node/npm
  sidecar prompt. The supported Desktop reconciliation helper publishes matching
  native assets. Direct-runtime state survives a new process and same-version
  reinstall, and `--enable` is confirmed by reading the resulting config after
  both. An earlier activation failure was caused by the test home's own path
  length (Hermes copies its managed runtime below the home, and Windows refuses
  targets past MAX_PATH); the test now uses a short home. This is a CLI
  acceptance result, not proof of a successful GUI first launch.
- **Not verified:** installation by catalog name or through the app (possible
  only once the plugin is listed), a clean operating-system install, and macOS
  or Linux. Development and testing were done on Windows 11.

## Quick start

Install from this repository (MapDesk is not in the plugin catalog yet):

```bash
hermes plugins install https://github.com/mackydubzz/hermes-mapdesk
```

Hermes installs the declared Python dependencies after asking. Node/npm and
manual Python setup are not needed to use the plugin.

1. In **Settings → Plugins → MapDesk**, enable the agent and Desktop components.
   If Hermes explicitly reports a dependency restart, save your work first.
2. Open the command palette and choose **Open MapDesk**. Every invocation starts
   a blank map: existing layers, selection, workspace name, viewport and unsaved
   drafts are cleared. Mapbox settings are retained. **Setup & help** checks
   storage connectivity and shows the Mapbox setup state separately from rendering.
3. The map works without a token on a keyless OpenFreeMap basemap. For Mapbox,
   put your own dedicated public Mapbox token in the plugin's masked settings
   field, never chat. Use the setup links for the account/token documentation and
   read the usage-cost disclosure below. Return and click **Check setup**: it
   rereads settings and recreates only the map frame, not Hermes or stored data.
   Retrying a map load can incur Mapbox usage. A configured token is not labeled
   a verified map until the renderer reports a successful load.
4. Under **Add your data**, choose a GeoJSON, CSV, KML, KMZ, GPX, XLSX or
   zipped shapefile. Give the dataset a name. For CSV, select latitude and longitude from the actual headers and
   inspect the preview. Common header names are suggested, never silently imported.
   Click **Import dataset**, or **Cancel import** to discard the unsaved preview.
5. The map focuses the imported geometry. **Zoom to layer** brings it back later.
   Existing layers are retained. **Public ArcGIS source** takes any public HTTPS
   layer URL and a WGS84 west/south/east/north query envelope; **Use current map
   view** fills the envelope from what the map shows. No region or data provider
   is built in, and nothing is fetched until you click **Import ArcGIS**.
   The header's **Clear map** button archives the live workspace, then clears
   layers, selection, name and view (Undo banner shown; refuse-on-archive-failure).
   **Clear on session change** (Setup & help, on by default) additionally clears
   the same way automatically whenever a different chat session becomes the
   active one, so a new inquiry starts on a blank map; the archived copy and the
   Undo banner recover it, and the checkbox turns the behavior off.
6. Layers, styling, selection, name and viewport are stored per Hermes profile,
   independently of the current conversation, **until the next Open MapDesk
   command**, which clears the shared workspace (not just its visible layers).
   Switching tabs, Refresh and Check setup do not reset it. A file preview is
   unsaved until confirmed. There is one live workspace per profile; **Save map**
   keeps a snapshot under **Saved maps**, and the reset archives the current
   map first. A reset does not delete source files.
   Reset waits for pending Desktop imports and checks the resulting state before
   opening. It uses sequential backend commands, not one atomic transaction; if
   interrupted, some clearing may already be saved. An error asks you to retry.
   Do not run agent workspace mutations concurrently with a reset.

### Supported inputs and recovery

- GeoJSON FeatureCollection in WGS84 — a legacy `crs` member is reprojected
  automatically (e.g. EPSG:3857); definite swapped longitude/latitude
  (latitudes outside ±90) is corrected and flagged in the layer source.
- KML and KMZ (ExtendedData and schema data become properties), GPX
  (waypoints, routes, tracks), XLSX (explicit latitude/longitude columns),
  and zipped shapefiles (.shp+.dbf, optional .prj with automatic
  reprojection).
- UTF-8 comma-separated CSV with distinct explicit latitude/longitude columns;
  quoted fields, embedded newlines, BOM and textual leading zeros are
  supported. CSVs without coordinate columns can be geocoded through the pane
  by an address column (see the network disclosure below).
- File size at most 10 MiB (8 MiB decoded for base64 binary formats), at most
  5,000 features/rows; the backend also enforces a 100,000-position geometry
  budget and a 10,000,000-byte serialized JSON limit.
- Public ArcGIS layer queries with an optional validated where-clause, up to
  5,000 features across server-side pages, with truncated results labeled
  incomplete. Retrieval time is not a guarantee of source freshness.
- Geometry analysis (geodesic buffer, dissolve, clip, centroid, hull,
  point-in-polygon counts, measurement) runs locally on imported data and
  creates derived layers labeled as analysis output.
- Raster ingestion, routing, isochrones, address geocoding from the agent
  (the pane flow only), and reprojection of arbitrary non-WGS84 CSV are not
  supported by this version.
- If the backend is unavailable, enable both components and use **Retry**.
  If the map fails, check token/account/network status and use **Check setup**.
  Invalid coordinates or malformed files produce an error without adding a layer.

## What this build does

- **Seven agent tools** (`mapdesk_status`, `mapdesk_import`, `mapdesk_layer`,
  `mapdesk_selection`, `mapdesk_query`, `mapdesk_analyze`, `mapdesk_view`)
  and a Desktop pane that share one SQLite workspace per Hermes profile:
  layers, styling, selection, camera and workspace name.
- **Imports:** GeoJSON, coordinate CSV, KML, KMZ, GPX, XLSX, zipped
  shapefiles and bounded public ArcGIS REST layers; points, lines and
  polygons, with source completeness and retrieval metadata kept per layer.
- **Styling:** visibility, color, opacity, draw order, color by a field
  (unique, quantile, natural breaks, equal interval or continuous classes
  with four palettes), labels, line width and circle radius; heatmap and
  point clustering; 3D terrain; a legend.
- **Selection and query:** click, box and radius selection on the map; ID,
  box, radius and layer-relationship (intersects/within/contains) selection
  and attribute queries from the agent; an attribute table with filtering.
- **Analysis:** geodesic buffer, dissolve, clip, centroid, convex hull,
  points-in-polygon counts and length/area measurement, run locally; every
  result except a measurement becomes a new derived layer.
- **Exports from the pane:** a layer as GeoJSON, CSV, GeoPackage, shapefile
  or KMZ; the whole workspace as GeoPackage or JSON; the selection as
  GeoJSON; the map as a PNG.
- **Saved maps:** **Save map** snapshots the workspace (layers included) to
  the plugin's own file store, and **Clear map** archives before clearing.
- **Basemap:** Mapbox with your own public token, or, with no token
  configured, a keyless MapLibre guest on OpenFreeMap tiles. Both offer
  Dark, Light, Streets and Satellite. Satellite shows roads, borders and
  place names over the imagery on both. The free basemaps draw country and
  state lines at every zoom and county lines from zoom 9.
- **Geocoding** of CSV rows by an address column, from the pane only.
- **Sample data:** an empty workspace offers **Load sample data**, three
  small layers generated in the pane and labeled synthetic (not real data).

Limits: one live workspace per profile; at most 5,000 features per layer;
no raster data, routing, isochrones or arbitrary scripts; geocoding is not
available to the agent. A layer can be removed from its card (**Remove
layer**, with confirmation) or by the agent through `mapdesk_layer`; removal
deletes the stored features and cannot be undone. Verification of selection
and lifecycle behavior inside the real Hermes Desktop host is still tracked
separately from the browser tests.

## Performance

Measured on Windows 11 with Hermes's Python 3.14 against a 10-layer, 13.8 MB
workspace (layers of 5,000 points, 1,100 lines and 2,000 polygons):

| Operation | Time |
|---|---|
| Status / summary (the pane polls this) | 2 ms |
| Save the camera | 8 ms |
| Show/hide, rename or restyle a layer | 9 ms |
| Reorder 10 layers | 9 ms |
| Import 5,000 points (GeoJSON) | 0.13 s |
| Import 1,100 lines, 49,500 positions (GeoJSON) | 0.25 s |
| Clip 5,000 points by 2,000 polygons | 0.12 s |
| Buffer 5,000 points by 500 m (output capped at 60,000 positions) | 0.5 s |
| Select 5,000 points by relation to 2,000 polygons | 55 ms |

How: a layer is stored in two parts, its features (megabytes) and everything
else (a few hundred bytes), so status reads and name, visibility, style and
order edits never touch the feature text, and a layer body is served to the
pane as stored text without being parsed and re-encoded. The store opens
without rescanning layers and uses a write-ahead log so the pane's polling
never blocks a write. Clip and spatial selection use a spatial index; buffers
share projections and stop at the output budget. In the pane, each layer body
is sent to the map once, a push that does not change a layer's features does
not rebuild its tiles, and camera moves are saved after a short pause without
being echoed back to the map.

## Local development checks

```bash
# Use a project venv, not pip from an unrelated Python installation.
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe 'shapely>=2.1,<3' 'numpy>=1.26,<3' 'pyproj>=3.7,<4' 'openpyxl>=3.1,<4' 'pyshp>=2.3,<3' 'fastapi>=0.115,<1' 'httpx>=0.28,<1' 'PyYAML>=6,<7'
PYTHONPATH='' .venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -q
npm --prefix dev ci --ignore-scripts
npm --prefix dev run test:desktop
npm --prefix dev run test:browser  # requires Microsoft Edge; no user browser profile is used
npm --prefix dev run test:draw     # real GL load of the keyless guest in Edge; needs network for the library and tiles
npm --prefix dev run test:imports
npm --prefix dev run test:pane      # real pane + HTTP/SQLite + keyless map in Edge: draw, request cost, saved maps, every export
npm --prefix dev run test:pane-more # same harness, every remaining pane control (imports with review, selection, measure, basemaps, restore, undo)
hermes plugins validate . --install-deps
```

Node packages are development-only and live under `dev/`. The plugin root has no
`package.json`: Hermes must not mistake the test suite for a Node runtime sidecar
or ask ordinary users to install npm dependencies.

Optional installation acceptance (run with the current **Hermes Python**, not the
minimal project test venv):

```bash
python scripts/verify_install.py --run
```

This makes a local Git fixture and a disposable Hermes home under `~/.mdt`
(override with `MAPDESK_INSTALL_TEST_ROOT`; on Windows the home must not be
longer than the active Hermes home). It may download declared Python dependencies into
that disposable home; it does not publish a repository or restart the running
app. It checks installed file hashes, actual enabled state (not just exit codes),
fresh-process SQLite persistence, and same-version reinstall persistence. Read
`artifacts/install-acceptance.json` (git-ignored) and the referenced logs: a CLI exit code of
zero can still accompany a refused activation. This is not a marketplace GUI
or clean-operating-system test.

The combined tests use installed Hermes source for SDK contracts; on Windows
it is under `%LOCALAPPDATA%/hermes/hermes-agent`. Clear inherited PYTHONPATH
when it points to a different Python version's compiled packages. The selected
FastAPI/Starlette dev environment currently emits an httpx deprecation warning;
the tests pass, but clean cross-platform packaging/CI is not yet verified.

For a development install, `python scripts/stage_dev.py` copies only enumerated
package files to the default profile's plugin folder. It does not copy databases,
credentials, tests or node_modules. Enable `mapdesk`, then Rescan in Capabilities
→ Plugins (or reload desktop plugins) to reconcile the desktop half. Follow any
reported dependency-restart requirement; a fresh chat is needed for new tools.


## Credentials and network disclosure

Mapbox rendering requires a dedicated public access token entered through the
Hermes-generated plugin settings form. The plugin reads it through Hermes's
profile-scoped secret API; secret tokens are not accepted. No tokens belong in
chat, source control, screenshots, exports or logs.

Setup path: **Settings → Plugins → MapDesk → Mapbox public access token**
(`MAPDESK_MAPBOX_ACCESS_TOKEN`). Use a dedicated, minimum-scope public `pk.` token;
never `sk.`. Hermes's opaque sandbox cannot supply the origin required by
URL-restricted Mapbox tokens. Proceed only with informed acceptance of a
browser-visible token without URL restrictions and its billing exposure. Monitor
and rotate it. The setting is masked/profile-scoped, not a promise of encrypted
storage; neither tokens nor bootstrap responses are persisted by this plugin.
Do not weaken iframe isolation to make a restricted token work.

The renderer fetches version-pinned Mapbox GL JS, styles, fonts and
map tiles from Mapbox. **Without a Mapbox token** the pane instead loads a
separate keyless guest: version-pinned MapLibre GL JS from a public CDN
(unpkg, SRI-verified) and vector basemap tiles, styles and fonts from
OpenFreeMap (OpenStreetMap-derived data); the free path carries no SLA and
shows its own attribution. On the free path the **Satellite** basemap loads
imagery, road and boundary/place-name tiles from Esri's public ArcGIS Online
services (server.arcgisonline.com), and **Terrain 3D** loads elevation tiles
from the public Terrarium set on Amazon S3 (s3.amazonaws.com/elevation-tiles-prod).
Each of these hosts sees the map area being viewed and the usual request
metadata, never your layers. **Address geocoding** (pane CSV import without
coordinate columns) sends one query per row to the public Nominatim service
(nominatim.openstreetmap.org) at a maximum of one request per second, and
caches resolved coordinates locally so repeat imports do not re-query.
Public GIS imports contact the chosen source. Geometry analysis runs
entirely locally. Mapbox service usage may be billed. Feature collections
and workspace metadata stay in Hermes's profile-scoped plugin data directory;
**Saved maps** are files the pane writes through the Desktop plugin file API
into the Desktop app's plugin file store. Deleting an entry in the pane removes
it from the list only; the archive file stays on disk until you remove it;
selected data returned by tools enters the current model conversation. No
separate analytics or LLM service, and no telemetry.

**Exports are produced entirely locally.** The export actions (GeoPackage
`.gpkg`, zipped shapefile `.zip`, and KMZ `.kmz`) assemble the file in memory
from the stored workspace layers and return it as a base64 payload for the
pane to download; no network request is made and no copy is written to disk
by the backend. Each export embeds a `mapdesk_provenance.json` sidecar
(workspace name, export timestamp, and each layer's source metadata, feature
count and fields) — inside the zip for shapefile/KMZ, and as a GeoPackage
`gpkg_metadata` row for GeoPackage.

Development and verification results will be documented as they occur; a passing
catalog gate alone is not a claim of functioning end-to-end integration.
