# Submission draft — plugin catalog entry + PR description

Do not publish without the owner's approval (standing policy). Fill the
placeholders at submission time: repo URL, 40-character SHA pin, and the
version bump that lands with 0.3.

## plugin-catalog/mapdesk.yaml (the catalog entry file)

```yaml
name: mapdesk
title: "MapDesk"
repo: https://github.com/mackydubzz/hermes-mapdesk   # public HTTPS clone URL
sha: <40-character commit sha>                    # exact pin, full length
description: A GIS workspace inside Hermes Desktop - import GeoJSON, CSV, KML, GPX, XLSX and shapefiles, run geodesic analysis and control layers from chat. Works with no account; a Mapbox token is optional.
maintainer: mackydubzz                            # the GitHub owner of the repo above
tier: community
category: desktop
requires_hermes: ">=0.23.0"                        # same floor as plugin.yaml
version: "0.3.0"                                   # same as plugin.yaml, quoted
screenshots:                                      # up to 6, on a GitHub host, pinned to the same sha
  - https://raw.githubusercontent.com/mackydubzz/hermes-mapdesk/<sha>/docs/screenshots/<file>.png
capabilities:                                     # must match plugin.yaml and what register() registers
  provides_tools:
    - mapdesk_status
    - mapdesk_import
    - mapdesk_layer
    - mapdesk_selection
    - mapdesk_query
    - mapdesk_analyze
    - mapdesk_view
  provides_hooks: []
  provides_middleware: []
  requires_env: []
```

(Keys follow the entry schema in plugin-catalog/README.md as read on
2026-11-11; re-check it at submission time. `subdir` is unnecessary since the
plugin sits at the repo root. Screenshots must be committed to the public
repo: `artifacts/` is git-ignored, so nothing there is reachable by URL.)

## PR description draft

**What MapDesk does**

MapDesk is a GIS workspace that lives inside Hermes Desktop rather than a
chat panel bolted onto an external GIS. Seven agent tools (`mapdesk_status`,
`mapdesk_import`, `mapdesk_layer`, `mapdesk_selection`, `mapdesk_view`,
`mapdesk_query`, `mapdesk_analyze`) operate one persistent, per-profile
workspace rendered in a sandboxed map pane with an attribute table, filters,
classification, labels, measure tools, radius and by-location selection, and
saved-map archive with undo. The map renders with **no account or token**
(keyless OpenFreeMap basemap via MapLibre); a public `pk.` Mapbox token
switches it to the Mapbox guest. Both offer dark, light, streets and
satellite (with roads, borders and place names) basemaps, terrain, heatmap
and clustering.

**Surfaces used**

- Python plugin: 7 tools, profile-scoped SQLite workspace store
- Dashboard backend (`plugin_api.py`): REST + websocket namespace under
  `/api/plugins/mapdesk`
- Desktop SDK (`desktop/plugin.js`): right-dock pane, `/mapdesk` route page
  with sidebar nav row, palette command, sandboxed `SandboxedFrame` renderer
  guests (Mapbox GL JS 3.33 pinned, SRI; MapLibre 5 pinned, SRI)

**Rule 13 disclosures**

- Network: optional Mapbox GL JS/styles/tiles (token, billed); for the
  keyless guest, MapLibre GL JS from unpkg (version-pinned, SRI), OpenFreeMap
  tiles/styles/fonts (no SLA), Esri ArcGIS Online imagery plus road and
  boundary/place-name overlays for the satellite basemap, and Terrarium
  elevation tiles on Amazon S3 for Terrain 3D; public ArcGIS REST imports
  contact the chosen source (SSRF-pinned, no credentials); Nominatim address
  geocoding at 1 req/s with a local cache (pane CSV flow).
- Files: imports/exports and the workspace live in Hermes's profile-scoped
  plugin data; saved maps are files written through the Desktop plugin file
  API; exports (GeoPackage/shapefile/KMZ/GeoJSON/CSV/PNG) are produced
  locally only.
- Credentials: one public `pk.` Mapbox token via the plugin settings secret
  (`agent.secret_scope.get_secret`); no `sk.` tokens, no env reads, no
  credential files.
- No telemetry. No self-updater — updates come only from a SHA-bump PR.

**Why it's different from LLM-in-QGIS plugins (GeoAgent, GIS Chat, GeoPilot)**

Those bolt an LLM onto an installed desktop GIS and generate platform code.
MapDesk inverts it: the GIS is native to the agent's own desktop — zero
install, zero accounts, seven first-class tools, honest provenance labels on
every bounded import, and the map archives/undoes across session switches.
It is narrower on purpose (no raster processing); its job is finding,
framing, screening and exporting — then handing off cleanly via GeoPackage.

**Built and verified with Hermes itself**

The plugin was developed end-to-end by Hermes agents: six parallel agent
workstreams against a binding contracts document, with the full suite
(217 Python tests plus real-browser draw and pane tests), catalog validation,
and live in-app verification run by the agent. Demo workspaces are described
in `docs/USE_CASES.md`.

## Pre-flight before opening the PR

- [x] plugin.yaml is 0.3.0 and matches `version:` here
- [ ] Publish the repo publicly; confirm `git clone` works unauthenticated
- [ ] Pin `sha` to the exact commit; re-check version match
- [ ] `hermes plugins validate . --install-deps` green at that commit
- [ ] CI green on the pinned commit (actions/workflows/ci.yml)
- [ ] Screenshots: Texas Triangle, Midland wells, NYC power lines,
      corridor screening + orphan-well choropleth (demo workspaces)
