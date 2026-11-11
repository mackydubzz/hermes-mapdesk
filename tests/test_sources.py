"""Stdlib-only adapter tests; live source probes are separate samples."""
import importlib
import json
import os
import tempfile
from pathlib import Path
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs


def sources():
    return importlib.import_module("mapdesk_sources")


class CSVTests(unittest.TestCase):
    def test_explicit_columns_preserve_text_and_coordinate_order(self):
        self.assertIsNotNone(importlib.util.find_spec("mapdesk_sources"))
        result = sources().csv_to_geojson(
            "api,latitude,longitude,name\n00123,31.9,-102.1,Example\n",
            "latitude", "longitude")
        self.assertEqual(result["geojson"], {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [-102.1, 31.9]},
            "properties": {"api": "00123", "latitude": "31.9", "longitude": "-102.1", "name": "Example"},
        }]})
        self.assertIsNone(result["source"]["url"])
        self.assertEqual(result["source"]["kind"], "csv")
        self.assertTrue(result["source"]["complete"])
        self.assertEqual(result["source"]["returned_count"], 1)
        self.assertEqual(result["source"]["latitude_column"], "latitude")
        self.assertEqual(result["source"]["longitude_column"], "longitude")
        self.assertIsNotNone(datetime.fromisoformat(result["source"]["fetched_at"]).tzinfo)

    def test_bom_is_allowed_but_ambiguous_or_missing_columns_rejected(self):
        result = sources().csv_to_geojson("\ufefflat,lon,id\n1,2,0007", "lat", "lon")
        self.assertEqual(result["geojson"]["features"][0]["properties"]["id"], "0007")
        for text, lat, lon in [
            ("lat,lon\n1,2", "", "lon"), ("lat,lon\n1,2", None, "lon"),
            ("lat,lon\n1,2", "lat", "lat"), ("lat,lon\n1,2", "LAT", "lon"),
            ("lat,lon,lat\n1,2,3", "lat", "lon"), ("lat,lon,\n1,2,3", "lat", "lon"),
            ("", "lat", "lon"), (b"lat,lon\n1,2", "lat", "lon"),
        ]:
            with self.subTest(text=text, lat=lat, lon=lon):
                with self.assertRaises(ValueError):
                    sources().csv_to_geojson(text, lat, lon)

    def test_bad_coordinate_rows_reject_entire_input_with_row_number(self):
        for row in ["91,0", "0,181", "-91,0", "0,-181", "nan,0", "0,inf", "1e999,0", ",2", "abc,2"]:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, r"row 3"):
                sources().csv_to_geojson("lat,lon\n1,2\n" + row, "lat", "lon")
        result = sources().csv_to_geojson("lat,lon\n-90,-180\n90,180", "lat", "lon")
        self.assertEqual(result["source"]["returned_count"], 2)

    def test_malformed_csv_rows_are_rejected_without_dropping_fields(self):
        for text in ['lat,lon,id\n1,2,3,4', 'lat,lon,id\n1,2', 'lat,lon,id\n1,2,"unterminated']:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'CSV'):
                sources().csv_to_geojson(text, 'lat', 'lon')
        result = sources().csv_to_geojson('lat,lon,id\n1,2,"a,b"', 'lat', 'lon')
        self.assertEqual(result['geojson']['features'][0]['properties']['id'], 'a,b')

    def test_csv_enforces_utf8_byte_and_row_caps(self):
        with self.assertRaisesRegex(ValueError, '10 MiB'):
            sources().csv_to_geojson('lat,lon,id\n1,2,' + 'é' * (5 * 1024 * 1024), 'lat', 'lon')
        with self.assertRaisesRegex(ValueError, '5000'):
            sources().csv_to_geojson('lat,lon\n' + '1,2\n' * 5001, 'lat', 'lon')
        result = sources().csv_to_geojson('lat,lon\n' + '1,2\n' * 5000, 'lat', 'lon')
        self.assertEqual(len(result['geojson']['features']), 5000)


