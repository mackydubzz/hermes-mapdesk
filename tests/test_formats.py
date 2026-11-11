"""MapDesk format parser tests: real in-memory fixtures, no mocks of the module."""
import base64
from datetime import datetime
import importlib
import io
import unittest
from unittest.mock import patch
import zipfile

import openpyxl
import pyproj
import shapefile


def formats():
    return importlib.import_module("mapdesk_formats")


def b64(data):
    return base64.b64encode(data).decode("ascii")


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def write_shapefile(shape_type, build):
    shp_buf, shx_buf, dbf_buf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp_buf, shx=shx_buf, dbf=dbf_buf, shapeType=shape_type)
    build(writer)
    writer.close()
    return shp_buf.getvalue(), shx_buf.getvalue(), dbf_buf.getvalue()


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Alpha</name>
      <ExtendedData>
        <Data name="type"><value>well</value></Data>
        <SchemaData schemaUrl="#schema"><SimpleData name="api">0007</SimpleData></SchemaData>
      </ExtendedData>
      <Point><coordinates>-102.1,31.9,120</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Route</name>
      <LineString><coordinates>-102.2,31.8 -102.0,32.0</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>Zone</name>
      <Polygon>
        <outerBoundaryIs><LinearRing>
          <coordinates>-102.3,31.7 -101.9,31.7 -101.9,32.1 -102.3,31.7</coordinates>
        </LinearRing></outerBoundaryIs>
        <innerBoundaryIs><LinearRing>
          <coordinates>-102.2,31.8 -102.05,31.8 -102.05,31.9 -102.2,31.8</coordinates>
        </LinearRing></innerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>"""

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <wpt lat="31.9" lon="-102.1"><name>Alpha</name><desc>Well</desc><ele>120.5</ele>
    <time>2024-01-01T00:00:00Z</time></wpt>
  <trk><name>Route</name>
    <trkseg><trkpt lat="31.8" lon="-102.2"></trkpt><trkpt lat="32.0" lon="-102.0"></trkpt></trkseg>
  </trk>
  <rte><name>Path</name>
    <rtept lat="31.5" lon="-102.5"></rtept><rtept lat="31.6" lon="-102.4"></rtept>
  </rte>
</gpx>"""


class SourceTestsMixin:
    def assert_source(self, source, kind):
        self.assertEqual(source["kind"], kind)
        self.assertIsNone(source["url"])
        self.assertEqual(source["crs"], "EPSG:4326")
        self.assertTrue(source["complete"])
        self.assertTrue(source["note"].startswith("Complete uploaded file"))
        self.assertIsNotNone(datetime.fromisoformat(source["fetched_at"]).tzinfo)


