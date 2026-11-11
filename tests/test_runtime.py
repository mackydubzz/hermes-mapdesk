import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
HERMES=pathlib.Path.home()/'AppData/Local/hermes/hermes-agent'
sys.path.insert(0,str(HERMES)); sys.path.insert(0,str(ROOT))
try:
    from testpaths import scratch_dir as _scratch_root
except ImportError:
    from tests.testpaths import scratch_dir as _scratch_root
SCRATCH=_scratch_root()
FC={'type':'FeatureCollection','features':[{'type':'Feature','id':'001','properties':{'operator':'A'},'geometry':{'type':'Point','coordinates':[-102,32]}}]}
SQUARES={'type':'FeatureCollection','features':[{'type':'Feature','id':'zone','properties':{},'geometry':{'type':'Polygon','coordinates':[[[-103,31],[-101,31],[-101,33],[-103,33],[-103,31]]]}}]}


class RuntimeTests(unittest.TestCase):
    def test_agent_import_and_http_state_share_persistent_workspace(self):
        path=ROOT/'mapdesk_runtime.py'
        self.assertTrue(path.exists(),'Shared MapDesk runtime is missing')
        import mapdesk_runtime as rt
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            result=rt.execute('import_geojson',{'name':'Test fixture','geojson':FC})
            self.assertEqual(result['feature_count'],1)
            self.assertNotIn('geojson',result)
            state=rt.execute('state',{})
            self.assertEqual(state['layers'][0]['id'],result['id'])
            self.assertEqual(state['layers'][0]['geojson']['features'][0]['id'],'001')
            selected=rt.execute('select',{'layer_id':result['id'],'feature_ids':['001']})
            self.assertEqual(selected['total'],1)
            self.assertEqual(selected['features'][0]['properties']['operator'],'A')
            self.assertEqual(rt.execute('selection',{})['total'],1)
            rt.execute('update_layer',{'layer_id':result['id'],'changes':{'visible':False}})
            self.assertFalse(rt.execute('state',{})['layers'][0]['visible'])
            rt.execute('viewport',{'center':[-102,32],'zoom':10})
            rt.execute('save',{'name':'Saved fixture'})
            self.assertEqual(rt.execute('state',{})['name'],'Saved fixture')
            self.assertEqual(rt.execute('status',{})['layer_count'],1)
            self.assertNotIn('token',rt.execute('status',{}))
            with self.assertRaises(ValueError): rt.execute('run_javascript',{'code':'1+1'})
            self.assertEqual(rt.execute('remove_layer',{'layer_id':result['id']})['removed'],result['id'])
            self.assertEqual(rt.execute('status',{})['layer_count'],0)
            self.assertEqual(rt.execute('selection',{})['total'],0)
            with self.assertRaises(ValueError): rt.execute('remove_layer',{'layer_id':result['id']})

    def test_csv_adapter_adds_a_persisted_layer_with_provenance(self):
        import mapdesk_runtime as rt
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            result=rt.execute('import_csv',{'name':'CSV fixture','text':'id,lat,lon\n001,32,-102\n','latitude_column':'lat','longitude_column':'lon'})
            self.assertEqual(result['feature_count'],1)
            self.assertEqual(result['source']['kind'],'csv')
            self.assertTrue(result['source']['complete'])
            self.assertEqual(rt.execute('state',{})['layers'][0]['geojson']['features'][0]['properties']['id'],'001')

    def test_arcgis_adapter_persists_sample_status(self):
        import mapdesk_runtime as rt
        sources=rt._load('mapdesk_sources')
        imported={'geojson':FC,'source':{'kind':'arcgis','complete':False,'total_count':20,'returned_count':1}}
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)), patch.object(sources,'fetch_arcgis',return_value=imported) as fetch:
            layer=rt.execute('import_arcgis',{'name':'Bounded fixture','url':'https://example.com/MapServer/1','bbox':[-103,31,-101,33],'limit':1})
            fetch.assert_called_once_with('https://example.com/MapServer/1',[-103,31,-101,33],max_features=1)
            self.assertFalse(layer['source']['complete'])
            self.assertEqual(rt.execute('state',{})['layers'][0]['source']['total_count'],20)

    def test_arcgis_where_clause_is_passed_to_the_source_module(self):
        import mapdesk_runtime as rt
        sources=rt._load('mapdesk_sources')
        imported={'geojson':FC,'source':{'kind':'arcgis','complete':False,'total_count':20,'returned_count':1,'where':"operator='A'"}}
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)), patch.object(sources,'fetch_arcgis',return_value=imported) as fetch:
            layer=rt.execute('import_arcgis',{'name':'Where fixture','url':'https://example.com/MapServer/1','bbox':[-103,31,-101,33],'limit':1,'where':"operator='A'"})
            self.assertEqual(fetch.call_args[0],('https://example.com/MapServer/1',[-103,31,-101,33]))
            self.assertEqual(fetch.call_args[1],{'where':"operator='A'",'max_features':1})
            self.assertEqual(layer['source']['where'],"operator='A'")

    def test_arcgis_missing_bbox_raises_the_source_valueerror_not_keyerror(self):
        import mapdesk_runtime as rt
        sources=rt._load('mapdesk_sources')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            with self.assertRaises(ValueError) as raised:
                rt.execute('import_arcgis',{'name':'No bbox','url':'https://example.com/MapServer/1'})
            self.assertIn('bbox', str(raised.exception))

    def test_summary_layer_query_and_reorder_actions(self):
        import mapdesk_runtime as rt
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            imported=rt.execute('import_geojson',{'name':'Wiring','geojson':FC})
            summary=rt.execute('summary',{})
            self.assertNotIn('geojson',summary['layers'][0])
            self.assertEqual(summary['layers'][0]['id'],imported['id'])
            self.assertEqual(summary['layers'][0]['order'],imported['order'])
            self.assertEqual(summary['layers'][0]['data_revision'],imported['data_revision'])
            self.assertEqual(summary['selection'],{'layer_id':None,'feature_ids':[],'bbox':None})
            self.assertEqual(summary['revision'],rt.execute('state',{})['revision'])
            layer=rt.execute('layer',{'layer_id':imported['id']})
            self.assertEqual(set(layer),{'layer_id','data_revision','geojson'})
            self.assertEqual(layer['data_revision'],imported['data_revision'])
            self.assertEqual(layer['geojson']['features'][0]['id'],'001')
            with self.assertRaises(ValueError): rt.execute('layer',{'layer_id':'missing'})
            query=rt.execute('query',{'layer_id':imported['id'],'where':[{'field':'operator','op':'eq','value':'A'}]})
            self.assertEqual(query,{'layer_id':imported['id'],'total':1,'feature_ids':['001'],'complete':True})
            self.assertEqual(rt.execute('query',{'layer_id':imported['id']})['feature_ids'],['001'])
            with self.assertRaises(ValueError): rt.execute('query',{'layer_id':imported['id'],'limit':0})
            with self.assertRaises(ValueError): rt.execute('query',{'layer_id':imported['id'],'where':[{'field':'nope','op':'eq','value':1}]})
            with self.assertRaises(ValueError): rt.execute('query',{'layer_id':'missing'})
            reordered=rt.execute('reorder',{'layer_ids':[imported['id']]})
            self.assertEqual([item['id'] for item in reordered],[imported['id']])
            self.assertNotIn('geojson',reordered[0])
            with self.assertRaisesRegex(ValueError,'exactly match'): rt.execute('reorder',{'layer_ids':[]})
            self.assertEqual(rt.execute('status',{})['layer_count'],1)

    def test_select_actions_support_radius_spatial_and_invert(self):
        import mapdesk_runtime as rt
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            points=rt.execute('import_geojson',{'name':'Points','geojson':FC})
            squares=rt.execute('import_geojson',{'name':'Squares','geojson':SQUARES})
            radius=rt.execute('select',{'layer_id':points['id'],'radius':{'center':[-102,32],'meters':1000}})
            self.assertEqual(radius['total'],1)
            self.assertEqual(radius['radius'],{'center':[-102,32],'meters':1000})
            spatial=rt.execute('select',{'layer_id':points['id'],'spatial':{'op':'intersects','layer_id':squares['id']}})
            self.assertEqual(spatial['total'],1)
            self.assertIsNone(spatial['bbox'])
            inverted=rt.execute('select',{'layer_id':points['id'],'feature_ids':['001'],'invert':True})
            self.assertEqual(inverted['total'],0)
            with self.assertRaises(ValueError): rt.execute('select',{'layer_id':points['id'],'bbox':[-103,31,-101,33],'radius':{'center':[-102,32],'meters':100}})
            with self.assertRaises(ValueError): rt.execute('select',{'layer_id':points['id'],'invert':True})
            with self.assertRaises(ValueError): rt.execute('select',{'layer_id':points['id'],'spatial':{'op':'intersects','layer_id':'missing'}})

    def test_geocode_batch_delegates_with_the_plugin_cache_dir(self):
        import mapdesk_runtime as rt
        sources=rt._load('mapdesk_sources')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)) as data_dir, patch.object(sources,'geocode_batch',return_value={'results':[[-102.0,32.0],None]},create=True) as geocode:
            result=rt.execute('geocode_batch',{'addresses':['Austin, TX','Nowhere']})
            self.assertEqual(result,{'results':[[-102.0,32.0],None]})
            self.assertEqual(geocode.call_args[0][0],['Austin, TX','Nowhere'])
            self.assertEqual(geocode.call_args[1].get('cache_dir'),pathlib.Path(directory))

    def test_analyze_action_measures_and_stores_result_layers(self):
        if not (ROOT/'mapdesk_ops.py').exists(): self.skipTest('mapdesk_ops has not landed yet')
        import mapdesk_runtime as rt
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            points=rt.execute('import_geojson',{'name':'Analysis','geojson':FC})
            squares=rt.execute('import_geojson',{'name':'Zones','geojson':SQUARES})
            measured=rt.execute('analyze',{'op':'measure','layer_id':points['id']})
            self.assertIn('features',measured)
            self.assertIn('total_area_km2',measured)
            self.assertIn('total_length_km',measured)
            self.assertEqual(rt.execute('status',{})['layer_count'],2)
            buffered=rt.execute('analyze',{'op':'buffer','layer_id':points['id'],'params':{'distance_m':100}})
            self.assertEqual(buffered['source']['kind'],'analysis')
            self.assertEqual(buffered['source']['op'],'buffer')
            self.assertEqual(buffered['source']['from_layer'],points['id'])
            self.assertEqual(buffered['source']['params'],{'distance_m':100})
            self.assertTrue(buffered['source']['complete'])
            self.assertGreaterEqual(buffered['feature_count'],1)
            self.assertEqual(buffered['name'],'Analysis · buffer')
            named=rt.execute('analyze',{'op':'hull','layer_id':squares['id'],'output_name':'Wrapped hull'})
            self.assertEqual(named['name'],'Wrapped hull')
            counted=rt.execute('analyze',{'op':'count_points','layer_id':squares['id'],'params':{'points_layer_id':points['id']}})
            self.assertEqual(counted['source']['op'],'count_points')
            self.assertEqual(rt.execute('status',{})['layer_count'],5)
            with self.assertRaises(ValueError): rt.execute('analyze',{'op':'buffer','layer_id':points['id'],'params':{'distance_m':0}})
            with self.assertRaises(ValueError): rt.execute('analyze',{'op':'explode','layer_id':points['id']})
            with self.assertRaises(ValueError): rt.execute('analyze',{'op':'buffer','layer_id':'missing','params':{'distance_m':10}})
            with self.assertRaises(ValueError): rt.execute('analyze',{'op':'clip','layer_id':squares['id'],'params':{}})

    def test_format_import_actions_delegate_to_mapdesk_formats(self):
        if not (ROOT/'mapdesk_formats.py').exists(): self.skipTest('mapdesk_formats has not landed yet')
        import base64, io, zipfile
        import mapdesk_runtime as rt
        kml='<kml><Document><Placemark><name>Spot</name><Point><coordinates>-102,32,0</coordinates></Point></Placemark></Document></kml>'
        gpx='<gpx><wpt lat="32" lon="-102"><name>Spot</name></wpt></gpx>'
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            kml_layer=rt.execute('import_kml',{'name':'KML fixture','text':kml})
            self.assertEqual(kml_layer['feature_count'],1)
            self.assertEqual(kml_layer['source']['kind'],'kml')
            gpx_layer=rt.execute('import_gpx',{'name':'GPX fixture','text':gpx})
            self.assertEqual(gpx_layer['feature_count'],1)
            self.assertEqual(gpx_layer['source']['kind'],'gpx')
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as archive:
                archive.writestr('doc.kml',kml)
            kmz_layer=rt.execute('import_kmz',{'name':'KMZ fixture','base64':base64.b64encode(buffer.getvalue()).decode('ascii')})
            self.assertEqual(kmz_layer['feature_count'],1)
            self.assertEqual(kmz_layer['source']['kind'],'kmz')
            with self.assertRaises(ValueError): rt.execute('import_kmz',{'name':'Bad','base64':'%%% not base64'})
            normalized=rt.execute('import_geojson',{'name':'Normalized','geojson':dict(FC)})
            self.assertIn('original_crs',normalized['source'])
            self.assertIn('swapped_warning',normalized['source'])
            self.assertFalse(normalized['source']['swapped_warning'])
            self.assertEqual(rt.execute('status',{})['layer_count'],4)

    def test_import_xlsx_action_builds_a_layer_from_a_workbook(self):
        if not (ROOT/'mapdesk_formats.py').exists(): self.skipTest('mapdesk_formats has not landed yet')
        import base64, io
        import mapdesk_runtime as rt
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            from openpyxl import Workbook
            book=Workbook(); sheet=book.active
            sheet.append(['latitude','longitude','name']); sheet.append([32,-102,'Spot'])
            buffer=io.BytesIO(); book.save(buffer)
            layer=rt.execute('import_xlsx',{'name':'XLSX fixture','base64':base64.b64encode(buffer.getvalue()).decode('ascii')})
            self.assertEqual(layer['feature_count'],1)
            self.assertEqual(layer['source']['kind'],'xlsx')
            self.assertEqual(rt.execute('status',{})['layer_count'],1)

if __name__=='__main__': unittest.main()