class ArcGISTests(unittest.TestCase):
    URL = 'https://example.com/arcgis/rest/services/Wells/MapServer/1'
    BBOX = [-102.2, 31.8, -101.8, 32.1]

    def test_invalid_arguments_fail_before_dns_or_http(self):
        self.assertTrue(callable(getattr(sources(), 'fetch_arcgis', None)))
        urls = ['http://example.com/MapServer/1', 'https://user:pass@example.com/MapServer/1',
                self.URL + '?token=x', self.URL + '#fragment', self.URL + '?',
                self.URL + '/', self.URL + '/query', self.URL.replace('/1', ''),
                'https://example.com:8443/MapServer/1', 'https://example.com/../MapServer/1',
                'https://example.com/%2e%2e/MapServer/1', ' https://example.com/MapServer/1',
                'https://example.com/MapServer/1\n', None, 123]
        bboxes = [None, [], [1, 2, 3], [0, 0, 0, 1], [2, 0, 1, 2],
                  [-181, 0, 1, 2], [0, -91, 1, 2], [0, 0, 181, 2], [0, 0, 1, 91],
                  [0, 0, float('nan'), 2], [0, 0, float('inf'), 2], [False, 0, 1, 2], ['0', 0, 1, 2]]
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS must not run')):
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    sources().fetch_arcgis(url, self.BBOX)
            for bbox in bboxes:
                with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                    sources().fetch_arcgis(self.URL, bbox)
            for limit in [0, -1, 1001, True, 2.0, '20', None]:
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    sources().fetch_arcgis(self.URL, self.BBOX, limit)

    def test_nonpublic_dns_results_are_rejected_including_mixed_answers(self):
        import socket
        forbidden = ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.1',
                     '100.64.0.1', '192.0.2.1', '0.0.0.0', '224.0.0.1',
                     '::1', 'fe80::1', 'fc00::1', '2001:db8::1', '::ffff:8.8.8.8',
                     '2002:0808:0808::1']
        for ip in forbidden:
            for addresses in [[ip], ['8.8.8.8', ip]]:
                records = [(socket.AF_INET6 if ':' in address else socket.AF_INET,
                            socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 443))
                           for address in addresses]
                with self.subTest(addresses=addresses), patch('socket.getaddrinfo', return_value=records):
                    with self.assertRaisesRegex(ValueError, 'public'):
                        sources().fetch_arcgis(self.URL, self.BBOX, 20)
        with patch('socket.getaddrinfo', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'public'):
                sources().fetch_arcgis(self.URL, self.BBOX)

    def test_tls_connection_pins_numeric_address_but_verifies_original_hostname(self):
        import socket
        import ssl
        self.assertTrue(hasattr(sources(), '_PinnedHTTPSConnection'))
        records = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 443))]
        with patch('socket.socket') as factory, patch('ssl.SSLContext.wrap_socket') as wrap:
            connection = sources()._PinnedHTTPSConnection('example.com', records, timeout=12)
            self.assertTrue(connection._context.check_hostname)
            self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
            with patch('socket.getaddrinfo', side_effect=AssertionError('No re-resolution')):
                connection.connect()
            factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            factory.return_value.connect.assert_called_once_with(('8.8.8.8', 443))
            factory.return_value.settimeout.assert_called_with(12)
            wrap.assert_called_once_with(factory.return_value, server_hostname='example.com')
            connection.close()

    def test_transport_gets_json_with_identity_encoding_and_closes(self):
        import json
        from urllib.parse import urlsplit, parse_qs
        self.assertTrue(hasattr(sources(), '_request_json'))
        payload = {'count': 2}
        with patch.object(sources(), '_PinnedHTTPSConnection') as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 200
            response.getheader.return_value = None
            response.read.return_value = json.dumps(payload).encode()
            result = sources()._request_json(urlsplit(self.URL), ['pinned'], {'f': 'json', 'returnCountOnly': 'true'})
            self.assertEqual(result, payload)
            self.assertEqual(factory.call_args.args[:2], ('example.com', ['pinned']))
            method, path = connection.request.call_args.args
            self.assertEqual(method, 'GET')
            self.assertEqual(path.split('?')[0], '/arcgis/rest/services/Wells/MapServer/1/query')
            self.assertEqual(parse_qs(path.split('?')[1])['returnCountOnly'], ['true'])
            self.assertEqual(connection.request.call_args.kwargs['headers']['Accept-Encoding'], 'identity')
            connection.close.assert_called_once()

    def test_http_errors_and_redirects_are_rejected_without_following(self):
        from urllib.parse import urlsplit
        for status in [301, 302, 303, 307, 308, 400, 403, 404, 429, 500]:
            with self.subTest(status=status), patch.object(sources(), '_PinnedHTTPSConnection') as factory:
                response = factory.return_value.getresponse.return_value
                response.status = status
                response.read.return_value = b'{"count": 1}'
                with self.assertRaisesRegex(ValueError, 'HTTP'):
                    sources()._request_json(urlsplit(self.URL), ['pinned'], {'f': 'json'})
                response.read.assert_not_called()
                factory.assert_called_once()
                factory.return_value.close.assert_called_once()

    def test_response_size_and_compression_are_bounded(self):
        from urllib.parse import urlsplit
        cap = 10 * 1024 * 1024
        cases = [({'Content-Length': str(cap + 1)}, b'{}'),
                 ({'Content-Length': '-1'}, b'{}'), ({'Content-Length': 'nonsense'}, b'{}'),
                 ({'Content-Encoding': 'gzip'}, b'{}'),
                 ({}, b' ' * cap + b'{}')]
        for headers, body in cases:
            with self.subTest(headers=headers, size=len(body)), patch.object(sources(), '_PinnedHTTPSConnection') as factory:
                response = factory.return_value.getresponse.return_value
                response.status = 200
                response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
                response.read.return_value = body
                with self.assertRaisesRegex(ValueError, 'response'):
                    sources()._request_json(urlsplit(self.URL), ['pinned'], {'f': 'json'})
                factory.return_value.close.assert_called_once()

    def test_bounded_query_uses_count_to_establish_bbox_completeness(self):
        feature = {'type': 'Feature', 'id': 7, 'properties': {'API': '00123', 'note': 'untrusted text'},
                   'geometry': {'type': 'Point', 'coordinates': [-102.0, 31.9]}}
        collection = {'type': 'FeatureCollection', 'features': [feature]}
        with patch.object(sources(), '_resolve_public', return_value=['pinned']), \
             patch.object(sources(), '_request_json', side_effect=[{'count': 1}, collection]) as request:
            result = sources().fetch_arcgis(self.URL, self.BBOX, limit=20)
        self.assertEqual(result['geojson'], collection)
        metadata = result['source']
        self.assertTrue(metadata['complete'])
        self.assertEqual(metadata['url'], self.URL)
        self.assertEqual(metadata['kind'], 'arcgis')
        self.assertEqual(metadata['bbox'], self.BBOX)
        self.assertEqual(metadata['total_count'], 1)
        self.assertEqual(metadata['returned_count'], 1)
        self.assertEqual(metadata['limit'], 20)
        self.assertIsNone(metadata['exceededTransferLimit'])
        self.assertIsNotNone(datetime.fromisoformat(metadata['fetched_at']).tzinfo)
        self.assertEqual(request.call_count, 2)
        count, data = [call.args[2] for call in request.call_args_list]
        for query in [count, data]:
            self.assertEqual(query['geometry'], '-102.2,31.8,-101.8,32.1')
            self.assertEqual(query['inSR'], '4326')
            self.assertEqual(query['geometryType'], 'esriGeometryEnvelope')
            self.assertEqual(query['spatialRel'], 'esriSpatialRelIntersects')
            self.assertEqual(query['where'], '1=1')
        self.assertEqual(count['returnCountOnly'], 'true')
        self.assertEqual(count['f'], 'json')
        self.assertEqual(data['f'], 'geojson')
        self.assertEqual(data['outSR'], '4326')
        self.assertEqual(data['outFields'], '*')
        self.assertEqual(data['returnGeometry'], 'true')
        self.assertEqual(data['resultRecordCount'], '20')

    def test_incomplete_results_are_explicit_samples_even_when_flag_is_nested(self):
        feature = {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Point', 'coordinates': [-102, 32]}}
        for count, extras in [(21, {}), (1, {'exceededTransferLimit': True}),
                              (1, {'properties': {'exceededTransferLimit': True}}),
                              (2, {'exceededTransferLimit': False})]:
            collection = dict(type='FeatureCollection', features=[feature], **extras)
            with self.subTest(count=count, extras=extras), \
                 patch.object(sources(), '_resolve_public', return_value=['pinned']), \
                 patch.object(sources(), '_request_json', side_effect=[{'count': count}, collection]):
                result = sources().fetch_arcgis(self.URL, self.BBOX, 20)
            self.assertFalse(result['source']['complete'])
            self.assertTrue(result['source']['sample'])
            self.assertEqual(result['source']['completeness_basis'], 'count_query')
            if 'properties' in extras:
                self.assertIs(result['source']['exceededTransferLimit'], True)

    def test_invalid_count_is_rejected_before_fetching_features(self):
        for count in [None, -1, True, 1.0, '1']:
            with self.subTest(count=count), patch.object(sources(), '_resolve_public', return_value=['pinned']), \
                 patch.object(sources(), '_request_json', return_value={'count': count}) as request:
                with self.assertRaisesRegex(ValueError, 'count'):
                    sources().fetch_arcgis(self.URL, self.BBOX, 20)
                self.assertEqual(request.call_count, 1)

    def test_oversized_or_non_geojson_feature_results_are_rejected(self):
        feature = {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Point', 'coordinates': [-102, 32]}}
        for collection in [{'features': []}, {'type': 'FeatureCollection', 'features': None},
                           {'type': 'FeatureCollection', 'features': [feature] * 21},
                           {'type': 'FeatureCollection', 'features': [None]}]:
            with self.subTest(collection=collection), patch.object(sources(), '_resolve_public', return_value=['pinned']), \
                 patch.object(sources(), '_request_json', side_effect=[{'count': 1}, collection]):
                with self.assertRaisesRegex(ValueError, 'GeoJSON|limit'):
                    sources().fetch_arcgis(self.URL, self.BBOX, 20)

    def test_arcgis_wgs84_declaration_is_normalized_not_reprojected(self):
        declared={'type':'name','properties':{'name':'EPSG:4326'}}
        collection={'type':'FeatureCollection','features':[], 'crs':declared}
        with patch.object(sources(),'_resolve_public',return_value=['pinned']), patch.object(sources(),'_request_json',side_effect=[{'count':0},collection]):
            result=sources().fetch_arcgis(self.URL,self.BBOX,20)
        self.assertNotIn('crs',result['geojson'])
        self.assertEqual(result['source']['original_crs'],declared)
        self.assertEqual(result['source']['crs'],'EPSG:4326')
        bad={'type':'FeatureCollection','features':[], 'crs':{'type':'name','properties':{'name':'EPSG:3857'}}}
        with patch.object(sources(),'_resolve_public',return_value=['pinned']), patch.object(sources(),'_request_json',side_effect=[{'count':0},bad]), self.assertRaisesRegex(ValueError,'CRS'):
            sources().fetch_arcgis(self.URL,self.BBOX,20)

    def test_http_200_service_errors_and_invalid_json_are_rejected(self):
        from urllib.parse import urlsplit
        for body in [b'{"error":{"code":400,"message":"bad query"}}', b'[]', b'null',
                     b'{"count":NaN}', b'{"count":Infinity}', b'<html>not json</html>']:
            with self.subTest(body=body), patch.object(sources(), '_PinnedHTTPSConnection') as factory:
                response = factory.return_value.getresponse.return_value
                response.status = 200
                response.getheader.return_value = None
                response.read.return_value = body
                with self.assertRaises(ValueError):
                    sources()._request_json(urlsplit(self.URL), ['pinned'], {'f': 'json'})
                factory.return_value.close.assert_called_once()


class ArcGISWhereTests(unittest.TestCase):
    URL = 'https://example.com/arcgis/rest/services/Wells/MapServer/1'
    BBOX = [-102.2, 31.8, -101.8, 32.1]
    FIELDS = ['OBJECTID', 'STATE', 'Fuel_Type', 'NAME']

    def test_where_grammar_accepts_and_normalizes_valid_clauses(self):
        for clause, expected in [
            ("STATE='48'", "STATE = '48'"),
            ("state = '48'", "STATE = '48'"),
            ("OBJECTID > 100", "OBJECTID > 100"),
            ("OBJECTID >= -3.5", "OBJECTID >= -3.5"),
            ("OBJECTID <> 0", "OBJECTID <> 0"),
            ("OBJECTID >= 1.5E3", "OBJECTID >= 1.5E3"),
            ("Fuel_Type LIKE 'NG%'", "Fuel_Type LIKE 'NG%'"),
            ("fuel_type like 'ng%'", "Fuel_Type LIKE 'ng%'"),
            ("NAME = 'O''Brien''s Well'", "NAME = 'O''Brien''s Well'"),
            ("STATE IN ('TX', 'OK')", "STATE IN ('TX', 'OK')"),
            ("STATE IN ('48')", "STATE IN ('48')"),
            ("STATE IN (1, 2)", "STATE IN (1, 2)"),
            ("state='48' and fuel_type='NG'", "STATE = '48' AND Fuel_Type = 'NG'"),
            ("STATE='48' or name='x'", "STATE = '48' OR NAME = 'x'"),
            ("(STATE='48' OR NAME='x') AND OBJECTID<5", "(STATE = '48' OR NAME = 'x') AND OBJECTID < 5"),
            ("((STATE = '48'))", "((STATE = '48'))"),
            ("STATE = OBJECTID", "STATE = OBJECTID"),
        ]:
            with self.subTest(clause=clause):
                self.assertEqual(sources()._validate_where_clause(clause, self.FIELDS), expected)

    def test_where_grammar_rejects_injections_and_malformed_clauses(self):
        for clause in [
            "1=1; DROP TABLE Wells", "STATE = 'TX", "STATE = 'TX''", "'quoted' = STATE",
            "STATE = 'x' AND", "AND STATE = 'x'", "OR STATE = 'x'",
            "NOT STATE = 'x'", "STATE = 'x' BETWEEN 1 AND 2", "1 = 1", "'48' = '48'",
            "STATE = `x`", "STATE = 'x' --", "STATE = 'x' /* comment */",
            "STATE = 'x' || NAME", "(STATE = 'x'", "STATE = 'x')", "STATE = 'x') AND NAME = 'y'",
            "STATE LIKE 5", "STATE LIKE 'x' OR LIKE 'y'", "STATE IN 'x'", "STATE IN ('x'",
            "STATE IN (x", "STATE IN ()", "STATE IN ('x' 'y')",
            "UNKNOWNFIELD = 1", "STATE = 1 AND MISSING = 2", "STATE = 1 AND (NAME = 'y' OR FOO = 2)",
            "STATE = 'x' AND AND NAME = 'y'", "STATE = 'x' ESCAPE '!'",
            "STATE = 'x' extra", "", "   ",
        ]:
            with self.subTest(clause=clause), self.assertRaises(ValueError):
                sources()._validate_where_clause(clause, self.FIELDS)

    def test_where_rejections_explain_the_allowed_grammar(self):
        with self.assertRaisesRegex(ValueError, r'allowed'):
            sources()._validate_where_clause("1=1; DROP TABLE Wells", self.FIELDS)
        with self.assertRaisesRegex(ValueError, 'Unknown field'):
            sources()._validate_where_clause("MISSING = 1", self.FIELDS)

    def test_metadata_request_hits_layer_url_without_query_suffix(self):
        from urllib.parse import urlsplit
        with patch.object(sources(), '_PinnedHTTPSConnection') as factory:
            response = factory.return_value.getresponse.return_value
            response.status = 200
            response.getheader.return_value = None
            response.read.return_value = b'{"fields": []}'
            payload = sources()._request_json(urlsplit(self.URL), ['pinned'], {'f': 'json'}, suffix='')
            method, path = factory.return_value.request.call_args.args
            self.assertEqual(method, 'GET')
            self.assertEqual(path, '/arcgis/rest/services/Wells/MapServer/1?f=json')
            self.assertEqual(payload, {'fields': []})

    def test_fetch_arcgis_where_fetches_metadata_first_and_rewrites_field_casing(self):
        metadata = {'fields': [{'name': 'OBJECTID'}, {'name': 'STATE'}], 'maxRecordCount': 500}
        collection = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {'STATE': '48'},
             'geometry': {'type': 'Point', 'coordinates': [-102, 32]}}]}
        with patch.object(sources(), '_resolve_public', return_value=['pinned']), \
             patch.object(sources(), '_request_json',
                          side_effect=[metadata, {'count': 1}, collection]) as request:
            result = sources().fetch_arcgis(self.URL, self.BBOX, 20, where='state = \'48\'', max_features=100)
        self.assertEqual(result['geojson'], collection)
        self.assertEqual(result['source']['where'], "STATE = '48'")
        self.assertEqual(result['source']['requested_max'], 100)
        self.assertEqual(result['source']['limit'], 20)
        self.assertTrue(result['source']['complete'])
        self.assertEqual(request.call_count, 3)
        metadata_call, count_call, data_call = request.call_args_list
        self.assertEqual(metadata_call.args[2], {'f': 'json'})
        self.assertEqual(metadata_call.kwargs.get('suffix'), '')
        self.assertEqual(count_call.args[2]['where'], "STATE = '48'")
        self.assertEqual(count_call.args[2]['returnCountOnly'], 'true')
        self.assertEqual(data_call.args[2]['where'], "STATE = '48'")
        self.assertEqual(data_call.args[2]['resultRecordCount'], '20')
        self.assertNotIn('resultOffset', data_call.args[2])

    def test_invalid_where_rejects_before_any_query(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS must not run')):
            for where in [123, b'STATE=1', ('STATE',)]:
                with self.subTest(where=where), self.assertRaises(ValueError):
                    sources().fetch_arcgis(self.URL, self.BBOX, 20, where=where)
        metadata = {'fields': [{'name': 'STATE'}]}
        for where in ['MISSING = 1', "STATE = '48", '1=1; DROP TABLE x', 'NOTHING HERE', '']:
            with self.subTest(where=where), \
                 patch.object(sources(), '_resolve_public', return_value=['pinned']), \
                 patch.object(sources(), '_request_json', return_value=metadata) as request:
                with self.assertRaises(ValueError):
                    sources().fetch_arcgis(self.URL, self.BBOX, 20, where=where)
                self.assertEqual(request.call_count, 1)  # metadata only, no count or page query

    def test_blank_where_behaves_like_no_filter(self):
        collection = {'type': 'FeatureCollection', 'features': []}
        with patch.object(sources(), '_resolve_public', return_value=['pinned']), \
             patch.object(sources(), '_request_json', side_effect=[{'count': 0}, collection]) as request:
            result = sources().fetch_arcgis(self.URL, self.BBOX, 20, where='   ')
        self.assertEqual(request.call_count, 2)  # no metadata fetch for a blank clause
        self.assertIsNone(result['source']['where'])
        self.assertEqual(request.call_args_list[0].args[2]['where'], '1=1')

    def test_max_features_is_validated_before_dns(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS must not run')):
            for max_features in [0, -1, 5001, True, 2.5, '5000', None]:
                with self.subTest(max_features=max_features), self.assertRaises(ValueError):
                    sources().fetch_arcgis(self.URL, self.BBOX, 20, max_features=max_features)


class ArcGISPaginationTests(unittest.TestCase):
    URL = 'https://example.com/arcgis/rest/services/Wells/MapServer/1'
    BBOX = [-102.2, 31.8, -101.8, 32.1]

    @staticmethod
    def feature(oid):
        return {'type': 'Feature', 'id': oid, 'properties': {'OBJECTID': oid},
                'geometry': {'type': 'Point', 'coordinates': [-102.0, 32.0]}}

    @classmethod
    def page(cls, oids, **extras):
        return dict(type='FeatureCollection', features=[cls.feature(oid) for oid in oids], **extras)

    def fetch(self, side_effect):
        with patch.object(sources(), '_resolve_public', return_value=['pinned']), \
             patch.object(sources(), '_request_json', side_effect=side_effect) as request:
            result = sources().fetch_arcgis(self.URL, self.BBOX, 1000, where='OBJECTID >= 1')
        return result, request

    def test_where_pagination_steps_by_service_max_record_count(self):
        metadata = {'fields': [{'name': 'OBJECTID'}], 'maxRecordCount': 3}
        pages = [self.page([1, 2, 3], exceededTransferLimit=True),
                 self.page([4, 5, 6], exceededTransferLimit=False)]
        result, request = self.fetch([metadata, {'count': 6}, *pages])
        self.assertEqual([f['id'] for f in result['geojson']['features']], [1, 2, 3, 4, 5, 6])
        self.assertEqual(result['source']['pages'], 2)
        self.assertTrue(result['source']['complete'])
        self.assertEqual(result['source']['requested_max'], 5000)
        self.assertEqual(result['source']['total_count'], 6)
        self.assertEqual(result['source']['returned_count'], 6)
        queries = [call.args[2] for call in request.call_args_list]
        self.assertEqual(queries[2]['resultRecordCount'], '3')
        self.assertNotIn('resultOffset', queries[2])
        self.assertEqual(queries[3]['resultRecordCount'], '3')
        self.assertEqual(queries[3]['resultOffset'], '3')
        for query in queries[1:]:
            self.assertEqual(query['where'], 'OBJECTID >= 1')

    def test_max_features_truncates_pagination_into_a_sample(self):
        metadata = {'fields': [{'name': 'OBJECTID'}], 'maxRecordCount': 3}
        pages = [self.page([1, 2, 3], exceededTransferLimit=True),
                 self.page([4, 5], exceededTransferLimit=True),
                 self.page([6, 7, 8], exceededTransferLimit=True)]
        with patch.object(sources(), '_resolve_public', return_value=['pinned']), \
             patch.object(sources(), '_request_json', side_effect=[metadata, {'count': 8}, *pages]):
            result = sources().fetch_arcgis(self.URL, self.BBOX, 1000, where='OBJECTID >= 1',
                                            max_features=5)
        self.assertEqual([f['id'] for f in result['geojson']['features']], [1, 2, 3, 4, 5])
        self.assertEqual(result['source']['pages'], 2)
        self.assertFalse(result['source']['complete'])
        self.assertTrue(result['source']['sample'])
        self.assertEqual(result['source']['requested_max'], 5)

    def test_short_page_without_transfer_limit_completes(self):
        metadata = {'fields': [{'name': 'OBJECTID'}], 'maxRecordCount': 3}
        result, request = self.fetch([metadata, {'count': 2}, self.page([1, 2])])
        self.assertEqual(result['source']['pages'], 1)
        self.assertTrue(result['source']['complete'])
        self.assertEqual(result['source']['returned_count'], 2)

    def test_short_page_with_transfer_limit_keeps_paging(self):
        metadata = {'fields': [{'name': 'OBJECTID'}]}  # no maxRecordCount -> page size 1000
        pages = [self.page([1, 2], exceededTransferLimit=True),
                 self.page([3, 4], exceededTransferLimit=True),
                 self.page([5])]
        result, request = self.fetch([metadata, {'count': 5}, *pages])
        self.assertEqual([f['id'] for f in result['geojson']['features']], [1, 2, 3, 4, 5])
        self.assertTrue(result['source']['complete'])
        self.assertEqual(result['source']['pages'], 3)
        queries = [call.args[2] for call in request.call_args_list]
        self.assertEqual(queries[3]['resultOffset'], '2')
        self.assertEqual(queries[4]['resultOffset'], '4')

    def test_service_ignoring_result_offset_does_not_duplicate_features(self):
        metadata = {'fields': [{'name': 'OBJECTID'}]}
        pages = [self.page([1, 2], exceededTransferLimit=True),
                 self.page([1, 2], exceededTransferLimit=True)]
        result, request = self.fetch([metadata, {'count': 4}, *pages])
        self.assertEqual([f['id'] for f in result['geojson']['features']], [1, 2])
        self.assertFalse(result['source']['complete'])

    def test_pagination_without_where_uses_default_page_size_and_no_metadata_call(self):
        pages = [self.page([1, 2, 3], exceededTransferLimit=True),
                 self.page([4, 5])]
        with patch.object(sources(), '_resolve_public', return_value=['pinned']), \
             patch.object(sources(), '_request_json', side_effect=[{'count': 5}, *pages]) as request:
            result = sources().fetch_arcgis(self.URL, self.BBOX)
        self.assertEqual(request.call_count, 3)  # count + two pages, no metadata call
        self.assertTrue(result['source']['complete'])
        queries = [call.args[2] for call in request.call_args_list]
        self.assertEqual(queries[1]['resultRecordCount'], '1000')
        self.assertNotIn('resultOffset', queries[1])
        self.assertEqual(queries[2]['resultRecordCount'], '1000')
        self.assertEqual(queries[2]['resultOffset'], '3')
        self.assertEqual(queries[2]['where'], '1=1')


class GeocodeTests(unittest.TestCase):
    @staticmethod
    def nominatim_connection(status=200, body=b'[{"lon": "-102.1", "lat": "31.9"}]'):
        connection = MagicMock()
        response = MagicMock()
        response.status = status
        response.getheader.return_value = None
        response.read.return_value = body
        connection.getresponse.return_value = response
        return connection

    def batch(self, addresses, connections, cache_dir=None, clock=1234.0, last_request=0.0):
        with patch('http.client.HTTPSConnection', side_effect=list(connections)), \
             patch('time.monotonic', return_value=clock), patch('time.sleep'), \
             patch.object(sources(), '_geocode_last_request', last_request):
            return sources().geocode_batch(addresses, cache_dir=cache_dir)

    def test_input_shape_is_validated_before_any_network(self):
        for addresses in [[], ['a'] * 26, ['a', 5], [5], ['a', None], ['a' * 301],
                          'single address', 42, None, [b'bytes']]:
            with self.subTest(addresses=addresses), \
                 patch('http.client.HTTPSConnection', side_effect=AssertionError('network must not run')):
                with self.assertRaises(ValueError):
                    sources().geocode_batch(addresses)

    def test_results_align_with_input_and_null_on_miss_or_error(self):
        connections = [
            self.nominatim_connection(body=b'[{"lon": "-102.1", "lat": "31.9"}]'),
            self.nominatim_connection(body=b'[]'),
            self.nominatim_connection(status=503),
            self.nominatim_connection(body=b'{"error": "not a list"}'),
        ]
        with patch('http.client.HTTPSConnection', side_effect=connections) as factory, \
             patch('time.monotonic', return_value=1234.0), patch('time.sleep'), \
             patch.object(sources(), '_geocode_last_request', 0.0):
            result = sources().geocode_batch(['Alpha', 'Beta', 'Gamma', 'Delta'])
        self.assertEqual(result, {'results': [{'lon': -102.1, 'lat': 31.9}, None, None, None]})
        self.assertIsInstance(result['results'][0]['lon'], float)
        self.assertIsInstance(result['results'][0]['lat'], float)
        self.assertEqual(factory.call_args.args, ('nominatim.openstreetmap.org',))
        self.assertEqual(factory.call_args.kwargs, {'timeout': 20})
        first = connections[0]
        method, path = first.request.call_args.args
        self.assertEqual(method, 'GET')
        self.assertTrue(path.startswith('/search?'))
        query = parse_qs(path[len('/search?'):])
        self.assertEqual(query['format'], ['jsonv2'])
        self.assertEqual(query['limit'], ['1'])
        self.assertEqual(query['q'], ['Alpha'])
        headers = first.request.call_args.kwargs['headers']
        self.assertEqual(headers['User-Agent'], 'MapDesk/0.2 (Hermes Desktop plugin; GIS import)')
        self.assertEqual(headers['Accept-Language'], 'en')
        self.assertEqual(headers['Accept-Encoding'], 'identity')
        for connection in connections:
            connection.close.assert_called_once()

    def test_rate_guard_sleeps_1_1s_between_nominatim_requests(self):
        connections = [self.nominatim_connection(), self.nominatim_connection()]
        with patch('http.client.HTTPSConnection', side_effect=connections), \
             patch('time.monotonic', return_value=100.0), patch('time.sleep') as sleep, \
             patch.object(sources(), '_geocode_last_request', 98.0):
            sources().geocode_batch(['A', 'B'])
        self.assertEqual(sleep.call_count, 1)
        self.assertAlmostEqual(sleep.call_args.args[0], 1.1)
        with patch('http.client.HTTPSConnection', side_effect=[self.nominatim_connection()]), \
             patch('time.monotonic', return_value=100.0), patch('time.sleep') as sleep, \
             patch.object(sources(), '_geocode_last_request', 100.0):
            sources().geocode_batch(['A'])
        self.assertEqual(sleep.call_count, 1)  # guard persists across batches
        self.assertAlmostEqual(sleep.call_args.args[0], 1.1)

    def test_batch_time_budget_nulls_the_remaining_addresses(self):
        monotonic_values = iter([0.0, 5.0, 5.0, 61.0])
        with patch('http.client.HTTPSConnection',
                   side_effect=[self.nominatim_connection()]) as factory, \
             patch('time.monotonic', side_effect=lambda: next(monotonic_values)), \
             patch('time.sleep'), patch.object(sources(), '_geocode_last_request', 0.0):
            result = sources().geocode_batch(['A', 'B'])
        self.assertEqual(result['results'][0], {'lon': -102.1, 'lat': 31.9})
        self.assertIsNone(result['results'][1])
        self.assertEqual(factory.call_count, 1)

    def test_batch_of_25_is_allowed_and_rate_guarded(self):
        addresses = ['x' * 300] + [f'a{i}' for i in range(24)]
        connections = [self.nominatim_connection() for _ in addresses]
        with patch('http.client.HTTPSConnection', side_effect=connections) as factory, \
             patch('time.monotonic', return_value=100.0), patch('time.sleep') as sleep, \
             patch.object(sources(), '_geocode_last_request', 0.0):
            result = sources().geocode_batch(addresses)
        self.assertEqual(factory.call_count, 25)
        self.assertEqual(len(result['results']), 25)
        self.assertEqual(sleep.call_count, 24)
        for call in sleep.call_args_list:
            self.assertAlmostEqual(call.args[0], 1.1)

    def test_duplicate_addresses_geocode_once(self):
        with patch('http.client.HTTPSConnection',
                   side_effect=[self.nominatim_connection()]) as factory, \
             patch('time.monotonic', return_value=1234.0), patch('time.sleep'), \
             patch.object(sources(), '_geocode_last_request', 0.0):
            result = sources().geocode_batch(['Dup', 'Dup'])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(result['results'], [{'lon': -102.1, 'lat': 31.9}] * 2)

    def test_transport_failure_yields_null_without_raising(self):
        connection = MagicMock()
        connection.request.side_effect = OSError('boom')
        with patch('http.client.HTTPSConnection', side_effect=[connection]), \
             patch('time.monotonic', return_value=1234.0), patch('time.sleep'), \
             patch.object(sources(), '_geocode_last_request', 0.0):
            result = sources().geocode_batch(['Broken'])
        self.assertEqual(result['results'], [None])

    def test_cache_round_trip_skips_network_on_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            result = self.batch(['Repeat Me'], [self.nominatim_connection()], cache_dir=cache_dir)
            self.assertEqual(result['results'], [{'lon': -102.1, 'lat': 31.9}])
            stored = json.loads((cache_dir / 'geocode_cache.json').read_text(encoding='utf-8'))
            self.assertEqual(stored, {'Repeat Me': {'lon': -102.1, 'lat': 31.9}})
            with patch('http.client.HTTPSConnection',
                       side_effect=AssertionError('cache must serve the repeat')), \
                 patch('time.monotonic', return_value=1234.0), patch('time.sleep'), \
                 patch.object(sources(), '_geocode_last_request', 0.0):
                again = sources().geocode_batch(['Repeat Me'], cache_dir=cache_dir)
            self.assertEqual(again['results'], [{'lon': -102.1, 'lat': 31.9}])

    def test_invalid_cache_contents_are_ignored_and_rewritten(self):
        for junk in ['{not json', '[]', 'null', '{"Stale": "entry"}',
                     '{"Stale": {"lon": "x", "lat": 1}}']:
            with self.subTest(junk=junk), tempfile.TemporaryDirectory() as directory:
                cache_dir = Path(directory)
                (cache_dir / 'geocode_cache.json').write_text(junk, encoding='utf-8')
                result = self.batch(['Addr'], [self.nominatim_connection()], cache_dir=cache_dir)
                self.assertEqual(result['results'], [{'lon': -102.1, 'lat': 31.9}])
                stored = json.loads((cache_dir / 'geocode_cache.json').read_text(encoding='utf-8'))
                self.assertEqual(stored, {'Addr': {'lon': -102.1, 'lat': 31.9}})

    def test_no_cache_dir_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.batch(['Nowhere'], [self.nominatim_connection()], cache_dir=None)
            self.assertEqual(result['results'], [{'lon': -102.1, 'lat': 31.9}])
            self.assertEqual(os.listdir(directory), [])


if __name__ == "__main__":
    unittest.main()
