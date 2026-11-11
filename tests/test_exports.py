import base64
import importlib.util
import io
import json
import os
import pathlib
import re
import sqlite3
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
import zipfile
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
HERMES=pathlib.Path.home()/'AppData/Local/hermes/hermes-agent'
sys.path.insert(0,str(HERMES)); sys.path.insert(0,str(ROOT))
try:
    from testpaths import scratch_dir as _scratch_root
except ImportError:
    from tests.testpaths import scratch_dir as _scratch_root
SCRATCH=_scratch_root()

POINTS={'type':'FeatureCollection','features':[
 {'type':'Feature','properties':{'operator':'A & B','depth':120,'rate':12.5,'active':True,'note':None},
  'geometry':{'type':'Point','coordinates':[-102,32]}},
 {'type':'Feature','properties':{'operator':'C','depth':200,'rate':7.25,'active':False,'note':'ok'},
  'geometry':{'type':'Point','coordinates':[-101,31]}}]}
RING=[[-103,31],[-101,31],[-101,33],[-103,33],[-103,31]]
HOLES={'type':'FeatureCollection','features':[
 {'type':'Feature','properties':{'zone':'core','acres':4.0},
  'geometry':{'type':'Polygon','coordinates':[RING,[[-102.5,31.5],[-102.5,32.5],[-101.5,32.5],[-102.5,31.5]]]}}]}
MIXED={'type':'FeatureCollection','features':[
 {'type':'Feature','properties':{'OPERATORNAME':'X','OPERATORNAMELONG':'Y','val':1},
  'geometry':{'type':'Point','coordinates':[-102,32]}},
 {'type':'Feature','properties':{'OPERATORNAME':'L','OPERATORNAMELONG':'M','val':2},
  'geometry':{'type':'LineString','coordinates':[[-103,31],[-101,31]]}},
 {'type':'Feature','properties':{'OPERATORNAME':'P','OPERATORNAMELONG':'Q','val':3},
  'geometry':{'type':'Polygon','coordinates':[RING]}}]}


def _store_directory():
    return tempfile.TemporaryDirectory(dir=SCRATCH)


def _import(rt, name, geojson):
    return rt.execute('import_geojson',{'name':name,'geojson':geojson})


class ExportFilenameTests(unittest.TestCase):
    def test_sanitize_stem_rules(self):
        import mapdesk_exports
        self.assertEqual(mapdesk_exports.sanitize_stem('Saved fixture'),'Saved_fixture')
        self.assertEqual(mapdesk_exports.sanitize_stem('A/B:C*D?E'),'A_B_C_D_E')
        self.assertEqual(mapdesk_exports.sanitize_stem('x'*80),'x'*60)
        self.assertEqual(mapdesk_exports.sanitize_stem(''),'mapdesk')
        self.assertEqual(mapdesk_exports.sanitize_stem('///'),'mapdesk')
        self.assertRegex(mapdesk_exports.export_filename('Saved fixture','gpkg'),
                         r'^Saved_fixture-\d{8}-\d{4}\.gpkg$')


