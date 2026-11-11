import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUESTS = {
    'mapbox': ROOT / 'desktop' / 'map.html',
    'keyless': ROOT / 'desktop' / 'map_keyless.html',
}
CORE_BEGIN = '// MAPDESK-CORE-BEGIN'
CORE_END = '// MAPDESK-CORE-END'

# Emit types both guests must produce (mapdesk:needs-token is mapbox-only).
COMMON_EMITS = ['mapdesk:ready', 'mapdesk:loaded', 'mapdesk:viewport',
                'mapdesk:select', 'mapdesk:rendered', 'mapdesk:error',
                'mapdesk:png']
# Inbound messages both guests must handle.
COMMON_HANDLERS = ['mapdesk:init', 'mapdesk:state', 'mapdesk:mode',
                   'mapdesk:export-png', 'mapdesk:fit', 'mapdesk:style',
                   'mapdesk:terrain', 'mapdesk:heatmap', 'mapdesk:cluster']


def read_guest(name):
    path = GUESTS[name]
    if not path.is_file():
        raise AssertionError('Renderer guest %s has not been built' % path)
    return path.read_text(encoding='utf-8')


def extract_core(text):
    start = text.index(CORE_BEGIN)
    end = text.index(CORE_END) + len(CORE_END)
    return text[start:end]