class KMLTests(SourceTestsMixin, unittest.TestCase):
    def test_placemarks_points_lines_polygons_and_extended_data(self):
        result = formats().parse_kml(KML)
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 3)
        self.assertEqual(features[0]["geometry"],
                         {"type": "Point", "coordinates": [-102.1, 31.9, 120]})
        self.assertEqual(features[0]["properties"],
                         {"name": "Alpha", "type": "well", "api": "0007"})
        self.assertEqual(features[1]["geometry"],
                         {"type": "LineString", "coordinates": [[-102.2, 31.8], [-102.0, 32.0]]})
        polygon = features[2]["geometry"]
        self.assertEqual(polygon["type"], "Polygon")
        self.assertEqual(len(polygon["coordinates"]), 2)
        for ring in polygon["coordinates"]:
            self.assertGreaterEqual(len(ring), 4)
            self.assertEqual(ring[0], ring[-1])
        self.assert_source(result["source"], "kml")
        self.assertEqual(result["source"]["returned_count"], 3)

    def test_namespace_agnostic_and_multigeometry_parts(self):
        text = """<kml xmlns="urn:fake:namespace">
          <Placemark><name>Mixed</name>
            <MultiGeometry>
              <Point><coordinates>-97.5,31.2</coordinates></Point>
              <Point><coordinates>-97.4,31.1</coordinates></Point>
              <LineString><coordinates>-97.5,31.2 -97.4,31.1</coordinates></LineString>
            </MultiGeometry>
          </Placemark>
          <Placemark><name>Empty</name><description>No geometry</description></Placemark>
          <Placemark><Point><coordinates>-97.3,31.0</coordinates></Point></Placemark>
        </kml>"""
        features = formats().parse_kml(text)["geojson"]["features"]
        self.assertEqual([feature["geometry"]["type"] for feature in features],
                         ["Point", "Point", "LineString", "Point"])
        self.assertEqual(features[0]["properties"], {"name": "Mixed"})

    def test_wrong_format_malformed_or_out_of_bounds_rejected(self):
        with self.assertRaisesRegex(ValueError, "KML"):
            formats().parse_kml(GPX)
        with self.assertRaisesRegex(ValueError, "well-formed"):
            formats().parse_kml("<kml><Placemark><Point><coordinates>1,2</coordinates>")
        with self.assertRaisesRegex(ValueError, "WGS84"):
            formats().parse_kml(
                "<kml><Placemark><Point><coordinates>200,31</coordinates></Point></Placemark></kml>")
        with self.assertRaisesRegex(ValueError, "numeric"):
            formats().parse_kml(
                "<kml><Placemark><Point><coordinates>1,2,x</coordinates></Point></Placemark></kml>")
        with self.assertRaisesRegex(ValueError, "outer boundary"):
            formats().parse_kml(
                "<kml><Placemark><Polygon><innerBoundaryIs><LinearRing>"
                "<coordinates>0,0 1,0 1,1 0,0</coordinates>"
                "</LinearRing></innerBoundaryIs></Polygon></Placemark></kml>")

    def test_feature_cap_and_empty_document(self):
        placemarks = "".join(
            f"<Placemark><Point><coordinates>-102.1,31.9</coordinates></Point></Placemark>"
            for _ in range(5001))
        with self.assertRaisesRegex(ValueError, "5000"):
            formats().parse_kml(f"<kml xmlns=\"http://www.opengis.net/kml/2.2\">{placemarks}</kml>")
        result = formats().parse_kml("<kml xmlns=\"http://www.opengis.net/kml/2.2\"/>")
        self.assertEqual(result["geojson"]["features"], [])


class KMZTests(SourceTestsMixin, unittest.TestCase):
    def test_kmz_archive_parses_first_kml_entry(self):
        archive = b64(zip_bytes({"doc.kml": KML, "ignored.txt": b"noise"}))
        result = formats().parse_kmz(archive)
        self.assertEqual(len(result["geojson"]["features"]), 3)
        self.assertEqual(result["geojson"]["features"][0]["properties"]["name"], "Alpha")
        self.assert_source(result["source"], "kmz")

    def test_bad_base64_zip_or_missing_kml_rejected(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            formats().parse_kmz("%%%not-base64%%%")
        with self.assertRaisesRegex(ValueError, "ZIP"):
            formats().parse_kmz(b64(b"not a zip at all"))
        with self.assertRaisesRegex(ValueError, "\\.kml"):
            formats().parse_kmz(b64(zip_bytes({"readme.txt": b"no kml here"})))
        with self.assertRaisesRegex(ValueError, "unsafe"):
            formats().parse_kmz(b64(zip_bytes({"../doc.kml": KML.encode()})))
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            with patch.object(formats(), "MAX_DECODED_BYTES", 10):
                formats().parse_kmz(b64(zip_bytes({"doc.kml": KML.encode()})))


class GPXTests(SourceTestsMixin, unittest.TestCase):
    def test_waypoints_tracks_and_routes(self):
        result = formats().parse_gpx(GPX)
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 3)
        self.assertEqual(features[0]["geometry"], {"type": "Point", "coordinates": [-102.1, 31.9]})
        self.assertEqual(features[0]["properties"],
                         {"name": "Alpha", "desc": "Well", "ele": "120.5",
                          "time": "2024-01-01T00:00:00Z"})
        self.assertEqual(features[1]["geometry"],
                         {"type": "LineString", "coordinates": [[-102.2, 31.8], [-102.0, 32.0]]})
        self.assertEqual(features[2]["geometry"],
                         {"type": "LineString", "coordinates": [[-102.5, 31.5], [-102.4, 31.6]]})
        self.assert_source(result["source"], "gpx")

    def test_multisegment_track_becomes_multilinestring(self):
        text = """<gpx xmlns="http://www.topografix.com/GPX/1/1">
          <trk><trkseg><trkpt lat="31" lon="-102"></trkpt><trkpt lat="32" lon="-102"></trkpt></trkseg>
          <trkseg><trkpt lat="33" lon="-101"></trkpt><trkpt lat="34" lon="-101"></trkpt></trkseg></trk>
        </gpx>"""
        features = formats().parse_gpx(text)["geojson"]["features"]
        self.assertEqual(features[0]["geometry"]["type"], "MultiLineString")
        self.assertEqual(len(features[0]["geometry"]["coordinates"]), 2)

    def test_wrong_format_bad_points_and_feature_cap_rejected(self):
        with self.assertRaisesRegex(ValueError, "GPX"):
            formats().parse_gpx(KML)
        with self.assertRaisesRegex(ValueError, "lat and lon"):
            formats().parse_gpx("<gpx><wpt lon=\"-102.1\"/></gpx>")
        with self.assertRaisesRegex(ValueError, "WGS84"):
            formats().parse_gpx("<gpx><wpt lat=\"91\" lon=\"-102\"/></gpx>")
        points = "".join(f'<wpt lat="31.9" lon="-102.1"/>' for _ in range(5001))
        with self.assertRaisesRegex(ValueError, "5000"):
            formats().parse_gpx(f"<gpx xmlns=\"http://www.topografix.com/GPX/1/1\">{points}</gpx>")