class GeoPackageTests(unittest.TestCase):
    def test_gpkg_round_trip_through_sqlite_and_shapely(self):
        from shapely import wkb as shapely_wkb
        from shapely.geometry import shape
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            rt.execute('save',{'name':'Saved fixture'})
            first=_import(rt,'Test fixture',POINTS)
            second=_import(rt,'test FIXTURE!',HOLES)
            result=rt.execute('export_gpkg',{'layer_ids':[first['id'],second['id']]})
            self.assertEqual(set(result),{'base64','filename','layers','feature_count','bytes'})
            self.assertEqual(result['feature_count'],3)
            self.assertRegex(result['filename'],r'^Saved_fixture-\d{8}-\d{4}\.gpkg$')
            payload=base64.b64decode(result['base64'])
            self.assertEqual(len(payload),result['bytes'])
            with tempfile.NamedTemporaryFile(dir=SCRATCH,suffix='.gpkg',delete=False) as handle:
                handle.write(payload); path=handle.name
            connection=sqlite3.connect(path)
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn('gpkg_spatial_ref_sys',tables); self.assertIn('gpkg_contents',tables)
            self.assertIn('gpkg_geometry_columns',tables); self.assertIn('gpkg_metadata',tables)
            self.assertIn('gpkg_metadata_reference',tables)
            self.assertEqual(result['layers'],['test_fixture','test_fixture_2'])
            self.assertEqual(tables&{'test_fixture','test_fixture_2'},{'test_fixture','test_fixture_2'})
            srs=dict(connection.execute('SELECT srs_id, definition FROM gpkg_spatial_ref_sys'))
            self.assertIn('AUTHORITY["EPSG","4326"]',srs[4326]); self.assertEqual(srs[-1],'undefined')
            contents=dict((row[0],row) for row in connection.execute(
                'SELECT table_name, identifier, last_change, min_x, min_y, max_x, max_y FROM gpkg_contents'))
            self.assertEqual(contents['test_fixture'][1],'Test fixture')
            self.assertTrue(contents['test_fixture'][2].endswith('Z'))
            self.assertEqual(contents['test_fixture'][3:],(-102.0,31.0,-101.0,32.0))
            self.assertEqual(contents['test_fixture_2'][3:],(-103.0,31.0,-101.0,33.0))
            gc=dict((row[0],row[1:]) for row in connection.execute(
                'SELECT table_name, column_name, geometry_type_name, srs_id FROM gpkg_geometry_columns'))
            self.assertEqual(gc['test_fixture'],('geom','POINT',4326))
            self.assertEqual(gc['test_fixture_2'],('geom','POLYGON',4326))
            blob=connection.execute('SELECT geom FROM test_fixture WHERE fid=1').fetchone()[0]
            self.assertEqual(blob[:2],b'GP'); self.assertEqual(blob[2],0); self.assertEqual(blob[3],2)
            self.assertEqual(int.from_bytes(blob[4:8],'little'),4326)
            envelope=struct.unpack('<dddd',blob[8:40])
            self.assertEqual(envelope,(-102.0,-102.0,32.0,32.0))
            point=shapely_wkb.loads(blob[40:])
            self.assertEqual((point.x,point.y),(-102.0,32.0))
            row=connection.execute('SELECT "operator", "depth", "rate", "active", "note" FROM test_fixture WHERE fid=1').fetchone()
            self.assertEqual(row,('A & B',120,12.5,1,None))
            polygon_blob=connection.execute('SELECT geom FROM test_fixture_2').fetchone()[0]
            polygon=shapely_wkb.loads(polygon_blob[40:])
            original=shape(HOLES['features'][0]['geometry'])
            self.assertEqual(len(polygon.interiors),1)
            self.assertTrue(polygon.equals(original))
            metadata=connection.execute('SELECT md_scope, md_standard_uri, mime_type, metadata FROM gpkg_metadata').fetchone()
            self.assertEqual(metadata[0],'dataset')
            self.assertEqual(metadata[1],'https://hermes-mapdesk.local/provenance')
            self.assertEqual(metadata[2],'application/json')
            provenance=json.loads(metadata[3])
            self.assertEqual(provenance['workspace'],'Saved fixture')
            self.assertTrue(provenance['exported_at'].endswith('Z'))
            self.assertEqual(provenance['layers'][0]['name'],'Test fixture')
            self.assertEqual(provenance['layers'][0]['source']['kind'],'geojson')
            self.assertEqual(provenance['layers'][0]['feature_count'],2)
            self.assertEqual(provenance['layers'][0]['fields'],['active','depth','note','operator','rate'])
            reference=connection.execute('SELECT reference_scope, table_name, md_file_id FROM gpkg_metadata_reference').fetchone()
            self.assertEqual(reference,('table','test_fixture',1))
            connection.close()
            os.unlink(path)

    def test_gpkg_rejects_unknown_and_empty_layers(self):
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            layer=_import(rt,'Wells',POINTS)
            with self.assertRaisesRegex(ValueError,'Select at least one layer'):
                rt.execute('export_gpkg',{'layer_ids':[]})
            with self.assertRaisesRegex(ValueError,'bogus'):
                rt.execute('export_gpkg',{'layer_ids':[layer['id'],'bogus']})
            with self.assertRaisesRegex(ValueError,'layer_ids'):
                rt.execute('export_gpkg',{'layer_ids':'nope'})

    def test_export_cap_rejects_oversized_result(self):
        import mapdesk_runtime as rt
        big='v'*1000000
        heavy={'type':'FeatureCollection','features':[
            {'type':'Feature','properties':{'blob':big},'geometry':{'type':'Point','coordinates':[0,0]}}
            for _ in range(6)]}
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            first=_import(rt,'Heavy one',heavy)
            second=_import(rt,'Heavy two',heavy)
            with self.assertRaisesRegex(ValueError,'Export exceeds 12 MB'):
                rt.execute('export_gpkg',{'layer_ids':[first['id'],second['id']]})


