# MapDesk — 10 Oil & Gas use cases

Each workflow runs with the 0.2 feature set on datasets verified live against
their public REST endpoints (RRC `RRC_Public_Viewer_Srvs`, TxDOT AGOL,
TIGERweb, HIFLD). No tokens, no accounts.

1. **Pipeline corridor screening (PIR / affected-area review).**
   Import RRC `QPipelines` (layer 12) for a county bbox, then
   `mapdesk_analyze` buffer by a potential impact radius (e.g. 200 m) and
   `mapdesk_query` / radius-select the `Well Locations` layer against it.
   Result: every well inside the corridor, exportable as GeoJSON/CSV for the
   integrity-management packet.

2. **Orphan-well prioritization & plugging program planning.**
   RRC `Orphan Wells` (layer 2) colored by `RELIAB` (location reliability);
   `count_points` against TIGERweb county polygons to rank counties by
   orphan-well burden; `measure` and TxDOT roads for crew-access distance.
   Export the workspace JSON as the program tracking baseline.

3. **Injection & disposal well review.**
   RRC `Injection/Disposal` (layer 4) + `Commercial Disposal` (layer 3) by
   county; buffer disposal wells by setback distance; `clip` against a
   client-provided survey or lease polygon (zipped shapefile import) to
   screen in-house acreage for nearby disposal activity.

4. **County-by-county well inventory for regulatory filings.**
   `mapdesk_import` with `where` clauses pulls exactly one county's wells
   per layer (e.g. `GIS_SYMBOL_DESCRIPTION='Oil Well'`, API numbers included);
   `count_points` over TIGERweb counties gives per-county tallies;
   `Export CSV` produces the spreadsheet the permit review wants.

5. **Lease / surface-ownership package assembly.**
   Import the land department's lease plats as KMZ or zipped shapefile
   (with `.prj` reprojection), overlay RRC `Surveys` (24) / `Survey Abstract
   Labels` (22), label wells by `GIS_WELL_NUMBER`, and `measure` polygon
   areas — a one-map exhibit instead of a folder of PDFs.

6. **Rig-move and access planning.**
   TxDOT roadways (`COUNTY='...'`, colored by `RTE_PRFX` so IH/US/SH/FM are
   visually distinct) plus well locations; use Measure mode for route
   distances, radius-select wells by drive-distance proxies around highway
   interchanges, and Snapshot PNG for the logistics brief.

7. **Facility address list → operational map (no coordinates needed).**
   The CSV geocode flow: paste a list of tank batteries, compressor
   stations, or vendor yards with an address column; MapDesk batch-geocodes
   via Nominatim and drops them as points to overlay on wells and pipelines.

8. **Incident / spill response common operating picture.**
   RRC `Discharge Permits` (37), `Operator Cleanup Program Sites` (33),
   `Water` (27) and wells on one map; Ctrl-drag a geodesic radius around a
   report location to enumerate every asset within it; Snapshot PNG and
   workspace export hand the COP to responders in minutes.

9. **Renewables-on-O&G-land site screening.**
   HIFLD electric substations + RRC wells + TxDOT roads in one workspace:
   dissolve soil/well concentration areas, count wells inside candidate
   parcels (`count_points`), buffer substations for tie-in distance — the
   diversification screening map for land with legacy production.

10. **Acquisition due-diligence data room.**
    A repeatable workspace: target-area wells by type (categorical
    classification + legend), pipelines, disposal/orphan flags, roads and
    county boundaries — styled once, then `Export workspace` JSON + per-layer
    CSV/GeoJSON give every reviewer the same base map with provenance
    (retrieval timestamps and source URLs attached to each layer).

## Notes and limits (say these in the demo)

- The RRC public viewer well layer carries API number, well number, symbol
  type and reliability — not operator or production. Operator-level rollups
  come from RRC's downloadable open data, imported as a file.
- ArcGIS bbox imports are rectangles; use `where` clauses (county, symbol
  type) for boundary-faithful extracts and say what falls outside the box.
- Wells layers cap at 5,000 features per import and are labeled
  INCOMPLETE SAMPLE when truncated — split by type or county for full sets.