def build_workbook(rows, sheet="Data", second_sheet="Empty"):
    workbook = openpyxl.Workbook()
    active = workbook.active
    active.title = sheet
    for row in rows:
        active.append(row)
    if second_sheet:
        workbook.create_sheet(second_sheet)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class XLSXTests(SourceTestsMixin, unittest.TestCase):
    def setUp(self):
        self.data = build_workbook([
            ["latitude", "longitude", "name", "depth", "when"],
            [31.9, -102.1, "Alpha", 12.5, datetime(2024, 1, 1)],
            ["31.8", -102.0, "Beta", None, None],
        ])

    def test_rows_keep_numeric_type_and_blank_for_none(self):
        result = formats().parse_xlsx(b64(self.data))
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 2)
        self.assertEqual(features[0]["geometry"], {"type": "Point", "coordinates": [-102.1, 31.9]})
        self.assertEqual(features[0]["properties"],
                         {"latitude": 31.9, "longitude": -102.1, "name": "Alpha",
                          "depth": 12.5, "when": "2024-01-01T00:00:00"})
        self.assertEqual(features[1]["properties"],
                         {"latitude": "31.8", "longitude": -102.0, "name": "Beta",
                          "depth": "", "when": ""})
        self.assert_source(result["source"], "xlsx")
        self.assertIn("Data", result["source"]["note"])

    def test_sheet_selection_and_case_insensitive_headers(self):
        result = formats().parse_xlsx(b64(self.data), sheet="Data")
        self.assertEqual(len(result["geojson"]["features"]), 2)
        text = b64(build_workbook([["LAT", "LNG", "id"], [31.9, -102.1, 7]]))
        self.assertEqual(len(formats().parse_xlsx(text)["geojson"]["features"]), 1)
        with self.assertRaisesRegex(ValueError, "Unknown XLSX sheet"):
            formats().parse_xlsx(b64(self.data), sheet="Missing")
        with self.assertRaisesRegex(ValueError, "string"):
            formats().parse_xlsx(b64(self.data), sheet=5)

    def test_missing_duplicate_or_empty_headers_rejected(self):
        for headers in [["elev", "name"], ["lat", "lat", "name"], ["lat", None, "name"], []]:
            with self.subTest(headers=headers):
                with self.assertRaisesRegex(ValueError, "XLSX"):
                    formats().parse_xlsx(b64(build_workbook([headers, [1, 2, 3]])))

    def test_bad_coordinates_and_inputs_rejected(self):
        with self.assertRaisesRegex(ValueError, "row 2"):
            formats().parse_xlsx(b64(build_workbook([["lat", "lon"], [91, 0]])))
        with self.assertRaisesRegex(ValueError, "row 2"):
            formats().parse_xlsx(b64(build_workbook([["lat", "lon"], ["abc", 0]])))
        with self.assertRaisesRegex(ValueError, "base64"):
            formats().parse_xlsx("%%%%")
        with self.assertRaisesRegex(ValueError, "workbook"):
            formats().parse_xlsx(b64(b"not an xlsx file"))

    def test_data_row_cap(self):
        rows = [["lat", "lon"]] + [[31.9, -102.1]] * 5001
        with self.assertRaisesRegex(ValueError, "5000"):
            formats().parse_xlsx(b64(build_workbook(rows)))
        rows = [["lat", "lon"]] + [[31.9, -102.1]] * 5000
        self.assertEqual(len(formats().parse_xlsx(b64(build_workbook(rows)))["geojson"]["features"]), 5000)