class ShapefileTests(unittest.TestCase):
    def test_shp_round_trip_with_mixed_type_split(self):
        import shapefile
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            rt.execute('save',{'name':'Saved fixture'})
            layer=_import(rt,'Field sites',MIXED)
            result=rt.execute('export_shp',{'layer_id':layer['id']})
            self.assertEqual(result['feature_count'],3)
            self.assertRegex(result['filename'],r'^Saved_fixture-\d{8}-\d{4}\.zip$')
            self.assertEqual(result['layers'],['Saved_fixture_point','Saved_fixture_line',
                                               'Saved_fixture_polygon'])
            payload=base64.b64decode(result['base64'])
            self.assertEqual(len(payload),result['bytes'])
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names=archive.namelist()
                for base in ('Saved_fixture_point','Saved_fixture_line','Saved_fixture_polygon'):
                    for extension in ('.shp','.shx','.dbf','.prj'):
                        self.assertIn(base+extension,names)
                self.assertIn('mapdesk_provenance.json',names)
                def _reader(base):
                    return shapefile.Reader(shp=io.BytesIO(archive.read(base+'.shp')),
                                            shx=io.BytesIO(archive.read(base+'.shx')),
                                            dbf=io.BytesIO(archive.read(base+'.dbf')))
                reader=_reader('Saved_fixture_point')
                self.assertEqual(len(reader),1)
                self.assertEqual(reader.fields[1:],[['OPERATORNA','C',254,0],
                                                      ['OPERATORN1','C',254,0],
                                                      ['VAL','N',18,0]],
                             'truncated field names must be deduplicated')
                record=reader.record(0).as_dict()
                self.assertEqual(record,{'OPERATORNA':'X','OPERATORN1':'Y','VAL':1})
                self.assertEqual(reader.shape(0).__geo_interface__['type'],'Point')
                self.assertEqual(tuple(reader.shape(0).__geo_interface__['coordinates']),(-102.0,32.0))
                polygon=_reader('Saved_fixture_polygon')
                self.assertEqual(len(polygon),1)
                self.assertEqual(polygon.record(0).as_dict(),{'OPERATORNA':'P','OPERATORN1':'Q','VAL':3})
                self.assertEqual(polygon.shape(0).__geo_interface__['type'],'Polygon')
                self.assertEqual(len(polygon.shape(0).points),5)
                prj=archive.read('Saved_fixture_point.prj').decode('utf-8')
                self.assertIn('GCS_WGS_1984',prj)
                provenance=json.loads(archive.read('mapdesk_provenance.json'))
                self.assertEqual(provenance['workspace'],'Saved fixture')
                self.assertEqual(provenance['layers'][0]['feature_count'],3)

    def test_shp_skips_geometry_collections(self):
        import mapdesk_exports
        with_geometry_collection={'type':'FeatureCollection','features':[
            {'type':'Feature','properties':{'val':4},
             'geometry':{'type':'GeometryCollection','geometries':[
                 {'type':'Point','coordinates':[-100,30]}]}},
            {'type':'Feature','properties':{'val':5},
             'geometry':{'type':'Point','coordinates':[-99,29]}}]}
        payload,names=mapdesk_exports.write_shp(
            'Skips',with_geometry_collection,
            mapdesk_exports.make_provenance('Skips',[{'name':'Skips','source':{},
                                                      'feature_count':2,'fields':['val']}]),
            'Skips')
        self.assertEqual(names,['Skips','skipped:1'])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.namelist(),
                             ['Skips.shp','Skips.shx','Skips.dbf',
                              'Skips.prj','mapdesk_provenance.json'])

    def test_shp_single_type_has_no_suffix(self):
        import shapefile
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            layer=_import(rt,'Wells',POINTS)
            result=rt.execute('export_shp',{'layer_id':layer['id']})
            self.assertEqual(result['layers'],['Untitled_map'])
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['base64']))) as archive:
                self.assertIn('Untitled_map.shp',archive.namelist())
                self.assertNotIn('Untitled_map_point.shp',archive.namelist())
                reader=shapefile.Reader(shp=io.BytesIO(archive.read('Untitled_map.shp')),
                                        shx=io.BytesIO(archive.read('Untitled_map.shx')),
                                        dbf=io.BytesIO(archive.read('Untitled_map.dbf')))
                self.assertEqual(len(reader),2)
                self.assertEqual(reader.record(1).as_dict(),
                                 {'OPERATOR':'C','DEPTH':200,'RATE':7.25,'ACTIVE':False,'NOTE':'ok'})

    def test_shp_unknown_layer_raises(self):
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            with self.assertRaisesRegex(ValueError,'bogus'):
                rt.execute('export_shp',{'layer_id':'bogus'})
            with self.assertRaisesRegex(ValueError,'layer_id'):
                rt.execute('export_shp',{'layer_id':''})


