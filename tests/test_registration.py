"""Catalog-contract tests. Registration must not touch network or disk."""
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
HERMES = pathlib.Path.home() / 'AppData/Local/hermes/hermes-agent'
if str(HERMES) not in sys.path:
    sys.path.insert(0, str(HERMES))


def registered_tools():
    entry = ROOT / '__init__.py'
    assert entry.is_file(), 'MapDesk plugin entry point is missing'
    spec = importlib.util.spec_from_file_location('mapdesk_test_plugin', entry,
                                                 submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    registrations = []
    class Context:
        def register_tool(self, **kwargs):
            registrations.append(kwargs)
    with patch('socket.socket', side_effect=AssertionError('network at registration')), \
         patch('pathlib.Path.mkdir', side_effect=AssertionError('disk write at registration')):
        module.register(Context())
    assert registrations, 'no tools were registered'
    return registrations


class RegistrationTests(unittest.TestCase):
    def test_register_declares_status_without_side_effects(self):
        entry = ROOT / '__init__.py'
        self.assertTrue(entry.is_file(), 'MapDesk plugin entry point is missing')
        spec = importlib.util.spec_from_file_location('mapdesk_test_plugin', entry,
                                                     submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        registrations = []
        class Context:
            def register_tool(self, **kwargs):
                registrations.append(kwargs)
        with patch('socket.socket', side_effect=AssertionError('network at registration')), \
             patch('pathlib.Path.mkdir', side_effect=AssertionError('disk write at registration')):
            module.register(Context())
        self.assertTrue(registrations)
        self.assertIn('mapdesk_status', [r['name'] for r in registrations])
        for required in ['mapdesk_import','mapdesk_layer','mapdesk_selection','mapdesk_view']:
            self.assertIn(required, [r['name'] for r in registrations])
        import yaml
        manifest = yaml.safe_load((ROOT / 'plugin.yaml').read_text(encoding='utf-8'))
        self.assertEqual(sorted(manifest['provides_tools']), sorted(r['name'] for r in registrations))
        for row in registrations:
            self.assertTrue(callable(row['handler']))
            self.assertEqual(row['toolset'], 'mapdesk')
            self.assertEqual(row['schema']['parameters']['type'], 'object')

    def test_manifest_declares_version_0_3_0_and_new_dependencies(self):
        import yaml
        manifest = yaml.safe_load((ROOT / 'plugin.yaml').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], '0.3.0')
        for dependency in ('shapely>=2.1,<3', 'pyproj>=3.7,<4',
                           'openpyxl>=3.1,<4', 'pyshp>=2.3,<3'):
            self.assertIn(dependency, manifest['python_dependencies'])

    def test_new_tool_schemas_match_the_contract(self):
        registrations = registered_tools()
        tools = {row['name']: row for row in registrations}
        for name in ('mapdesk_query', 'mapdesk_analyze'):
            self.assertIn(name, tools)
        properties = lambda name: tools[name]['schema']['parameters']['properties']
        imported = properties('mapdesk_import')
        self.assertEqual(sorted(imported['kind']['enum']),
                         sorted(['geojson', 'csv', 'arcgis', 'kml', 'kmz', 'gpx', 'xlsx', 'shp']))
        self.assertEqual(imported['limit']['maximum'], 5000)
        for key in ('base64', 'sheet', 'where'):
            self.assertIn(key, imported)
        query = properties('mapdesk_query')
        self.assertEqual(tools['mapdesk_query']['schema']['parameters']['required'], ['layer_id'])
        self.assertEqual(query['where']['items']['properties']['op']['enum'],
                         ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'like', 'isnull'])
        self.assertEqual(query['spatial']['required'], ['op', 'layer_id'])
        self.assertEqual(query['spatial']['properties']['op']['enum'], ['intersects', 'within', 'contains'])
        self.assertEqual(query['limit']['maximum'], 1000)
        description = tools['mapdesk_query']['description'].lower()
        self.assertIn('read-only', description)
        self.assertIn('not a draw filter', description)
        self.assertIn('mapdesk_selection', description)
        analyze = properties('mapdesk_analyze')
        self.assertEqual(tools['mapdesk_analyze']['schema']['parameters']['required'], ['op', 'layer_id'])
        self.assertEqual(sorted(analyze['op']['enum']),
                         sorted(['buffer', 'dissolve', 'centroid', 'hull', 'clip', 'count_points', 'measure']))
        for key in ('overlay_layer_id', 'points_layer_id', 'distance_m', 'by_field', 'output_name'):
            self.assertIn(key, analyze)
        self.assertEqual(analyze['distance_m']['maximum'], 100000)
        self.assertIn('permanent', tools['mapdesk_analyze']['description'].lower())
        selection = properties('mapdesk_selection')
        for key in ('radius', 'spatial', 'invert'):
            self.assertIn(key, selection)
        self.assertEqual(sorted(selection['radius']['required']), ['center', 'meters'])
        self.assertEqual(selection['radius']['properties']['meters']['minimum'], 10)
        self.assertEqual(selection['radius']['properties']['meters']['maximum'], 5000000)
        self.assertEqual(selection['spatial']['required'], ['op', 'layer_id'])
        layer = properties('mapdesk_layer')
        self.assertIn('reorder', layer['action']['enum'])
        self.assertIn('layer_ids', layer)
        style = layer['changes']['properties']['style']['properties']
        for key in ('class_method', 'class_count', 'palette', 'label_field', 'line_width', 'circle_radius'):
            self.assertIn(key, style)
        self.assertEqual(sorted(style['class_method']['enum']),
                         sorted(['auto', 'unique', 'quantile', 'jenks', 'equal', 'continuous']))
        self.assertEqual(style['class_count']['minimum'], 3)
        self.assertEqual(style['class_count']['maximum'], 7)
        self.assertEqual(sorted(style['palette']['enum']), sorted(['classic', 'okabe', 'viridis', 'sunset']))
        self.assertEqual(style['line_width']['minimum'], 0.5)
        self.assertEqual(style['line_width']['maximum'], 8)
        self.assertEqual(style['circle_radius']['minimum'], 2)
        self.assertEqual(style['circle_radius']['maximum'], 15)

if __name__ == '__main__':
    unittest.main()
