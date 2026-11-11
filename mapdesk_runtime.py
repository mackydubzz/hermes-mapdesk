"""Shared profile-scoped workspace commands, used by both tools and HTTP."""
import importlib.util
import math
from pathlib import Path

_MODULES = {}
ROOT = Path(__file__).resolve().parent

ANALYSIS_OPS = ('buffer', 'dissolve', 'centroid', 'hull', 'clip', 'count_points', 'measure')
_IMPORT_OPS = ('import_kml', 'import_kmz', 'import_gpx', 'import_xlsx', 'import_shp')


def _load(name):
    if name not in {'mapdesk_core', 'mapdesk_sources', 'mapdesk_formats', 'mapdesk_ops',
                    'mapdesk_exports'}:
        raise ValueError('Unknown internal module')
    if name not in _MODULES:
        path = ROOT / (name + '.py')
        if not path.exists():
            raise ValueError('MapDesk dependencies unavailable. Run hermes plugins doctor mapdesk.')
        spec = importlib.util.spec_from_file_location('mapdesk_internal_' + name, path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError:
            raise ValueError('MapDesk dependencies unavailable. Run hermes plugins doctor mapdesk.')
        _MODULES[name] = module
    return _MODULES[name]


def _store():
    from plugins.plugin_storage import plugin_data_dir
    return _load('mapdesk_core').WorkspaceStore(plugin_data_dir('mapdesk'))


def layer_text(layer_id):
    """The 'layer' action's reply as JSON text (see WorkspaceStore.layer_reply_text)."""
    return _store().layer_reply_text(layer_id)


def _summary(layer):
    return {key: value for key, value in layer.items() if key != 'geojson'}


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _analysis_params(op, params):
    if params is None:
        params = {}
    if type(params) is not dict:
        raise ValueError('params must be an object')
    if op == 'buffer':
        distance = params.get('distance_m')
        if type(distance) not in (int, float) or not math.isfinite(distance) or not 1 <= distance <= 100000:
            raise ValueError('buffer distance_m must be a number from 1 to 100000')
    if op == 'dissolve' and params.get('by_field') is not None and type(params.get('by_field')) is not str:
        raise ValueError('dissolve by_field must be a string or null')
    if op == 'clip' and type(params.get('overlay_layer_id')) is not str:
        raise ValueError('clip needs overlay_layer_id naming a workspace layer')
    if op == 'count_points' and type(params.get('points_layer_id')) is not str:
        raise ValueError('count_points needs points_layer_id naming a workspace layer')
    return params


def _export_layer_ids(store, ids):
    if not isinstance(ids, list) or any(type(item) is not str for item in ids):
        raise ValueError('layer_ids must be an array of layer ids')
    if not ids:
        raise ValueError('Select at least one layer')
    known = {layer['id'] for layer in store.summary()['layers']}
    for item in ids:
        if item not in known:
            raise ValueError('Unknown layer ID: ' + item)
    return ids


def _export(store, ids, kind):
    """Assemble export inputs from the store and return the base64 payload."""
    import base64
    exports = _load('mapdesk_exports')
    layers = [store.layer(item) for item in ids]
    records = [{'name': layer['name'], 'source': layer.get('source') or {},
                'feature_count': layer.get('feature_count', len(layer['geojson']['features'])),
                'fields': layer.get('fields') or []} for layer in layers]
    workspace = store.summary().get('name') or 'mapdesk'
    provenance = exports.make_provenance(workspace, records)
    stem = exports.sanitize_stem(workspace)
    if kind == 'gpkg':
        payload, names = exports.write_gpkg(
            [{'name': layer['name'], 'geojson': layer['geojson']} for layer in layers],
            provenance, stem)
        extension = 'gpkg'
    elif kind == 'shp':
        layer = layers[0]
        payload, names = exports.write_shp(layer['name'], layer['geojson'], provenance, stem)
        extension = 'zip'
    else:
        payload, names = exports.write_kmz(
            [{'name': layer['name'], 'geojson': layer['geojson'], 'style': layer.get('style') or {}}
             for layer in layers], provenance, stem)
        extension = 'kmz'
    encoded = base64.b64encode(payload).decode('ascii')
    if len(encoded) > exports.BASE64_CAP:
        raise ValueError('Export exceeds 12 MB; remove layers or filter first')
    return {'base64': encoded, 'filename': exports.export_filename(stem, extension),
            'layers': names,
            'feature_count': sum(record['feature_count'] for record in records),
            'bytes': len(payload)}


def execute(action, args):
    if not isinstance(args, dict):
        raise ValueError('Arguments must be an object')
    store = _store()
    if action == 'state':
        return store.state()
    if action == 'status':
        state = store.state(include_data=False)
        return {'plugin': 'mapdesk', 'version': '0.3.0', 'layer_count': len(state['layers']), **state}
    if action == 'summary':
        return store.summary()
    if action == 'layer':
        layer = store.layer(args['layer_id'])
        return {'layer_id': layer['id'], 'data_revision': layer.get('data_revision'),
                'geojson': layer['geojson']}
    if action == 'import_geojson':
        geojson = args['geojson']
        source = {'kind': 'geojson', 'crs': 'EPSG:4326', 'complete': True,
                  'fetched_at': _now_iso(),
                  'note': 'Complete uploaded file; external dataset completeness is not established.'}
        if (ROOT / 'mapdesk_formats.py').exists():
            normalized = _load('mapdesk_formats').normalize_geojson(geojson)
            geojson = normalized['geojson']
            source['original_crs'] = normalized.get('original_crs')
            source['swapped_warning'] = bool(normalized.get('swapped_warning'))
        return _summary(store.add_layer(args['name'], geojson, source=source))
    if action in _IMPORT_OPS:
        formats = _load('mapdesk_formats')
        if action == 'import_kml':
            imported = formats.parse_kml(args['text'])
        elif action == 'import_kmz':
            imported = formats.parse_kmz(args['base64'])
        elif action == 'import_gpx':
            imported = formats.parse_gpx(args['text'])
        elif action == 'import_xlsx':
            imported = formats.parse_xlsx(args['base64'], args.get('sheet'))
        else:
            imported = formats.parse_shp_zip(args['base64'])
        return _summary(store.add_layer(args['name'], imported['geojson'], source=imported['source']))
    if action == 'import_arcgis':
        sources = _load('mapdesk_sources')
        # The tool's limit (1..5000) caps total features (max_features); the
        # page size stays service-driven with the fetch_arcgis default of 1000.
        limit = args.get('limit')
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 5000):
            raise ValueError('ArcGIS limit must be an integer from 1 to 5000')
        kwargs = {}
        if limit is not None:
            kwargs['max_features'] = limit
        if args.get('where') is not None:
            kwargs['where'] = args['where']
        imported = sources.fetch_arcgis(args['url'], args.get('bbox'), **kwargs)
        return _summary(store.add_layer(args['name'], imported['geojson'], source=imported['source']))
    if action == 'import_csv':
        imported = _load('mapdesk_sources').csv_to_geojson(args['text'], args['latitude_column'], args['longitude_column'])
        return _summary(store.add_layer(args['name'], imported['geojson'], source=imported['source']))
    if action == 'geocode_batch':
        from plugins.plugin_storage import plugin_data_dir
        return _load('mapdesk_sources').geocode_batch(args['addresses'],
                                                      cache_dir=plugin_data_dir('mapdesk'))
    if action == 'select':
        return store.set_selection(args.get('layer_id'), args.get('feature_ids'), args.get('bbox'),
                                   radius=args.get('radius'), spatial=args.get('spatial'),
                                   invert=args.get('invert', False), detail=True)
    if action == 'selection':
        return store.selection()
    if action == 'reorder':
        return store.reorder(args['layer_ids'])
    if action == 'query':
        limit = args.get('limit')
        return store.query(args['layer_id'], args.get('where'), spatial=args.get('spatial'),
                            limit=200 if limit is None else limit)
    if action == 'analyze':
        op = args['op']
        if op not in ANALYSIS_OPS:
            raise ValueError('Unsupported analysis op')
        ops = _load('mapdesk_ops')
        params = _analysis_params(op, args.get('params'))
        layer = store.layer(args['layer_id'])
        geojson = layer['geojson']
        if op == 'measure':
            return ops.measure(geojson)
        if op == 'buffer':
            result = ops.buffer(geojson, params['distance_m'])
        elif op == 'dissolve':
            result = ops.dissolve(geojson, params.get('by_field'))
        elif op == 'centroid':
            result = ops.centroid(geojson)
        elif op == 'hull':
            result = ops.hull(geojson)
        elif op == 'clip':
            result = ops.clip(geojson, store.layer(params['overlay_layer_id'])['geojson'])
        else:
            result = ops.count_points(geojson, store.layer(params['points_layer_id'])['geojson'])
        name = args.get('output_name') or '{} · {}'.format(layer['name'], op)
        return _summary(store.add_layer(name, result['geojson'], source={
            'kind': 'analysis', 'op': op, 'from_layer': args['layer_id'], 'params': params,
            'complete': not result.get('partial', False), 'fetched_at': _now_iso()}))
    if action == 'update_layer':
        return _summary(store.update_layer(args['layer_id'], args['changes'], include_data=False))
    if action == 'remove_layer':
        return store.remove_layer(args['layer_id'])
    if action == 'viewport':
        return store.set_viewport(args['center'], args['zoom'])
    if action == 'frame':
        return store.frame(args['bbox'])
    if action == 'save':
        return {'name': store.save_name(args['name'])}
    if action in ('export_gpkg', 'export_shp', 'export_kmz'):
        if action == 'export_shp':
            layer_id = args.get('layer_id')
            if type(layer_id) is not str or not layer_id:
                raise ValueError('layer_id must be a string naming a workspace layer')
            ids = _export_layer_ids(store, [layer_id])
        else:
            ids = _export_layer_ids(store, args.get('layer_ids'))
        return _export(store, ids, action[len('export_'):])
    raise ValueError('Unsupported workspace action')
