# MapDesk — post-hackathon roadmap

Successor to the hackathon impressiveness checklist. The 0.3.0 submission
baseline is shipped and verified; this doc tracks what comes next.
Status markers: [x] done, [~] partial, [ ] todo. Effort: S (<2h) M (half day) L (1+ day).

## 1. Shipped baseline — 0.3.0 (verified)

### Capability breadth
- [x] 7 first-class agent tools (status/import/layer/selection/view/query/analyze)
- [x] 7 input paths: GeoJSON (legacy-CRS auto-reproject + swapped-coords fix),
      KML/KMZ, GPX, XLSX, zipped shapefile (.prj reprojection), CSV, ArcGIS REST
- [x] ArcGIS where-clauses (validated grammar, injection-safe) + server-side
      pagination to 5,000 features with honest INCOMPLETE SAMPLE labeling
- [x] Address geocoding (batched Nominatim, 1 rps, cached) — pane flow only
- [x] Geodesic analysis: buffer, dissolve, clip, centroid, hull,
      point-in-polygon counts, length/area measure (pyproj + shapely)
- [x] Attribute + spatial query engine; radius select, select-by-location, invert
- [x] Classification: unique/quantile/Jenks/equal/continuous, 4 palettes,
      class counts, map labels, per-layer line width / circle radius
- [x] Measure mode, hover tooltips, coordinate readout, scale bar, 2x DPI PNG snapshot
- [x] Frame-on-region: geocode places → bbox → `map.fitBounds` fits the camera
      to the pane's real pixels
- [x] Keyless basemap (OpenFreeMap, zero account) + Mapbox guest when token set;
      Dark / Light / Streets / Satellite on both, Terrain 3D, heatmap, clustering
- [x] Public-data catalog with only curl-verified entries (TIGERweb, HIFLD)

### Renderer and performance
- [x] Zoom-scaled line width / circle radius (`interpolate ['zoom']`)
- [x] Hover highlight via `setFeatureState`
- [x] Source perf knobs: per-source `buffer` / `tolerance` / `maxzoom`
      (geojson sources have no `minzoom` in the style spec — do not add one)
- [x] `ctx.socket` revision push with the 5 s HTTP poll kept as fallback
- [x] Revision-gated state transfer (summary poll + per-layer fetch), guest-side
      GeoJSON cache; split feature/meta storage + WAL in SQLite
- [x] Fit-to-selection, Escape/`/` shortcuts in the attributes panel

### Interop exports
- [x] GeoPackage (multi-layer, stdlib writer — no GDAL), zipped shapefile, KMZ,
      GeoJSON, CSV, selection GeoJSON, workspace JSON, PNG
- [x] Provenance sidecar in every binary export (`mapdesk_provenance.json`;
      `gpkg_metadata` row for GeoPackage)

### QoL / workflow
- [x] One-click access: sidebar nav row + full-width route page
- [x] Drag-drop + paste intake; auto color_by suggestion on import
- [x] Per-layer filters, Clear filter, invert, by-location
- [x] Drag-to-resize attributes panel, height remembered
- [x] Saved maps: archive-first clear, rename/delete, restore, Undo banner;
      delete now trashes the archive file (no on-disk leak)
- [x] Session-change auto-clear (preference-gated, archive-first)
- [x] Legacy 0.1 workspace migration on open

### Trust engineering
- [x] SSRF pinning, no redirects, size caps; where-clause grammar validation
- [x] Secrets only via agent.secret_scope, pk-only token gate
- [x] Provenance cards (source URL, fetch time, where, matched vs returned)
- [x] `hermes plugins validate` clean; 217 Python tests; real-browser draw and
      pane suites; desktop-surface + no-core-override clean
- [x] CI: Python + JS suites and a dependency release quarantine
      (`uv --exclude-newer`), green on main

## 2. Housekeeping (this pass)

- [x] CI fix: cross-volume `Move-Item` → `Copy-Item -Recurse -Force` (read-only
      `.git` files broke the move); actions bumped off Node 20
- [x] Saved-map delete trashes the archive file; confirm copy, README
      disclosure and CONTRACTS item 7 updated; pane test asserts no leak
- [x] Checklist retired into this roadmap; USE_CASES version reference fixed
- [x] Stale local branches (`master`, `fix/draw-and-perf`) removed; `main` is
      the only branch

## 3. Build queue (priority order)

### A. Agent-side geocoding (M) — the biggest chat-workflow gap
- [ ] Geocode an address list from chat (a `mapdesk_geocode` tool or an import
      option), reusing the pane's batched-Nominatim + cache path. Today
      address-only CSVs must go through the pane flow.

### B. Renderer leftovers (S)
- [ ] `cooperativeGestures` (scroll-trap protection) + drag-rotate/pitch on
      both guests; NavigationControl already visualizes pitch

### C. Interop stretch (M)
- [ ] Minimal `.qgs` QGIS project export, pre-styled from our palettes —
      completes the hand-off-to-QGIS story beyond GeoPackage

### D. Capacity and workspace model (L)
- [ ] Revisit the 5,000-feature cap and the 3.5 MB archive ceiling (the latter
      currently blocks session auto-clear on big maps)
- [ ] Multiple named workspaces per profile instead of one shared live
      workspace (removes cross-chat interference)

### E. Analysis breadth (M–L)
- [ ] Attribute joins beyond `count_points` (key-field join onto points/polygons)
- [ ] Bigger bets, only on demand: raster ingestion, routing, isochrones

### F. Platform coverage (M)
- [ ] macOS / Linux verification and CI matrix jobs (everything proven on
      Windows 11 so far); clean-OS install check
- [ ] Catalog-name / in-app installation test (possible once listed)

## 4. Release track (owner approval required — standing policy)

- [ ] Catalog PR: `docs/SUBMISSION.md` is drafted; pin the full commit SHA,
      validate at that commit, confirm CI green, open the PR
- [ ] Demo assets: 3–5 min walkthrough video, one-page architecture diagram,
      polished saved-map demos of USE_CASES 1 (corridor screening) and 2
      (orphan-well choropleth)
- [ ] Upstream issue: the MAX_PATH install limit isolated by
      `scripts/verify_install.py` is a Hermes host limit — file it against
      hermes-agent with the evidence

## 5. Known limits (deliberate, documented in README)

One live workspace per profile; 5,000 features / 100,000 positions / 10 MiB
per import; no raster, routing, isochrones or arbitrary scripts; geocoding is
pane-only (until 3A); layer removal is permanent.