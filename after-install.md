# Start using MapDesk

1. In **Settings → Plugins → MapDesk**, check that the agent toolkit and Desktop
   interface are enabled. They are separate controls. Follow any activation
   notice Hermes shows; do not restart unrelated services.
2. Open the command palette and choose **Open MapDesk**. The map works right
   away on the free OpenFreeMap basemap; no account or token is needed.
   **Every Open MapDesk invocation starts a blank map.** The current map is
   archived to **Saved maps** first, and your settings stay.
3. Add data. Choose a file under **Add your data** (GeoJSON, CSV, KML, KMZ, GPX,
   XLSX or a zipped shapefile) or drop one anywhere in the pane. Review the
   preview, name the layer, pick the CSV latitude/longitude columns when
   needed, then confirm; nothing from a file is imported before confirmation.
   The map zooms to the imported data, and **Zoom to layer** brings it back
   later. To try the pane without a file, click **Load sample data**: it adds
   three small layers that are synthetic, not real data.
4. Optional: a Mapbox basemap. In the same settings page, enter a dedicated
   **public** Mapbox token (`pk.`, with the `styles:read` and `fonts:read`
   scopes). Never enter a secret `sk.` token or paste credentials into chat.
   Review Mapbox's account requirements and usage pricing first; map loads may
   be billed. Then expand **Setup & help** in the pane and click **Check
   setup**. A saved token alone does not prove the map works: Setup & help
   says whether the Mapbox map actually loaded. To return to the free basemap,
   remove the token and click **Check setup** again.

Layers, source metadata, styling, selection and map position are saved locally
for this Hermes profile. Switching tabs and Refresh keep current work. **Clear
map** and **Open MapDesk** archive the map to **Saved maps** before clearing;
original source files are untouched. ArcGIS results can be incomplete and are
labeled as such. Existing layers are not replaced by a new import.

**Supported now:** GeoJSON, coordinate CSV, KML, KMZ, GPX, XLSX, zipped
shapefiles and bounded public ArcGIS feature layers. Rasters, routing and
identifier joins are not supported. One live workspace per profile; imports and
stored attributes are not encrypted by MapDesk.

See the installed **README.md** for data limits, privacy/usage notes and
troubleshooting. Normal users do not need npm or the development test tools.