class KMZTests(unittest.TestCase):
    def _placemarks(self,document):
        return document.findall('.//{http://www.opengis.net/kml/2.2}Placemark')

    def test_kmz_round_trip_with_escapes_and_holes(self):
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            rt.execute('save',{'name':'Saved fixture'})
            first=_import(rt,'Wells',POINTS)
            second=_import(rt,'Zones',HOLES)
            rt.execute('update_layer',{'layer_id':second['id'],'changes':{'style':{'color':'#ff0000','opacity':0.5}}})
            result=rt.execute('export_kmz',{'layer_ids':[first['id'],second['id']]})
            self.assertEqual(result['layers'],['Wells','Zones'])
            self.assertEqual(result['feature_count'],3)
            self.assertRegex(result['filename'],r'^Saved_fixture-\d{8}-\d{4}\.kmz$')
            payload=base64.b64decode(result['base64'])
            self.assertEqual(len(payload),result['bytes'])
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(set(archive.namelist()),{'doc.kml','mapdesk_provenance.json'})
                document=ElementTree.fromstring(archive.read('doc.kml'))
                provenance=json.loads(archive.read('mapdesk_provenance.json'))
            self.assertEqual([entry['name'] for entry in provenance['layers']],['Wells','Zones'])
            self.assertEqual(provenance['workspace'],'Saved fixture')
            placemarks=self._placemarks(document)
            self.assertEqual(len(placemarks),3)
            ns='{http://www.opengis.net/kml/2.2}'
            def _data(placemark,key):
                entry=placemark.find('.//{}Data[@name="{}"]'.format(ns,key))
                return None if entry is None else entry.findtext(ns+'value')
            wells=[p for p in placemarks if _data(p,'operator') in ('A & B','C')]
            self.assertEqual(len(wells),2)
            ampersand=wells[0].find('.//{}Data[@name="operator"]'.format(ns))
            self.assertEqual(ampersand.findtext(ns+'value'),'A & B','ampersand values must round-trip escaped')
            zone=[p for p in placemarks if _data(p,'zone')=='core'][0]
            inner=zone.find('.//{}innerBoundaryIs'.format(ns))
            self.assertIsNotNone(inner,'polygon holes must be written as innerBoundaryIs')
            outer=zone.find('.//{}outerBoundaryIs'.format(ns))
            self.assertEqual(outer.findtext('.//{}coordinates'.format(ns)),
                             '-103,31 -101,31 -101,33 -103,33 -103,31')
            styles=document.findall('.//{}Style'.format(ns))
            colors={style.get('id'):style.findtext('.//{}color'.format(ns))
                    for style in styles}
            self.assertEqual(colors['layer-1'],'800000FF','0.5 opacity #ff0000 -> aabbggrr')

    def test_kmz_splits_geometry_collections_per_part(self):
        import mapdesk_exports
        collection={'type':'FeatureCollection','features':[
            {'type':'Feature','properties':{'name':'Twin','note':'A & B <tag>'},
             'geometry':{'type':'GeometryCollection','geometries':[
                 {'type':'Point','coordinates':[-100,30]},
                 {'type':'Polygon','coordinates':[RING]}]}}]}
        payload,names=mapdesk_exports.write_kmz(
            [{'name':'Mixed','geojson':collection}],
            mapdesk_exports.make_provenance('Saved fixture',
                                            [{'name':'Mixed','source':{},'feature_count':1,'fields':['name','note']}]),
            'Saved fixture')
        self.assertEqual(names,['Mixed'])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(set(archive.namelist()),{'doc.kml','mapdesk_provenance.json'})
            document=ElementTree.fromstring(archive.read('doc.kml'))
        twins=self._placemarks(document)
        self.assertEqual(len(twins),2,'GeometryCollection splits one placemark per part')
        self.assertEqual(twins[0].findtext('{http://www.opengis.net/kml/2.2}name'),'Twin')
        note=twins[0].find('.//{http://www.opengis.net/kml/2.2}Data[@name="note"]')
        self.assertEqual(note.findtext('{http://www.opengis.net/kml/2.2}value'),'A & B <tag>')
        self.assertIsNotNone(twins[1].find('.//{http://www.opengis.net/kml/2.2}innerBoundaryIs') or
                              twins[1].find('.//{http://www.opengis.net/kml/2.2}outerBoundaryIs'))

    def test_kmz_rejects_unknown_and_empty_layers(self):
        import mapdesk_runtime as rt
        with _store_directory() as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            layer=_import(rt,'Wells',POINTS)
            with self.assertRaisesRegex(ValueError,'Select at least one layer'):
                rt.execute('export_kmz',{'layer_ids':[]})
            with self.assertRaisesRegex(ValueError,'bogus'):
                rt.execute('export_kmz',{'layer_ids':['bogus',layer['id']]})


if __name__=='__main__': unittest.main()
