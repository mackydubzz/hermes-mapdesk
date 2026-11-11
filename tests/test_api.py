import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
HERMES=pathlib.Path.home()/'AppData/Local/hermes/hermes-agent'
sys.path.insert(0,str(HERMES))


def load_api():
    path=ROOT/'dashboard/plugin_api.py'
    assert path.is_file(),'Plugin backend routes are missing'
    spec=importlib.util.spec_from_file_location('mapdesk_test_api',path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI(); app.include_router(mod.router)
    return mod,TestClient(app)


def ws_receive(ws,timeout=5.0):
    """receive_json with a timeout (X1); None when nothing arrives in time.

    starlette's WebSocketTestSession has no timeout-aware receive, so this
    races its portal receive stream against a clock via anyio.
    """
    import anyio
    import json
    async def _receive():
        with anyio.move_on_after(timeout):
            return await ws._send_rx.receive()
        return None
    message=ws.portal.call(_receive)
    if message is None:
        return None
    ws._raise_on_close(message)
    return json.loads(message['text'])


class ApiTests(unittest.TestCase):
    def test_bootstrap_never_exposes_a_secret_mapbox_token(self):
        mod,client=load_api()
        with patch('agent.secret_scope.get_secret',return_value='sk.test-only-not-a-real-token'):
            result=client.get('/renderer')
            self.assertEqual(result.status_code,200)
            self.assertIn('MapDesk map',result.json()['html'])
            status=client.get('/status').json()
            self.assertFalse(status['token_configured'])
            self.assertNotIn('sk.test',str(status))
            bootstrap=client.get('/bootstrap').json()
            self.assertEqual(bootstrap['token'],'')
            self.assertNotIn('sk.test',str(bootstrap))
            self.assertIs(bootstrap['token_rejected'],True)

    def test_saved_token_is_trimmed_and_blank_counts_as_none(self):
        mod,client=load_api()
        public='pk.eyJtes...oken'
        with patch('agent.secret_scope.get_secret',return_value='  '+public+'\n'):
            self.assertEqual(client.get('/bootstrap').json(),{'token':public,'basemap':'mapbox','token_rejected':False})
            self.assertTrue(client.get('/status').json()['token_configured'])
        for blank in ('   ','\n',None):
            with self.subTest(blank=repr(blank)), patch('agent.secret_scope.get_secret',return_value=blank):
                self.assertEqual(client.get('/bootstrap').json(),{'token':'','basemap':'keyless','token_rejected':False})
        with patch('agent.secret_scope.get_secret',return_value='not-a-token'):
            reply=client.get('/bootstrap').json()
            self.assertEqual(reply,{'token':'','basemap':'keyless','token_rejected':True})
            self.assertNotIn('not-a-token',str(reply))

    def test_http_command_and_tools_read_the_same_store(self):
        import tempfile
        import mapdesk_runtime as rt
        mod,client=load_api()
        scratch=pathlib.Path.home()/'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            response=client.post('/command',json={'action':'import_csv','args':{'name':'HTTP fixture','text':'lat,lon\n32,-102','latitude_column':'lat','longitude_column':'lon'}})
            self.assertEqual(response.status_code,200)
            self.assertEqual(rt.execute('status',{})['layer_count'],1)
            layer=response.json()
            self.assertEqual(client.get('/state').json()['layers'][0]['id'],layer['id'])
            selected=client.post('/command',json={'action':'select','args':{'layer_id':layer['id'],'bbox':[-103,31,-101,33]}})
            self.assertEqual(selected.json()['total'],1)
            self.assertEqual(rt.execute('selection',{})['total'],1)
            self.assertEqual(client.post('/command',json={'action':'arbitrary_code','args':{}}).status_code,400)

    def test_bootstrap_and_renderer_choose_basemap_by_token_presence(self):
        mod,client=load_api()
        public='pk.eyJtest-only-not-a-real-token'
        with patch('agent.secret_scope.get_secret',return_value=public):
            bootstrap=client.get('/bootstrap')
            self.assertEqual(bootstrap.status_code,200)
            self.assertEqual(bootstrap.headers['cache-control'],'no-store')
            self.assertEqual(bootstrap.json(),{'token':public,'basemap':'mapbox','token_rejected':False})
            rendered=client.get('/renderer').json()
            self.assertEqual(rendered['kind'],'mapbox')
            self.assertEqual(rendered['html'],(ROOT/'desktop'/'map.html').read_text(encoding='utf-8'))
        for secret in ('sk.test-only-not-a-real-token',''):
            with self.subTest(secret=secret[:2]), patch('agent.secret_scope.get_secret',return_value=secret):
                self.assertEqual(client.get('/bootstrap').json(),{'token':'','basemap':'keyless','token_rejected':bool(secret)})
                rendered=client.get('/renderer').json()
                self.assertEqual(rendered['kind'],'keyless')
                keyless=ROOT/'desktop'/'map_keyless.html'
                expected=keyless if keyless.is_file() else ROOT/'desktop'/'map.html'
                self.assertEqual(rendered['html'],expected.read_text(encoding='utf-8'))

    def test_summary_layer_and_geocode_endpoints(self):
        import tempfile
        mod,client=load_api()
        scratch=pathlib.Path.home()/'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            response=client.post('/command',json={'action':'import_csv','args':{'name':'HTTP fixture','text':'lat,lon\n32,-102','latitude_column':'lat','longitude_column':'lon'}})
            layer=response.json()
            summary=client.get('/summary').json()
            self.assertNotIn('geojson',summary['layers'][0])
            self.assertEqual(summary['layers'][0]['id'],layer['id'])
            self.assertEqual(summary['layers'][0]['data_revision'],layer['data_revision'])
            self.assertEqual(summary['layers'][0]['order'],0)
            full=client.get('/layer/'+layer['id']).json()
            self.assertEqual(set(full),{'layer_id','data_revision','geojson'})
            self.assertEqual(full['geojson']['features'][0]['geometry']['type'],'Point')
            self.assertEqual(client.get('/layer/missing').status_code,400)
            sources=mod.runtime()._load('mapdesk_sources')
            with patch.object(sources,'geocode_batch',return_value={'results':[[-102.0,32.0]]},create=True) as geocode:
                resolved=client.post('/geocode',json={'addresses':['Austin, TX']})
                self.assertEqual(resolved.status_code,200)
                self.assertEqual(resolved.json(),{'results':[[-102.0,32.0]]})
                self.assertEqual(geocode.call_args[1].get('cache_dir'),pathlib.Path(directory))
            self.assertEqual(client.post('/geocode',json={'addresses':'Austin, TX'}).status_code,400)
            self.assertEqual(client.post('/geocode',json={'nope':[]}).status_code,400)

    def test_command_cap_is_16MiB(self):
        import tempfile
        mod,client=load_api()
        scratch=pathlib.Path.home()/'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            oversized=client.post('/command',json={'action':'selection','args':{'pad':'x'*(13*1024*1024)}})
            self.assertEqual(oversized.status_code,200)
            rejected=client.post('/command',json={'action':'selection','args':{'pad':'x'*(17*1024*1024)}})
            self.assertEqual(rejected.status_code,413)
            self.assertEqual(rejected.json()['detail'],'Command exceeds 16 MiB')

    def test_events_socket_hello_revision_push_and_error_silence(self):
        import tempfile
        mod,client=load_api()
        scratch=pathlib.Path.home()/'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            with client.websocket_connect('/events') as ws:
                self.assertEqual(ws_receive(ws),{'type':'hello','revision':0})
                saved=client.post('/command',json={'action':'save','args':{'name':'Live sync fixture'}})
                self.assertEqual(saved.status_code,200)
                self.assertEqual(saved.json(),{'name':'Live sync fixture'})
                self.assertEqual(ws_receive(ws),{'type':'revision','revision':1})
                self.assertEqual(client.get('/summary').status_code,200)
                self.assertEqual(client.post('/command',json={'action':'arbitrary_code','args':{}}).status_code,400)
                self.assertIsNone(ws_receive(ws,1.0))
                renamed=client.post('/command',json={'action':'save','args':{'name':'Renamed fixture'}})
                self.assertEqual(renamed.status_code,200)
                self.assertEqual(ws_receive(ws),{'type':'revision','revision':2})
            self.assertEqual(mod._EVENT_SOCKETS,set())

    def test_events_socket_broadcast_reaches_every_socket(self):
        import tempfile
        mod,client=load_api()
        scratch=pathlib.Path.home()/'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as directory, patch('plugins.plugin_storage.plugin_data_dir',return_value=pathlib.Path(directory)):
            with client.websocket_connect('/events') as first, client.websocket_connect('/events') as second:
                self.assertEqual(ws_receive(first),{'type':'hello','revision':0})
                self.assertEqual(ws_receive(second),{'type':'hello','revision':0})
                self.assertEqual(client.post('/command',json={'action':'save','args':{'name':'Fanout fixture'}}).status_code,200)
                self.assertEqual(ws_receive(first),{'type':'revision','revision':1})
                self.assertEqual(ws_receive(second),{'type':'revision','revision':1})
            self.assertEqual(mod._EVENT_SOCKETS,set())
            with client.websocket_connect('/events') as third:
                self.assertEqual(ws_receive(third),{'type':'hello','revision':1})
                self.assertEqual(client.post('/command',json={'action':'save','args':{'name':'After close'}}).status_code,200)
                self.assertEqual(ws_receive(third),{'type':'revision','revision':2})

if __name__=='__main__': unittest.main()