class RendererGuestContractTests(unittest.TestCase):
    """Static contract checks over both standalone guest documents."""

    def test_renderer_is_credential_free_and_sandbox_bridge_is_scoped(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertTrue(GUESTS[name].is_file(),
                                'Map renderer document has not been built')
                self.assertIn('event.source !== parent', text)
                self.assertIn("'mapdesk:init'", text)
                self.assertIn('ResizeObserver', text)
                self.assertNotIn('pk.ey', text)
                self.assertNotIn('sk.ey', text)
                self.assertNotIn('localStorage', text)
                # Guests never talk to the network or ask the parent for data.
                self.assertNotIn('fetch(', text)
                self.assertNotIn('XMLHttpRequest', text)
                self.assertIn('data.version!==1', text)

    def test_protocol_surface_is_shared(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                for emit_type in COMMON_EMITS:
                    self.assertIn("emit('%s'" % emit_type, text,
                                  'missing emit %s in %s' % (emit_type, name))
                for handler in COMMON_HANDLERS:
                    self.assertIn("'%s'" % handler, text,
                                  'missing handler %s in %s' % (handler, name))
                # Selection emit keeps the existing shapes: feature_ids click,
                # bbox box select, radius Ctrl-drag select.
                self.assertIn('feature_ids:', text)
                self.assertIn('bbox:[', text)
                self.assertIn('radius:{center:', text)

    def test_mapbox_pins_worker_hook_and_token_gate(self):
        text = read_guest('mapbox')
        self.assertIn('https://api.mapbox.com/mapbox-gl-js/v3.33.0/', text)
        self.assertEqual(text.count('integrity="sha384-'), 2,
                         'both Mapbox pins must stay SRI-protected')
        self.assertIn('sha384-S0a+eT2dLtsB1suB6ZJ/8IX+0d0Fvix9weJQ67L3E+jHOvn6SUKwYXVDyGdMuJtL', text)
        self.assertIn('sha384-jdGuG3GDIph+ONsjITkxWmlUE2RR0At6jtOVirn6L/6dYy3WcAVDaBNI3X96rUT0', text)
        # The verified classic-worker hook must survive untouched.
        self.assertIn("workerClass=class MapDeskWorker extends Worker", text)
        self.assertIn("{type:'classic',name:'mapdesk-mapbox'}", text)
        self.assertIn("'mapbox://styles/mapbox/dark-v11'", text)
        # Token gate: pk. prefix requirement plus the needs-token emit.
        self.assertIn("token.startsWith('pk.')", text)
        self.assertIn("emit('mapdesk:needs-token')", text)
        self.assertIn("'mapdesk:token'", text)

    def test_keyless_pins_and_no_token_paths(self):
        text = read_guest('keyless')
        self.assertIn('https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js', text)
        self.assertIn('https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css', text)
        self.assertEqual(text.count('integrity="sha384-'), 2,
                         'both MapLibre pins must be SRI-protected')
        self.assertIn("'https://tiles.openfreemap.org/styles/fiord'", text)
        self.assertIn('dark_matter', text, 'the rejected style name must be recorded')
        self.assertIn('attributionControl:true', text)
        # Never a token, never a needs-token emit, no worker option.
        self.assertNotIn("emit('mapdesk:needs-token'", text)
        self.assertNotIn("'mapdesk:token'", text)
        self.assertNotIn('workerClass', text)
        self.assertNotIn('pk.', text)

    def test_map_construction_surface(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                # PNG capture reads the canvas inside a render callback, so the
                # (slower, and MapLibre-5-ignored) preserveDrawingBuffer flag is gone.
                self.assertNotIn('preserveDrawingBuffer', text.replace('no preserveDrawingBuffer is needed', ''))
                self.assertIn('map.triggerRepaint()', text)
                self.assertIn('ScaleControl', text)
                self.assertIn("'bottom-left'", text)
                # Bottom-right coordinate readout: 4 decimals + zoom.
                self.assertIn("getElementById('coords')", text)
                self.assertIn("toFixed(4)+'°", text)

    def test_per_layer_styling_surface(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn("'fill-opacity':opacity*0.45", text)
                # R1: zoom-scaled sizes with a feature-state hover bump.
                self.assertIn("'line-width':hoverBump(lineWidthExpr(st.line_width),1)", text)
                self.assertIn("'circle-radius':hoverBump(circleRadiusExpr(st.circle_radius),1.5)", text)
                # Labels: symbol layers keyed off style.label_field.
                self.assertIn('st.label_field', text)
                self.assertIn("'text-field':['get',", text)
                self.assertIn("'text-font':LABEL_FONTS", text)
                self.assertIn("'text-halo-color'", text)
                self.assertIn("'text-offset':[0,0.8]", text)
                self.assertIn("'text-size':11", text)
                self.assertIn('label:true', text)

    def test_label_fonts_per_guest(self):
        self.assertIn("['DIN Offc Pro Medium','Arial Unicode MS Regular']",
                      read_guest('mapbox'))
        self.assertIn("['Noto Sans Regular']", read_guest('keyless'))

    def test_classification_v2_surface(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                for hex_color in ('#6ed8d0', '#E69F00', '#F0E442', '#46327E',
                                  '#D7191C', '#f2d37e'):
                    self.assertIn(hex_color, text)
                self.assertIn("'#88949f'", text)
                for token in ('uniqueCategories', 'quantileBreaks', 'equalBreaks',
                              'jenksBreaks', 'classifyLayer', 'cachedGeojson'):
                    self.assertIn(token, text)
                # Jenks cap: 2,000 numeric values, quantile fallback beyond.
                self.assertIn('>2000', text)
                # Continuous uses an interpolate expression + gradient legend.
                self.assertIn("'interpolate'", text)
                self.assertIn("'linear-gradient(90deg,'", text)
                # Class method note lines.
                self.assertIn('Quantile classes, not a continuous ramp.', text)
                self.assertIn('Natural breaks (Jenks)', text)
                self.assertIn('Equal interval classes', text)
                # Missing/blank values get the fallback color and a (blank) row.
                self.assertIn("'(blank)'", text)

    def test_geojson_cache_never_requests_from_parent(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                core = extract_core(text)
                self.assertIn('client_revision', core)
                self.assertIn('data_revision', core)
                self.assertIn('cachedGeojson', core)
                self.assertIn('never request', text)
                self.assertIn('geoCache', text)

    def test_draw_order_follows_state_layers(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('orderKey', text)
                self.assertIn('map.removeLayer(l.id)', text)

    def test_hover_tooltip_and_coord_readout_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('featureName', text)
                self.assertIn('queryRenderedFeatures', text)
                self.assertIn('shown>=3', text)
                self.assertIn('tip-row', text)
                # textContent-only tooltip (no innerHTML).
                self.assertNotIn('innerHTML', text)

    def test_measure_mode_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn("'mapdesk:mode'", text)
                self.assertIn("mode==='measure'", text)
                self.assertIn('setMode', text)
                self.assertIn('dblclick', text)
                self.assertIn("key==='Enter'", text)
                self.assertIn("key==='Escape'", text)
                self.assertIn('clearMeasure', text)
                self.assertIn('finishMeasure', text)
                self.assertIn('haversineM', text)
                self.assertIn('ringAreaM2', text)
                self.assertIn('Measure: click to add vertices', text)
                # Hint copy for select mode per the pane contract.
                self.assertIn('Measure mode in the pane header', text)
                self.assertIn('Ctrl-drag radius select', text)

    def test_radius_and_box_select_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('e.ctrlKey', text)
                self.assertIn('e.shiftKey', text)
                self.assertIn('setCircle', text)
                self.assertIn('radius:{center:d.center,meters:d.meters}', text)
                # Escape cancels both drags.
                self.assertIn('radiusDrag=null', text)

    def test_png_export_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn("'mapdesk:export-png'", text)
                self.assertIn('pngPending', text)
                self.assertIn("toDataURL('image/png')", text)
                self.assertIn("emit('mapdesk:png',{dataUrl:", text)

    def test_r1_new_inbound_messages_and_fit_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                for handler in ('mapdesk:fit', 'mapdesk:style', 'mapdesk:terrain',
                                'mapdesk:heatmap', 'mapdesk:cluster'):
                    self.assertIn("'%s'" % handler, text)
                # fit: exact camera call, queued until load like other actions.
                self.assertIn('map.fitBounds([[bbox[0],bbox[1]],[bbox[2],bbox[3]]],'
                              '{padding:24,maxZoom:16,linear:false})', text)
                self.assertIn('pendingFit', text)
                # style: diff:false swap, re-render on style.load.
                self.assertIn('setStyle(BASEMAPS[basemap]||BASEMAP_STYLE,{diff:false})', text)
                self.assertIn("map.once('style.load'", text)
                # init payload gains basemap/terrain; style chosen at construction.
                self.assertIn('if(BASEMAPS[data.basemap])basemap=data.basemap', text)
                self.assertIn('terrainOn=!!data.terrain', text)
                self.assertIn('style:BASEMAPS[basemap]||BASEMAP_STYLE', text)
                # The five new types are inbound only — never emitted.
                for handler in ('mapdesk:fit', 'mapdesk:style', 'mapdesk:terrain',
                                'mapdesk:heatmap', 'mapdesk:cluster'):
                    self.assertNotIn("emit('%s'" % handler, text)

    def test_hover_state_is_removed_not_set_false(self):
        # A leftover {hover:false} keeps the source's feature state non-empty, which lets Mapbox GL refresh an old
        # tile against a just re-added layer and throw in the next frame. Both guests remove the state instead.
        for kind in ('mapbox', 'keyless'):
            guest = read_guest(kind)
            self.assertIn('function clearHover(){', guest)
            self.assertIn('map.removeFeatureState({source:hovered.source,id:hovered.id})', guest)
            self.assertNotIn('{hover:false}', guest.split('function clearHover(){')[1])
            self.assertIn("  clearHover();\n  for(const l of [...map.getStyle().layers])if(l.metadata?.mapdesk)map.removeLayer(l.id);\n  orderKey=ids;", guest.replace('\r\n', '\n'))

    def test_keyless_guest_strengthens_basemap_borders(self):
        keyless = read_guest('keyless')
        self.assertIn('function strengthenBorders(){', keyless)
        self.assertIn("layer['source-layer']!=='boundary'", keyless)
        # The dark style has no county lines of its own; the guest adds them from zoom 9, where the tiles carry them.
        self.assertIn("const COUNTY_BORDERS='mapdesk-county-borders';", keyless)
        self.assertIn("minzoom:9,filter:['==',['get','admin_level'],6]", keyless)
        # Applied on first load and again after every basemap swap, which replaces the whole style.
        self.assertIn("status.style.display='none';strengthenBorders();", keyless)
        self.assertIn("hovered=null;\n  strengthenBorders();", keyless.replace('\r\n', '\n'))
        for name in ('dark', 'light', 'streets'):
            self.assertRegex(keyless, r"BORDER_INK=\{.*%s:\{country:'#[0-9a-f]{6}',state:'#[0-9a-f]{6}'\}" % name)

    def test_r1_basemap_style_maps_per_guest(self):
        mapbox = read_guest('mapbox')
        for style in ("dark:'mapbox://styles/mapbox/dark-v11'",
                      "light:'mapbox://styles/mapbox/light-v11'",
                      "streets:'mapbox://styles/mapbox/streets-v12'",
                      "satellite:'mapbox://styles/mapbox/satellite-streets-v12'"):
            self.assertIn(style, mapbox)
        keyless = read_guest('keyless')
        self.assertIn("dark:'https://tiles.openfreemap.org/styles/fiord'", keyless)
        self.assertIn("light:'https://tiles.openfreemap.org/styles/positron'", keyless)
        for guest in (mapbox, keyless):  # data labels flip to dark text on the light basemap
            self.assertIn("basemap==='light'?{'text-color':'#1b2733'", guest)
        self.assertIn("streets:'https://tiles.openfreemap.org/styles/liberty'", keyless)
        self.assertIn('satellite:SATELLITE_STYLE', keyless)

    def test_r1_keyless_inline_satellite_style(self):
        text = read_guest('keyless')
        self.assertIn('https://server.arcgisonline.com/ArcGIS/rest/services/'
                      'World_Imagery/MapServer/tile/{z}/{y}/{x}', text)
        self.assertIn("attribution:'Esri, Maxar, Earthstar Geographics'", text)
        # Roads, then boundaries and place names, drawn over the imagery in that order.
        for service in ('Reference/World_Transportation', 'Reference/World_Boundaries_and_Places'):
            self.assertIn('https://server.arcgisonline.com/ArcGIS/rest/services/%s/MapServer/tile/{z}/{y}/{x}' % service, text)
        self.assertRegex(text, r"layers:\[\{id:'esri-imagery'.*\{id:'esri-roads'.*\{id:'esri-places'")
        self.assertIn('maxzoom:19', text)
        # glyphs keep data labels drawable over the raster imagery (curl-verified).
        self.assertIn('https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf', text)

    def test_r1_terrain_wiring_per_guest(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('setTerrain({source:', text)
                self.assertIn('setTerrain(null)', text)
                self.assertIn("if(typeof map.setSky==='function')map.setSky(SKY)", text)
        mapbox = read_guest('mapbox')
        self.assertIn("'mapbox://mapbox.mapbox-terrain-dem-v1'", mapbox)
        self.assertIn("addSource('mapbox-dem',", mapbox)
        self.assertIn('maxzoom:14', mapbox)
        keyless = read_guest('keyless')
        self.assertIn('https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
                      keyless)
        self.assertIn("encoding:'terrarium'", keyless)
        self.assertIn('maxzoom:13', keyless)

    def test_r1_heatmap_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn("'heatmap-weight':1", text)
                self.assertIn("'heatmap-radius':heatmapRadiusExpr()", text)
                self.assertIn("'heatmap-color':heatmapColorExpr(st.palette)", text)
                self.assertIn("'heatmap-opacity':st.opacity", text)
                self.assertIn('heat:true', text)

    def test_r1_cluster_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('clusterRadius=60', text)
                self.assertIn('clusterMaxZoom=14', text)
                self.assertIn("['!',['has','point_count']]", text)
                self.assertIn('getClusterExpansionZoom', text)
                self.assertIn('map.easeTo({center:f.geometry.coordinates,zoom})', text)
                self.assertIn("['get','point_count_abbreviated']", text)
                self.assertIn("'text-color':'#ffffff'", text)
                self.assertIn("+'-clusters'", text)
                self.assertIn("+'-cluster-count'", text)

    def test_r1_hover_feature_state_wiring(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('setFeatureState', text)
                self.assertIn("['feature-state','hover']", text)
                self.assertIn('updateHover', text)
                self.assertIn("?'pointer':''", text)

    def test_r1_source_knobs_and_pitch(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn('visualizePitch:true', text)
                core = extract_core(text)
                self.assertIn('buffer:points&&!linear?64:40', core)
                self.assertIn('tolerance:points&&!linear?0.375:2', core)
                self.assertIn('maxzoom:18};', core)
                # MapLibre refuses a geojson source carrying minzoom (no data draws).
                self.assertNotIn('minzoom:', core)

    def test_r1_pixel_ratio_guard(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                text = read_guest(name)
                self.assertIn("scale===2&&typeof map.setPixelRatio==='function'", text)
                self.assertIn('map.setPixelRatio(', text)
                self.assertIn("map.once('render'", text)


class RendererCoreParityTests(unittest.TestCase):
    """The classification/cache core must be byte-identical in both guests."""

    def test_core_block_identical_across_guests(self):
        mapbox_core = extract_core(read_guest('mapbox'))
        keyless_core = extract_core(read_guest('keyless'))
        self.assertEqual(mapbox_core, keyless_core,
                         'guests must embed the same shared core block')

    def test_core_block_is_pure(self):
        for name in GUESTS:
            with self.subTest(guest=name):
                core = extract_core(read_guest(name))
                for forbidden in ('document.', 'mapboxgl', 'maplibregl',
                                  'parent.', 'fetch(', 'getElementById'):
                    self.assertNotIn(forbidden, core,
                                     'core must stay library/DOM free: %s' % forbidden)


# Functional harness: extracts the pure core from each guest and runs it under
# node against a synthetic state (real behavior, no mocks). Skipped when node
# is unavailable; the static tests above remain the always-on gate.
DRIVER = r"""
const assert=require('node:assert/strict');
let checks=0;
function check(name,fn){try{fn();}catch(err){console.error(JSON.stringify({passed:false,check:name,error:String(err&&err.message||err)}));process.exit(1);}checks+=1;}
function feats(values){return values.map((value,i)=>{const p={name:'p'+i};if(value!==undefined)p.v=value;return {type:'Feature',id:'f'+i,properties:p,geometry:{type:'Point',coordinates:[0,0]}};});}
const BLANK=['any',['!',['has','v']],['==',['get','v'],null],['==',['get','v'],'']];

check('palettes match the contract hex arrays',()=>{
 assert.deepEqual(CLASSIC,['#6ed8d0','#e7ad67','#aab1ff','#ef86a4','#8ac887','#f2d37e','#72b8f0','#ba9bce']);
 assert.deepEqual(OKABE,['#0072B2','#E69F00','#009E73','#D55E00','#CC79A7','#56B4E9','#F0E442','#111111']);
 assert.deepEqual(VIRIDIS,['#440154','#46327E','#365C8D','#277F8E','#1FA187','#4AC16B','#A0DA39','#FDE725']);
 assert.deepEqual(SUNSET,['#2C7BB6','#00A6CA','#79D1A4','#C9E892','#F4C46B','#ED8A4C','#D7191C','#7B0D2B']);
});

check('layerStyle defaults and clamps',()=>{
 const st=layerStyle({style:{class_count:99,line_width:99,circle_radius:1,opacity:2,palette:'nope'}});
 assert.equal(st.class_count,7);assert.equal(st.line_width,8);assert.equal(st.circle_radius,2);
 assert.equal(st.opacity,1);assert.equal(st.palette,'classic');
 const def=layerStyle({});
 assert.equal(def.class_count,5);assert.equal(def.line_width,2.2);assert.equal(def.circle_radius,5);
 assert.equal(def.opacity,0.85);assert.equal(def.class_method,'auto');assert.equal(def.color,'#6ed8d0');
 assert.equal(layerStyle({style:{class_count:1,circle_radius:99}}).class_count,3);
 assert.equal(layerStyle({style:{class_count:1,circle_radius:99}}).circle_radius,15);
});

check('unique classification with blank handling',()=>{
 const u=classifyLayer({style:{color_by:'v',class_method:'unique',palette:'okabe'}},feats(['a','b','a',null,'']));
 assert.equal(u.method,'unique');
 assert.deepEqual(u.expression,['match',['to-string',['get','v']],'','#0072B2','a','#E69F00','b','#009E73','#88949f']);
 assert.deepEqual(u.legend.rows,[{label:'(blank)',color:'#0072B2',count:2},{label:'a',color:'#E69F00',count:2},{label:'b',color:'#009E73',count:1}]);
});

check('unique caps at 24 distinct values',()=>{
 const many=Array.from({length:30},(_,i)=>'c'+i);
 const u=classifyLayer({style:{color_by:'v',class_method:'unique'}},feats(many));
 assert.equal(u.expression.length,3+24*2);
 assert.equal(u.legend.rows.length,24);
});

check('auto keeps the legacy numeric-break heuristic',()=>{
 const a=classifyLayer({style:{color_by:'v',color_scale:'numeric',breaks:[10,20]}},feats([1,15,25,undefined]));
 assert.equal(a.method,'auto');
 assert.deepEqual(a.expression,['case',BLANK,'#88949f',['step',['to-number',['get','v'],0],'#440154',10,'#21918c',20,'#fde725']]);
});

check('auto falls back to unique without numeric breaks',()=>{
 assert.equal(classifyLayer({style:{color_by:'v'}},feats(['x','y'])).method,'unique');
});

check('no color_by keeps the base color',()=>{
 const n=classifyLayer({style:{color:'#123456'}},feats([1,2]));
 assert.equal(n.method,'none');assert.equal(n.expression,'#123456');assert.equal(n.legend,null);
});

check('quantile classes on a synthetic state',()=>{
 const vals=Array.from({length:100},(_,i)=>i);
 const q=classifyLayer({style:{color_by:'v',class_method:'quantile',class_count:5,palette:'viridis'}},feats(vals));
 assert.deepEqual(q.expression,['case',BLANK,'#88949f',['step',['to-number',['get','v'],0],'#440154',20,'#365C8D',40,'#1FA187',60,'#4AC16B',80,'#FDE725']]);
 assert.deepEqual(q.legend.rows.map(r=>[r.label,r.count]),[['< 20',20],['20 – 40',20],['40 – 60',20],['60 – 80',20],['> 80',20]]);
 assert.equal(q.note,'Quantile classes, not a continuous ramp.');
});

check('equal interval classes',()=>{
 const e=classifyLayer({style:{color_by:'v',class_method:'equal',class_count:5,palette:'sunset'}},feats([0,2.5,5,7.5,10,undefined,'']));
 assert.equal(e.method,'equal');
 const step=e.expression[3];
 assert.equal(step[0],'step');
 assert.deepEqual([step[3],step[5],step[7],step[9]],[2,4,6,8]);
 assert.equal(e.legend.rows.length,6);
 assert.deepEqual(e.legend.rows.at(-1),{label:'(blank)',color:'#88949f',count:2});
 assert.equal(e.note,'Equal interval classes, 5 classes.');
});

check('jenks finds the known natural break',()=>{
 assert.deepEqual(jenksBreaks([1,2,3,4,5,6,7,8,9,10,100],2),[100]);
});

check('jenks matches the brute-force optimum',()=>{
 const data=[3,7,1,9,4,12,6,2,8,15,5,11];
 const sorted=[...data].sort((a,b)=>a-b);
 function wc(a,b){let s=0,ss=0;for(let i=a;i<=b;i++){s+=sorted[i];ss+=sorted[i]*sorted[i];}return ss-s*s/(b-a+1);}
 let best=Infinity;
 for(let i=0;i<sorted.length-2;i++)for(let j=i+1;j<sorted.length-1;j++)best=Math.min(best,wc(0,i)+wc(i+1,j)+wc(j+1,sorted.length-1));
 const jb=jenksBreaks(data,3);
 assert.equal(jb.length,2);
 function totalWith(breaks){
  const bounds=breaks.map(b=>{let i=0;while(i<sorted.length&&sorted[i]<b)i++;return i;});
  let total=0,start=0;
  for(const b of [...bounds,sorted.length]){total+=wc(start,b-1);start=b;}
  return total;
 }
 assert.ok(Math.abs(totalWith(jb)-best)<1e-6,'jenks must hit the optimal partition cost');
});

check('jenks caps at 2000 values with quantile fallback',()=>{
 const big=[];for(let i=0;i<2500;i++)big.push((i*37)%1000);
 const jj=classifyLayer({style:{color_by:'v',class_method:'jenks',class_count:5}},feats(big));
 const qq=classifyLayer({style:{color_by:'v',class_method:'quantile',class_count:5}},feats(big));
 assert.deepEqual(jj.expression,qq.expression);
});

check('class_count range honored',()=>{
 const vals=Array.from({length:12},(_,i)=>i);
 assert.equal(quantileBreaks(vals,3).length,2);
 const c7=classifyLayer({style:{color_by:'v',class_method:'quantile',class_count:7}},feats(vals));
 assert.equal(c7.expression[3].length,3+6*2);
});

check('continuous ramp with gradient legend',()=>{
 const c=classifyLayer({style:{color_by:'v',class_method:'continuous',palette:'viridis'}},feats([0,2,4,6,8]));
 assert.equal(c.method,'continuous');
 assert.deepEqual(c.legend,{kind:'gradient',min:0,max:8,colors:VIRIDIS});
 assert.equal(c.expression[2],'#88949f');
 assert.deepEqual(c.expression[1],BLANK);
 const expected=['interpolate',['linear'],['to-number',['get','v'],0]];
 VIRIDIS.forEach((color,i)=>expected.push(0+8*i/7,color));
 assert.deepEqual(c.expression[3],expected);
});

check('missing values use the fallback color and a blank legend row',()=>{
 const q=classifyLayer({style:{color_by:'v',class_method:'quantile',class_count:4}},feats([1,10,undefined,null,'']));
 assert.equal(q.expression[2],'#88949f');
 assert.equal(q.legend.rows.at(-1).label,'(blank)');
 assert.equal(q.legend.rows.at(-1).count,3);
});

check('slim-layer geojson cache keyed by client_revision ?? data_revision',()=>{
 const cache=new Map();
 const geo={type:'FeatureCollection',features:[{type:'Feature',id:'a',properties:{},geometry:{type:'Point',coordinates:[0,0]}}]};
 const full={id:'L1',client_revision:'fabc',geojson:geo};
 assert.equal(cachedGeojson(full,cache),geo);
 assert.equal(cache.get('L1').revision,'fabc');
 assert.equal(cachedGeojson({id:'L1',client_revision:'fabc'},cache),geo);
 assert.equal(cachedGeojson({id:'L1',data_revision:'fabc'},cache),geo);
 assert.equal(cachedGeojson({id:'L1',client_revision:'fdef'},cache),null);
 assert.equal(cachedGeojson({id:'L2',data_revision:3},cache),null);
 assert.equal(cachedGeojson({id:'L2'},cache),null);
});

check('geodesic haversine and spherical-shoelace area',()=>{
 const hm=haversineM([0,0],[1,0]);
 assert.ok(Math.abs(hm-111195.08)<1,'1 degree of longitude at the equator');
 const side=Math.PI/180*6371008.8;
 const quad=ringAreaM2([[0,0],[1,0],[1,1],[0,1]]);
 assert.ok(Math.abs(quad-side*side)/(side*side)<0.01,'1x1 degree square at the equator');
});

check('name-like property and measure formats',()=>{
 assert.equal(featureName({title:'T'}),'T');
 assert.equal(featureName({id:5}),'5');
 assert.equal(featureName({}),null);
 assert.equal(featureName(null),null);
 assert.equal(formatLength(500),'500 m');
 assert.equal(formatLength(5000),'5.00 km');
 assert.equal(formatArea(900),'900 m²');
 assert.equal(formatArea(2000000),'2.00 km²');
 assert.equal(fmtNum(12.5),'12.5');
});


check('R1 zoom-scaled line-width interpolation with style clamps',()=>{
 const e=lineWidthExpr(2.2);
 assert.deepEqual(e,['interpolate',['exponential',1.6],['zoom'],4,Math.max(0.5,2.2*0.45),10,2.2,14,Math.min(8,2.2*1.8)]);
 const big=lineWidthExpr(99);
 assert.deepEqual([big[4],big[6],big[8]],[Math.max(0.5,8*0.45),8,8]);
 const small=lineWidthExpr(0.1);
 assert.deepEqual([small[4],small[6],small[8]],[0.5,0.5,0.9]);
});

check('R1 zoom-scaled circle-radius interpolation with style clamps',()=>{
 const e=circleRadiusExpr(5);
 assert.deepEqual(e,['interpolate',['exponential',1.6],['zoom'],4,2.5,10,5,13,8]);
 const big=circleRadiusExpr(99);
 assert.deepEqual([big[4],big[6],big[8]],[7.5,15,15]);
 const small=circleRadiusExpr(0.5);
 assert.deepEqual([small[4],small[6],small[8]],[2,2,3.2]);
});

// Style-spec rule (Mapbox GL and MapLibre): ['zoom'] may only be the input of
// the TOP-LEVEL step/interpolate. A nested zoom curve makes addLayer refuse
// the layer, which is how 0.3.0 lost every line, circle and cluster halo.
function zoomUses(expr,depth=0){
 if(!Array.isArray(expr))return [];
 const here=expr[0]==='zoom'?[depth]:[];
 return here.concat(...expr.slice(1).map(part=>zoomUses(part,depth+1)));
}
function assertTopLevelZoomCurve(expr){
 assert.ok(expr[0]==='interpolate'||expr[0]==='step','zoom curve must be the top-level expression');
 const input=expr[0]==='interpolate'?expr[2]:expr[1];
 assert.deepEqual(input,['zoom']);
 assert.deepEqual(zoomUses(expr),[1],'exactly one zoom reference, as the curve input');
}

check('R1 hover bump stays inside a single top-level zoom curve',()=>{
 const base=lineWidthExpr(2.2);
 const b=hoverBump(base,1);
 assertTopLevelZoomCurve(b);
 assert.deepEqual(b.slice(0,3),base.slice(0,3));
 const bump=['case',['boolean',['feature-state','hover'],false],1,0];
 for(let i=3;i<base.length;i+=2){assert.equal(b[i],base[i]);assert.deepEqual(b[i+1],['+',base[i+1],bump]);}
 const r=hoverBump(circleRadiusExpr(5),1.5);
 assertTopLevelZoomCurve(r);
 assert.equal(r[4][2][2],1.5);
 assert.deepEqual(base,lineWidthExpr(2.2),'hoverBump must not mutate its input');
});

check('R1 heatmap radius and 5-step palette ramp',()=>{
 assert.deepEqual(heatmapRadiusExpr(),['interpolate',['linear'],['zoom'],0,8,12,24]);
 const h=heatmapColorExpr('viridis');
 assert.equal(h[0],'interpolate');
 assert.deepEqual([h[3],h[5],h[7],h[9],h[11]],[0,0.25,0.5,0.75,1]);
 assert.equal(h[4],'rgba(68,1,84,0.06)');
 assert.equal(h[12],'rgba(253,231,37,1)');
});

check('R1 cluster config, source knobs and halo radius',()=>{
 const pts=feats([1,2,3]);
 const lines=pts.map(f=>({type:'Feature',id:f.id,properties:f.properties,geometry:{type:'LineString',coordinates:[[0,0],[1,1]]}}));
 const clustered=dataSourceOptions(pts,true);
 assert.deepEqual([clustered.cluster,clustered.clusterRadius,clustered.clusterMaxZoom],[true,60,14]);
 assert.deepEqual([clustered.buffer,clustered.tolerance,clustered.maxzoom],[64,0.375,18]);
 assert.equal('minzoom' in clustered,false,'MapLibre refuses a geojson source with minzoom');
 const plain=dataSourceOptions(pts,false);
 assert.equal('cluster' in plain,false);
 const lineOpts=dataSourceOptions(lines,false);
 assert.deepEqual([lineOpts.buffer,lineOpts.tolerance],[40,2]);
 const mixed=dataSourceOptions([...pts,...lines],true);
 assert.deepEqual([mixed.buffer,mixed.tolerance],[40,2]);
 assert.equal(mixed.cluster,true);
 const cr=clusterCircleRadiusExpr();
 assertTopLevelZoomCurve(cr);
 const halo=['step',['get','point_count'],14,25,20,100,28];
 assert.deepEqual(cr,['step',['zoom'],halo,8,['*',halo,1.5]]);
 assert.equal(hasPointFeatures(pts),true);
 assert.equal(hasPointFeatures(lines),false);
});

check('R1 withAlpha converts hex to rgba and passes through junk',()=>{
 assert.equal(withAlpha('#440154',0.06),'rgba(68,1,84,0.06)');
 assert.equal(withAlpha('nope',0.5),'nope');
});

console.log(JSON.stringify({passed:true,checks}));
"""


@unittest.skipUnless(shutil.which('node'), 'node is required to run the guest core')
class RendererCoreBehaviorTests(unittest.TestCase):
    """Runs each guest's extracted core under node against a synthetic state."""

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        with tempfile.TemporaryDirectory() as tmp:
            for name in GUESTS:
                core = extract_core(read_guest(name))
                driver = pathlib.Path(tmp) / ('driver_%s.js' % name)
                driver.write_text(core + '\n' + DRIVER, encoding='utf-8')
                proc = subprocess.run(['node', str(driver)],
                                      capture_output=True, text=True, timeout=120)
                cls.results[name] = proc

    def test_classification_methods_on_synthetic_state(self):
        for name, proc in self.results.items():
            with self.subTest(guest=name):
                self.assertEqual(proc.returncode, 0,
                                 'node driver failed for %s:\n%s' % (name, proc.stderr))
                payload = json.loads(proc.stdout.strip().splitlines()[-1])
                self.assertTrue(payload['passed'])
                self.assertGreaterEqual(payload['checks'], 24)

    def test_guest_cores_behave_identically(self):
        for name, proc in self.results.items():
            with self.subTest(guest=name):
                self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.results['mapbox'].stdout,
                         self.results['keyless'].stdout,
                         'both guests must produce identical classification results')


if __name__ == '__main__':
    unittest.main()