class ShpZipTests(SourceTestsMixin, unittest.TestCase):
    @staticmethod
    def build_points(writer):
        writer.field("name", "C", size=24)
        writer.field("elev", "N", size=10, decimal=2)
        writer.field("when", "D")
        writer.point(-97.5, 31.2)
        writer.record("Alpha", 12.5, "20240101")
        writer.null()
        writer.record("Null", None, None)

    def test_point_records_attributes_and_null_skips(self):
        shp, shx, dbf = write_shapefile(shapefile.POINT, self.build_points)
        result = formats().parse_shp_zip(b64(zip_bytes({"places.shp": shp, "places.dbf": dbf})))
        features = result["geojson"]["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["geometry"], {"type": "Point", "coordinates": [-97.5, 31.2]})
        self.assertEqual(features[0]["properties"], {"name": "Alpha", "elev": 12.5, "when": "2024-01-01"})
        self.assertEqual(result["source"]["skipped"], 1)
        self.assertIsNone(result["source"]["original_crs"])
        self.assert_source(result["source"], "shp")

    def test_lines_multipoints_pointz_and_polygons(self):
        def build_lines(writer):
            writer.field("name", "C", size=10)
            writer.line([[[-102.2, 31.8], [-102.0, 32.0]]])
            writer.record("Line")
            writer.line([[[-102.2, 31.8], [-102.0, 32.0]], [[-102.2, 31.8], [-102.0, 32.0]]])
            writer.record("MultiLine")

        def build_multipoints(writer):
            writer.field("name", "C", size=10)
            writer.multipoint([[-97.5, 31.2], [-97.4, 31.1]])
            writer.record("Pair")

        def build_pointz(writer):
            writer.field("name", "C", size=10)
            writer.pointz(-97.5, 31.2, 500)
            writer.record("High")

        def build_polygons(writer):
            writer.field("name", "C", size=10)
            shell = [(-102.3, 31.7), (-101.9, 31.7), (-101.9, 32.1), (-102.3, 32.1)]
            hole = [(-102.2, 31.8), (-102.05, 31.8), (-102.05, 31.95), (-102.2, 31.95)]
            writer.poly([shell, hole])
            writer.record("Donut")
            writer.poly([shell, [(-100.5, 31.0), (-100.0, 31.0), (-100.0, 31.5), (-100.5, 31.5)]])
            writer.record("Islands")

        def features_of(shape_type, build):
            shp, shx, dbf = write_shapefile(shape_type, build)
            return formats().parse_shp_zip(
                b64(zip_bytes({"layers.shp": shp, "layers.shx": shx, "layers.dbf": dbf})))["geojson"]["features"]

        types = ([feature["geometry"]["type"] for feature in features_of(shapefile.POLYLINE, build_lines)]
                 + [feature["geometry"]["type"] for feature in features_of(shapefile.MULTIPOINT, build_multipoints)]
                 + [feature["geometry"]["type"] for feature in features_of(shapefile.POINTZ, build_pointz)]
                 + [feature["geometry"]["type"] for feature in features_of(shapefile.POLYGON, build_polygons)])
        self.assertEqual(types, ["LineString", "MultiLineString", "MultiPoint", "Point", "Polygon", "MultiPolygon"])
        polygons = features_of(shapefile.POLYGON, build_polygons)
        donut = polygons[0]["geometry"]["coordinates"]
        self.assertEqual(len(donut), 2)
        for ring in donut:
            self.assertGreaterEqual(len(ring), 4)
            self.assertEqual(ring[0], ring[-1])
        self.assertEqual(len(polygons[1]["geometry"]["coordinates"]), 2)

    def test_prj_wgs84_is_untouched(self):
        prj = pyproj.CRS.from_epsg(4326).to_wkt()
        shp, shx, dbf = write_shapefile(shapefile.POINT, self.build_points)
        result = formats().parse_shp_zip(
            b64(zip_bytes({"places.shp": shp, "places.dbf": dbf, "places.prj": prj.encode()})))
        self.assertIsNone(result["source"]["original_crs"])
        self.assertEqual(result["geojson"]["features"][0]["geometry"]["coordinates"], [-97.5, 31.2])

    def test_prj_web_mercator_is_reprojected(self):
        def build(writer):
            writer.field("name", "C", size=10)
            writer.point(0, 0)
            writer.record("Origin")
            writer.point(1113194.91, 0)
            writer.record("Ten East")
        prj = pyproj.CRS.from_epsg(3857).to_wkt()
        shp, shx, dbf = write_shapefile(shapefile.POINT, build)
        result = formats().parse_shp_zip(
            b64(zip_bytes({"places.shp": shp, "places.dbf": dbf, "places.prj": prj.encode()})))
        features = result["geojson"]["features"]
        self.assertEqual(features[0]["geometry"]["coordinates"], [0.0, 0.0])
        lon, lat = features[1]["geometry"]["coordinates"]
        self.assertAlmostEqual(lon, 10.0, places=4)
        self.assertAlmostEqual(lat, 0.0, places=6)
        self.assertEqual(result["source"]["original_crs"], "EPSG:3857")
        self.assertIn("reprojected", result["source"]["note"])

    def test_unsupported_prj_rejected(self):
        shp, shx, dbf = write_shapefile(shapefile.POINT, self.build_points)
        with self.assertRaisesRegex(ValueError, "prj"):
            formats().parse_shp_zip(
                b64(zip_bytes({"places.shp": shp, "places.dbf": dbf, "places.prj": b"Garbage"})))

    def test_bad_archives_and_entries_rejected(self):
        shp, shx, dbf = write_shapefile(shapefile.POINT, self.build_points)
        with self.assertRaisesRegex(ValueError, "base64"):
            formats().parse_shp_zip("%%%%")
        with self.assertRaisesRegex(ValueError, "ZIP"):
            formats().parse_shp_zip(b64(b"plain bytes"))
        with self.assertRaisesRegex(ValueError, "matching"):
            formats().parse_shp_zip(b64(zip_bytes({"places.shp": shp})))
        with self.assertRaisesRegex(ValueError, "matching"):
            formats().parse_shp_zip(b64(zip_bytes({"places.dbf": dbf})))
        with self.assertRaisesRegex(ValueError, "unsafe"):
            formats().parse_shp_zip(b64(zip_bytes({"../places.shp": shp, "places.dbf": dbf})))
        with self.assertRaisesRegex(ValueError, "64 MiB"):
            with patch.object(formats(), "MAX_ZIP_MEMBER_BYTES", 50):
                formats().parse_shp_zip(b64(zip_bytes({"places.shp": shp, "places.dbf": dbf})))


class NormalizeTests(unittest.TestCase):
    @staticmethod
    def collection(*coordinates):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": index + 1},
             "geometry": {"type": "Point", "coordinates": list(position)}
            } for index, position in enumerate(coordinates)]}

    def test_plain_collection_is_untouched(self):
        fc = self.collection([-102.1, 31.9])
        result = formats().normalize_geojson(fc)
        self.assertIs(result["geojson"]["features"][0]["geometry"], fc["features"][0]["geometry"])
        self.assertIsNone(result["original_crs"])
        self.assertFalse(result["swapped_warning"])
        self.assertNotIn("crs", result["geojson"])

    def test_wgs84_crs_members_are_stripped(self):
        for name in ("EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84"):
            with self.subTest(name=name):
                fc = dict(self.collection([-102.1, 31.9]),
                          crs={"type": "name", "properties": {"name": name}})
                result = formats().normalize_geojson(fc)
                self.assertNotIn("crs", result["geojson"])
                self.assertIsNone(result["original_crs"])
                self.assertFalse(result["swapped_warning"])
                self.assertEqual(result["geojson"]["features"][0]["geometry"]["coordinates"], [-102.1, 31.9])

    def test_known_non_wgs84_crs_is_reprojected(self):
        fc = dict(self.collection([1113194.91, 0.0], [0.0, 0.0]),
                  crs={"type": "name", "properties": {"name": "EPSG:3857"}})
        result = formats().normalize_geojson(fc)
        self.assertEqual(result["original_crs"], "EPSG:3857")
        self.assertNotIn("crs", result["geojson"])
        self.assertAlmostEqual(result["geojson"]["features"][0]["geometry"]["coordinates"][0], 10.0, places=4)
        self.assertEqual(result["geojson"]["features"][1]["geometry"]["coordinates"], [0.0, 0.0])

    def test_unknown_crs_is_named_in_error(self):
        fc = dict(self.collection([1, 2]), crs={"type": "name", "properties": {"name": "MadeUp:XYZ"}})
        with self.assertRaisesRegex(ValueError, "MadeUp:XYZ"):
            formats().normalize_geojson(fc)
        with self.assertRaisesRegex(ValueError, "Link CRS"):
            formats().normalize_geojson(dict(self.collection([1, 2]), crs={"type": "link", "properties": {"url": "https://x/crs"}}))

    def test_swapped_coordinates_are_detected_and_flipped(self):
        fc = self.collection([-33.9, 151.0, 5.0], [-31.0, 150.0])
        result = formats().normalize_geojson(fc)
        self.assertTrue(result["swapped_warning"])
        self.assertEqual(result["geojson"]["features"][0]["geometry"]["coordinates"], [151.0, -33.9, 5.0])
        self.assertEqual(result["geojson"]["features"][1]["geometry"]["coordinates"], [150.0, -31.0])

    def test_inrange_ambiguity_is_never_swapped(self):
        # Both values inside ±90: indistinguishable from valid data (e.g. Europe
        # has |lat| > |lon|) — must not be flipped or warned about.
        result = formats().normalize_geojson(self.collection([20.0, 80.0], [21.0, 79.0]))
        self.assertFalse(result["swapped_warning"])
        self.assertEqual(result["geojson"]["features"][0]["geometry"]["coordinates"], [20.0, 80.0])
        self.assertEqual(result["geojson"]["features"][1]["geometry"]["coordinates"], [21.0, 79.0])

    def test_normal_ranges_do_not_trigger_warning(self):
        fc = self.collection([-102.1, 31.9], [-101.0, 32.0])
        result = formats().normalize_geojson(fc)
        self.assertFalse(result["swapped_warning"])
        self.assertEqual(result["geojson"]["features"][0]["geometry"]["coordinates"], [-102.1, 31.9])

    def test_invalid_collections_rejected(self):
        for fc, pattern in [
            ({"type": "Feature"}, "FeatureCollection"),
            ({"type": "FeatureCollection", "features": "x"}, "features"),
            ({"type": "FeatureCollection", "features": [{"type": "Point"}]}, "Feature objects"),
            ({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Circle", "coordinates": [1, 2]}}]}, "geometry type"),
            ({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [200, 0]}}]}, "WGS84"),
            ({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": [], "geometry": {"type": "Point", "coordinates": [1, 2]}}]},
             "properties"),
            (dict(self.collection([1, 2]), crs="EPSG:99999999"), "Unsupported CRS"),
        ]:
            with self.subTest(fc=fc):
                with self.assertRaisesRegex(ValueError, pattern):
                    formats().normalize_geojson(fc)

    def test_feature_cap(self):
        fc = self.collection(*([1.0, 2.0] for _ in range(5001)))
        with self.assertRaisesRegex(ValueError, "5000"):
            formats().normalize_geojson(fc)

    def test_polygon_and_ring_validation(self):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
                [[0, 0], [1, 0], [0, 0]]]}}]}
        with self.assertRaisesRegex(ValueError, "four positions"):
            formats().normalize_geojson(fc)
        fc["features"][0]["geometry"]["coordinates"] = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        result = formats().normalize_geojson(fc)
        self.assertEqual(result["geojson"]["features"][0]["properties"], {})


if __name__ == "__main__":
    unittest.main()
