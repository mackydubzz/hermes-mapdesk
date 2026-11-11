# MapDesk — hackathon impressiveness checklist

Master list of everything that makes this entry strong: what is already
shipped, what to build next, and how to present it. Status markers: [x] done,
[~] partially done, [ ] todo. Effort: S (<2h) M (half day) L (1+ day).

## 1. Already shipped — the demo baseline

### Capability breadth
- [x] 7 first-class agent tools (status/import/layer/selection/view/query/analyze)
- [x] 7 input paths: GeoJSON (legacy-CRS auto-reproject + swapped-coords fix),
      KML/KMZ, GPX, XLSX, zipped shapefile (.prj reprojection), CSV, ArcGIS REST
- [x] ArcGIS where-clauses (validated grammar, injection-safe) + server-side
      pagination to 5,000 features with honest INCOMPLETE SAMPLE labeling
- [x] Address geocoding (batched Nominatim, 1 rps, cached)
- [x] Geodesic analysis: buffer, dissolve, clip, centroid, hull,
      point-in-polygon counts, length/area measure (pyproj + shapely)
- [x] Attribute + spatial query engine; radius select, select-by-location, invert
- [x] Classification v2: unique/quantile/Jenks/equal/continuous, 4 palettes,
      class counts, map labels, per-layer line width / circle radius
- [x] Measure mode, hover tooltips, coordinate readout, scale bar, PNG snapshot
- [x] Frame-on-region: geocode places → bbox → pane fits camera to real pixels
      (auto-zoom "map the triangle" flow)
- [x] Keyless basemap (OpenFreeMap, zero account) + Mapbox guest when token set
- [x] Public-data catalog with only curl-verified entries (TIGERweb, HIFLD)

### QoL / workflow
- [x] One-click access: sidebar nav row + full-width route page (no palette needed)
- [x] Drag-drop + paste intake; auto color_by suggestion on import
- [x] Per-layer filters (no more cross-layer bleed), Clear filter, invert, by-location
- [x] Drag-to-resize attributes panel, height remembered, rows scale with height
- [x] Saved maps: auto-archive, rename/delete, restore, Undo banner
- [x] Clear map button (archive-then-clear-then-verify, oversized-map fallback)
- [x] Session-change auto-clear (preference-gated, archive-first)
- [x] Revision-gated state transfer (summary poll + per-layer fetch), guest-side
      GeoJSON cache — the perf architecture
- [x] Legacy 0.1 workspace migration on open

### Trust engineering
- [x] SSRF pinning, no redirects, size caps; where-clause grammar validation
- [x] Secrets only via agent.secret_scope (no os.environ), pk-only token gate
- [x] Provenance cards (source URL, fetch time, where, matched vs returned)
- [x] `hermes plugins validate` clean (security scan: safe), 164 Python tests,
      3 JS suites, desktop-surface + no-core-override clean
- [x] Catalog-rule audit complete (rules 1-18 reviewed, disclosures in README)

### Verified vertical demos
- [x] Midland County: TxDOT roads + 5,000-well RRC sample (where + 5-page pagination)
- [x] Texas Triangle: 4 interstate corridor layers, geocoder-framed
- [x] NYC: HIFLD transmission lines colored by voltage class

## 2. Build queue (priority order)

### A. Renderer Tier 1 — GL JS flash, both guests (L)
- [ ] `map.fitBounds` for frame requests (replaces hand-rolled mercator math)
- [ ] Zoom-scaled line width / circle radius (`interpolate ['zoom']`)
- [ ] Heatmap toggle for dense point layers
- [ ] Clustering below a zoom threshold with counts (kills the old
      "no cluster donuts" limit)
- [ ] Hover highlight via `setFeatureState` + pointer cursor
- [ ] Source perf knobs: per-layer `minzoom`, `buffer`, `tolerance`
- [ ] `cooperativeGestures` + pitch/rotate on NavigationControl

### B. Pro-tool exports (L) — the interop story
- [ ] GeoPackage export (multi-layer, stdlib GPKG writer — no GDAL)
- [ ] Zipped shapefile export (pyshp already a dependency; O&G land shops)
- [ ] KMZ export for Google Earth
- [ ] Provenance sidecar (or embedded in GPKG metadata tables)
- [ ] Stretch: minimal `.qgs` QGIS project pre-styled from our palettes

### C. Live sync (M)
- [ ] `ctx.socket` push instead of 5s polling (keep HTTP poll as fallback)
      — instant multi-surface/multi-session updates

### D. Wow tier (M)
- [ ] Basemap picker incl. satellite (Mapbox satellite-streets w/ token;
      ESRI World Imagery XYZ keyless, attribution-compliant)
- [ ] 3D terrain toggle (Mapbox DEM w/ token; AWS terrain tiles keyless)
- [ ] Frame live in a fresh session (deployed; just demo it)

### E. Polish (S each)
- [ ] Fit-to-selection button
- [ ] 2x DPI snapshot export
- [ ] Escape/`/` keyboard shortcuts in the attributes panel

## 3. Demo and presentation

- [ ] 3-5 min recorded walkthrough, the arc: zero-token first run → drag a file
      → one-click public data catalog → "map the Texas Triangle" (auto-frame)
      → buffer analysis + query → export GeoPackage → open in QGIS
- [ ] Live-demo script + recorded fallback (network dies; recording doesn't)
- [ ] Screenshot gallery (3 evidence shots exist; refresh after Tier 1/D)
- [ ] Architecture diagram (one page, from docs/CONTRACTS.md structure)
- [ ] **Demo workspaces from docs/USE_CASES.md — build the top two as
      polished, repeatable, saved maps** (judges watch these run, they don't
      read the doc):
  - [ ] Use case 1 · Pipeline corridor screening: RRC QPipelines + 200 m
        buffer + radius-selected wells inside the corridor + exported CSV
        (exercises import, where, analyze, radius select, export)
  - [ ] Use case 2 · Orphan-well prioritization: RRC Orphan Wells colored by
        RELIAB, point-counted per county over TIGERweb counties, snapshot PNG
        (exercises import, classification, count_points, snapshot)
  - [ ] Remaining 8 use cases: runnable on request — rehearse two more
        (county inventory #4, address-list geocode #7) as judge-Q&A backups
- [ ] The inversion narrative vs prior art (GeoAgent, GIS Chat, GeoPilot):
      GIS *inside* the agent, not an agent bolted onto QGIS. Cite them.
- [ ] The vertical story: docs/USE_CASES.md (10 O&G workflows)
- [ ] THE META-STORY: MapDesk was built, tested, live-verified, and iterated
      entirely *by Hermes agents* — 6 parallel agent workstreams against a
      binding contracts doc, with a human approving exactly two clicks.
      The entry demos the plugin AND the platform.

## 4. Submission quality

- [ ] Public GitHub repo (commit history already tells the build story)
- [ ] CI: GitHub Actions running all suites + `hermes plugins validate`
- [ ] plugin-catalog/mapdesk.yaml entry (40-char SHA, quoted version)
- [ ] PR description with rule-13 disclosures + screenshots + video link
- [ ] README final polish (quick-start, disclosures, use cases)
- [ ] Dependency release quarantine in CI (uv --exclude-newer, rule 10 nod)
