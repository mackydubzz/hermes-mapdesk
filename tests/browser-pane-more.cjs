// Extended pane click-through on the keyless map: every control the base pane test (browser-pane.cjs) does not
// drive. Basemap swap, terrain, heatmap, cluster, style controls, rename, reorder, zoom, table tools, select by
// location, map click/box/radius selection, measure, paste and file imports with review, geocoding (stubbed),
// provenance, remove, saved maps restore/delete, clear with undo, refresh. Opt-in (Edge + network for the map):
//   npm --prefix dev run test:pane-more      (add MAPDESK_NETWORK_TESTS=1 for the live ArcGIS import step)
// MAPDESK_LIVE_TEST=1 MAPDESK_PANE_GUEST=mapbox runs it on the Mapbox guest with the profile's token (live map loads).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const tools = require('./dev-tools.cjs');
const esbuild = tools('esbuild'), {chromium} = tools('playwright');
const root = path.join(__dirname, '..');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const SDK = `import React from 'react';
export {useQuery} from '@tanstack/react-query';
export const atom=initial=>{let value=initial;const listeners=new Set();return {get:()=>value,set:next=>{value=next;listeners.forEach(fn=>fn());},subscribe:fn=>{listeners.add(fn);return ()=>listeners.delete(fn);}};};
export const PALETTE_AREA='palette',ROUTES_AREA='routes',SIDEBAR_NAV_AREA='sidebar';
const fixed=value=>({get:()=>value,subscribe:()=>()=>{}});
export const host={state:{connectionId:fixed('test'),profile:fixed('isolated-test'),activeSessionId:fixed('session-1')},navigate(){},openWorkspace(){},notify(notice){window.lastNotice=notice;}};
export const useValue=a=>React.useSyncExternalStore(a.subscribe||(()=>()=>{}),a.get);
export const Button=({variant,size,children,...props})=>React.createElement('button',props,children);
export const SandboxedFrame=React.forwardRef(({src,...props},ref)=>React.createElement('iframe',{...props,ref,sandbox:'allow-scripts',referrerPolicy:'no-referrer',src}));
export const SelectItem=()=>null,SelectContent=()=>null,SelectTrigger=()=>null,SelectValue=()=>null;
export const Select=({value,onValueChange,disabled,children})=>{const kids=React.Children.toArray(children);const trigger=kids.find(k=>k.type===SelectTrigger);const content=kids.find(k=>k.type===SelectContent);const items=React.Children.toArray(content?content.props.children:[]).filter(Boolean);return React.createElement('select',{value,disabled,'aria-label':trigger&&trigger.props['aria-label'],style:trigger&&trigger.props.style,onChange:e=>onValueChange(e.target.value)},items.map(i=>React.createElement('option',{key:i.props.value,value:i.props.value},i.props.children)));};`;

function dataset() {
  let seed = 20261111; const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
  const wells = [], roads = [], leases = [];
  for (let i = 0; i < 300; i++) wells.push({type:'Feature',properties:{api:'42-'+String(i).padStart(4,'0'),status:['PRODUCING','SHUT-IN','PLUGGED'][i%3],depth_ft:4000+Math.round(rnd()*9000)},geometry:{type:'Point',coordinates:[-102.4+rnd()*0.8,31.7+rnd()*0.6]}});
  for (let i = 0; i < 40; i++) { const x=-102.4+rnd()*0.7, y=31.7+rnd()*0.5; roads.push({type:'Feature',properties:{name:'Road '+i,lanes:1+i%4},geometry:{type:'LineString',coordinates:[[x,y],[x+0.05,y+0.02],[x+0.1,y+0.05]]}}); }
  for (let i = 0; i < 30; i++) { const x=-102.4+(i%6)*0.13, y=31.7+Math.floor(i/6)*0.11; leases.push({type:'Feature',properties:{lease:'L'+i,acres:Math.round(100+rnd()*900)},geometry:{type:'Polygon',coordinates:[[[x,y],[x+0.1,y],[x+0.1,y+0.08],[x,y+0.08],[x,y]]]}}); }
  const fc = features => ({type:'FeatureCollection',features});
  return {wells:fc(wells),roads:fc(roads),leases:fc(leases)};
}

(async () => {
  const scratch = fs.mkdtempSync(path.join(process.env.MAPDESK_TEST_SCRATCH || os.tmpdir(), 'mapdesk-pane-'));
  const bundle = path.join(scratch, 'ui.js');
  await esbuild.build({entryPoints:[path.join(__dirname,'ui-harness.jsx')],nodePaths:[path.join(root,'dev/node_modules')],outfile:bundle,bundle:true,format:'iife',platform:'browser',logLevel:'error',plugins:[{name:'test-sdk',setup(build){build.onResolve({filter:/^@hermes\/plugin-sdk$/},()=>({path:'sdk',namespace:'test-sdk'}));build.onLoad({filter:/.*/,namespace:'test-sdk'},()=>({contents:SDK,resolveDir:root}));}}]});
  const python = path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  const server = spawn(python,['-u',path.join(__dirname,'ui-server.py'),bundle],{cwd:root,env:{...process.env,PYTHONPATH:'',TMPDIR:scratch,TMP:scratch,TEMP:scratch},stdio:['ignore','pipe','pipe']});
  let stderr = '', browser, base, page;
  server.stderr.on('data', d => stderr += d);
  try {
    base = await new Promise((resolve, reject) => { let text=''; const timer=setTimeout(()=>reject(new Error('Test server startup timeout: '+stderr.slice(-400))),20000); server.stdout.on('data',d=>{text+=d;if(text.includes('\n')){clearTimeout(timer);resolve('http://127.0.0.1:'+JSON.parse(text.split('\n')[0]).port);}}); server.on('exit',code=>reject(new Error('Test server exited '+code+' '+stderr.slice(-400)))); });
    const api = async (pathname, body) => { const response = await fetch(base+'/api'+pathname, body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined); const json = await response.json(); if(!response.ok) throw new Error(pathname+' '+JSON.stringify(json).slice(0,200)); return json; };
    const command = (action, args) => api('/command', {action, args});
    const data = dataset();
    const leases = (await command('import_geojson',{name:'Leases',geojson:data.leases})).id;
    const roads = (await command('import_geojson',{name:'Roads',geojson:data.roads})).id;
    const wells = (await command('import_geojson',{name:'Wells',geojson:data.wells})).id;
    await command('update_layer',{layer_id:wells,changes:{style:{color_by:'status'}}});
    await command('viewport',{center:[-102,32],zoom:8.5});

    browser = await chromium.launch({channel:'msedge',headless:true,args:['--enable-gpu','--ignore-gpu-blocklist','--use-angle=d3d11']});
    page = await browser.newPage({viewport:{width:1500,height:950}});
    const pageErrors = []; page.on('pageerror', e => pageErrors.push(String(e.message).slice(0,200)));
    // The token setting is simulated at the HTTP boundary (no real token, no Mapbox load): a stub guest stands in
    // for the Mapbox map and reports a load or a failure depending on the test token it is handed; without a token the real keyless guest runs.
    let tokenMode = 'none';
    // Opt-in: MAPDESK_LIVE_TEST=1 MAPDESK_PANE_GUEST=mapbox runs the same click-through on the real Mapbox guest
    // with the profile's scoped public token. The token stays in this process's memory and is never printed.
    const liveMapbox = process.env.MAPDESK_PANE_GUEST === 'mapbox';
    let liveToken = '';
    if (liveMapbox) {
      if (process.env.MAPDESK_LIVE_TEST !== '1') throw new Error('Set MAPDESK_LIVE_TEST=1 to allow live Mapbox map loads.');
      liveToken = require('node:child_process').execFileSync('python', ['-c', 'from agent.secret_scope import get_secret; import sys; sys.stdout.write(get_secret("MAPDESK_MAPBOX_ACCESS_TOKEN") or "")'], {encoding:'utf8'}).trim();
      assert.ok(liveToken.startsWith('pk.'), 'A scoped public token is required');
    }
    const stub = `<html><body>Stub map<script>addEventListener('message',e=>{const d=e.data;if(d&&d.type==='mapdesk:init')parent.postMessage({version:1,type:String(d.token).includes('fail')?'mapdesk:error':'mapdesk:loaded',channel:d.channel},'*');});parent.postMessage({version:1,type:'mapdesk:ready'},'*');</script></body></html>`;
    await page.route('**/api/bootstrap', route => route.fulfill({json:liveMapbox?{token:liveToken,basemap:'mapbox',token_rejected:false}:tokenMode.startsWith('public')?{token:tokenMode==='public-ok'?'pk.test-only-ok':'pk.test-only-fail',basemap:'mapbox',token_rejected:false}:{token:'',basemap:'keyless',token_rejected:tokenMode==='rejected'}}));
    await page.route('**/api/renderer', route => route.fulfill({json:liveMapbox?{html:fs.readFileSync(path.join(root,'desktop/map.html'),'utf8'),kind:'mapbox'}:tokenMode.startsWith('public')?{html:stub,kind:'mapbox'}:{html:fs.readFileSync(path.join(root,'desktop/map_keyless.html'),'utf8'),kind:'keyless'}}));
    await page.addInitScript(() => { window.__simulatePush = true; window.__viewportMsgs = 0; addEventListener('message', e => { if (e.data && e.data.type === 'mapdesk:viewport') window.__viewportMsgs++; }); });
    await page.goto(base);
    await page.getByText('Map ready',{exact:true}).waitFor({timeout:90000});
    const frame = page.frames().find(f => f !== page.mainFrame());
    const settle = (ms=2500) => frame.evaluate(ms => new Promise(r => { let done=false; const finish=()=>{if(!done){done=true;r();}}; map.once('idle',finish); setTimeout(finish,ms); }), ms);
    await settle(6000); await sleep(600);
    await frame.evaluate(() => { window.__c={setData:0,jump:0}; const jump=map.jumpTo.bind(map); map.jumpTo=o=>{window.__c.jump++;return jump(o);}; for(const id of Object.keys(map.getStyle().sources)){const s=map.getSource(id);if(id.startsWith('data-')&&s.setData){const o=s.setData.bind(s);s.setData=d=>{window.__c.setData++;return o(d);};}} });
    const requests = () => page.evaluate(() => { const by={}; for(const r of window.__rest){const k=r.action?'POST '+r.action:'GET '+r.path;by[k]=(by[k]||0)+1;} return by; });
    const mark = async () => { await page.evaluate(() => { window.__rest.length=0; window.__viewportMsgs=0; }); await frame.evaluate(() => { window.__c.setData=0; window.__c.jump=0; }); };
    const guest = () => frame.evaluate(() => ({...window.__c}));
    const passed = [];
    // ---- Extended keyless click-through: every remaining pane control. Each step records pass/fail and carries on.
    const failed = []; page.setDefaultTimeout(5000);
    let current = '(setup)';
    page.on('pageerror', e => console.log('PAGEERROR during [' + current + '] ' + String(e.stack || e.message).replace(/access_token=[^\s&"']+/g, 'access_token=[REDACTED]').split('\n').slice(0, 4).join(' | ').slice(0, 500)));
    const scrub = s => (liveToken ? String(s).split(liveToken).join('[REDACTED]') : String(s)).replace(/access_token=[^\s&"']+/g, 'access_token=[REDACTED]');
    const renders = () => frame.evaluate(() => new Promise(resolve => { let drew = false; map.once('render', () => { drew = true; resolve(true); }); map.triggerRepaint(); setTimeout(() => resolve(drew), 3000); })).catch(() => 'frame gone');
    const health = [];
    const step = async (name, fn) => { current = name; try { await fn(); passed.push(name); if (process.env.MAPDESK_RENDER_HEALTH === '1') health.push(name.slice(0, 28) + '=' + await renders()); } catch (e) { failed.push(scrub(name + ' :: ' + String(e.message).split('\n').slice(0, 7).join(' | ').slice(0, 430))); } };
    const summaryOf = () => api('/summary');
    const layerNamed = async name => (await summaryOf()).layers.find(l => l.name === name);
    const dataLayerIds = () => frame.evaluate(() => map.getStyle().layers.filter(l => l.metadata && l.metadata.mapdesk).map(l => l.id));
    const sources = () => frame.evaluate(() => Object.keys(map.getStyle().sources).filter(id => id.startsWith('data-')).length);
    const box = await page.locator('iframe').boundingBox();
    const pixel = async lngLat => { const p = await frame.evaluate(c => { const q = map.project(c); return [q.x, q.y]; }, lngLat); return [box.x + p[0], box.y + p[1]]; };
    await page.route('**/api/geocode', async route => { const body = JSON.parse(route.request().postData()); await route.fulfill({json:{results:body.addresses.map((a, i) => a.includes('nowhere') ? null : {lon:-102.0 + i * 0.01, lat:32.0})}}); });

    await step('basemap swap: light, streets, satellite, back to dark, layers survive', async () => {
      for (const choice of ['light', 'streets', 'satellite', 'dark']) { await page.getByLabel('Basemap', {exact:true}).selectOption(choice); await sleep(2500); await settle(5000); assert.equal(await sources(), 3, choice + ' keeps the data sources'); }
    });
    await step('terrain 3D on and off', async () => {
      await page.getByRole('button', {name:/^Terrain 3D/}).click(); await sleep(2500);
      assert.ok(await frame.evaluate(() => !!(map.getTerrain && map.getTerrain())), 'terrain is set');
      await page.getByRole('button', {name:/^Terrain 3D/}).click(); await sleep(1500);
      assert.ok(await frame.evaluate(() => !(map.getTerrain && map.getTerrain())), 'terrain is cleared');
    });
    await step('heatmap on and off', async () => {
      await page.getByRole('button', {name:/^Heatmap/}).click(); await sleep(1500);
      assert.ok((await dataLayerIds()).some(id => id.endsWith('-heatmap')));
      await page.getByRole('button', {name:/^Heatmap/}).click(); await sleep(1200);
      assert.ok(!(await dataLayerIds()).some(id => id.endsWith('-heatmap')));
    });
    await step('cluster on and off', async () => {
      await page.getByRole('button', {name:/^Cluster/}).click(); await sleep(2500);
      assert.ok((await dataLayerIds()).some(id => id.endsWith('-clusters')));
      await page.getByRole('button', {name:/^Cluster/}).click(); await sleep(2500);
      assert.ok(!(await dataLayerIds()).some(id => id.endsWith('-clusters')));
    });
    await step('style selects: color field, class method, class count, palette, label', async () => {
      await page.locator('summary', {hasText:'Labels & classes'}).nth(2).click();
      await page.getByLabel('Color field for Wells').selectOption('depth_ft'); await sleep(700);
      await page.getByLabel('Class method for Wells').selectOption('quantile'); await sleep(700);
      await page.getByLabel('Class count for Wells').selectOption('4'); await sleep(700);
      await page.getByLabel('Palette for Wells').selectOption('viridis'); await sleep(700);
      await page.getByLabel('Label field for Wells').selectOption('api'); await sleep(1200);
      const style = (await layerNamed('Wells')).style;
      assert.deepEqual([style.color_by, style.class_method, style.class_count, style.palette, style.label_field], ['depth_ft', 'quantile', 4, 'viridis', 'api']);
      assert.ok((await dataLayerIds()).some(id => id.endsWith('-label')), 'a label layer is drawn');
    });
    await step('color picker and opacity slider', async () => {
      await page.getByLabel('Color for Roads').fill('#ff0000'); await sleep(900);
      const slider = page.getByLabel('Opacity for Roads'); await slider.focus();
      for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowLeft');
      await sleep(1200);
      const style = (await layerNamed('Roads')).style;
      assert.equal(style.color, '#ff0000'); assert.ok(style.opacity < 0.8, 'opacity lowered: ' + style.opacity);
    });
    await step('rename a layer from its card', async () => {
      await page.locator('summary', {hasText:'Rename'}).nth(1).click();
      const input = page.getByLabel('New name for Roads'); await input.fill('Roads renamed');
      await input.locator('xpath=ancestor::details[1]').getByRole('button', {name:'Save name', exact:true}).click(); await sleep(1200);
      assert.ok(await layerNamed('Roads renamed'), 'layer renamed in the store');
    });
    await step('reorder with the arrow buttons', async () => {
      const before = (await summaryOf()).layers.map(l => l.name);
      await page.getByLabel('Move Wells up').click(); await sleep(1200);
      const after = (await summaryOf()).layers.map(l => l.name);
      assert.notDeepEqual(after, before); assert.deepEqual([...after].sort(), [...before].sort());
    });
    await step('zoom to layer moves the camera onto it', async () => {
      await frame.evaluate(() => map.jumpTo({center:[-100, 30], zoom:5})); await sleep(900);
      await page.getByRole('button', {name:'Zoom to layer', exact:true}).first().click(); await sleep(2500);
      const center = await frame.evaluate(() => map.getCenter().toArray());
      assert.ok(center[0] > -102.6 && center[0] < -101.4 && center[1] > 31.5 && center[1] < 32.5, 'camera at ' + center);
    });
    await step('make a layer the active selection layer from its card', async () => {
      const card = page.getByLabel('Move Wells up').locator('xpath=ancestor::*[.//button[contains(.,"Zoom to layer")]][1]');
      await card.getByRole('button', {name:/Select in this layer|Active selection layer/}).click(); await sleep(1500);
      assert.equal((await api('/selection')).layer_id, wells);
      await page.getByLabel('Filter Wells').waitFor();
    });
    await step('table paging, select filtered, invert, clear, fit to selection', async () => {
      await page.getByRole('button', {name:'Next', exact:true}).click(); await sleep(400);
      await page.getByRole('button', {name:'Prev', exact:true}).click(); await sleep(400);
      await page.getByPlaceholder('Filter rows and map').fill('plugged'); await sleep(700);
      await page.getByRole('button', {name:'Select filtered', exact:true}).click(); await sleep(1500);
      assert.equal((await api('/selection')).total, 100);
      await page.getByRole('button', {name:'Fit to selection', exact:true}).click(); await sleep(1500);
      await page.getByPlaceholder('Filter rows and map').fill(''); await sleep(600);
      await page.getByRole('button', {name:'Invert', exact:true}).click(); await sleep(1500);
      assert.equal((await api('/selection')).total, 200);
      // "Clear" in the table toolbar clears the filter, so it is only enabled while a filter is set.
      await page.getByPlaceholder('Filter rows and map').fill('shut'); await sleep(500);
      await page.getByRole('button', {name:'Clear', exact:true}).click(); await sleep(500);
      assert.equal(await page.getByPlaceholder('Filter rows and map').inputValue(), '');
    });
    await step('select by location from the table', async () => {
      await page.locator('summary', {hasText:'By location'}).click();
      await page.getByLabel('Target layer for Wells').selectOption(leases);
      await page.getByLabel('Spatial operator for Wells').selectOption('within');
      await page.getByRole('button', {name:'Apply', exact:true}).click(); await sleep(1800);
      const expected = (await command('query', {layer_id:wells, spatial:{op:'within', layer_id:leases}, limit:1000})).total;
      assert.ok(expected > 0); assert.equal((await api('/selection')).total, expected);
    });
    await step('map: click selects one feature, hover shows a tooltip', async () => {
      await frame.evaluate(() => map.jumpTo({center:[-102, 32], zoom:10})); await sleep(2500); await settle(3000);
      const target = await frame.evaluate(() => { const f = map.queryRenderedFeatures({layers:map.getStyle().layers.filter(l => l.id.endsWith('-circle')).map(l => l.id)})[0]; return f.geometry.coordinates; });
      const [x, y] = await pixel(target);
      await page.mouse.move(x, y); await sleep(500);
      assert.ok(await frame.evaluate(() => getComputedStyle(document.getElementById('tooltip')).display !== 'none'), 'tooltip visible on hover');
      const hoveredFeature = await frame.evaluate(() => hovered && {source:hovered.source, id:hovered.id});
      assert.ok(hoveredFeature, 'the guest tracks the hovered feature');
      assert.equal((await frame.evaluate(target => map.getFeatureState(target), hoveredFeature)).hover, true);
      await page.mouse.click(x, y); await sleep(1800);
      assert.ok((await api('/selection')).total >= 1, 'click selected a feature');
      // A reorder or a removal rebuilds every data layer. The hover state must be gone before that happens: a
      // leftover state on the source is what lets Mapbox GL throw in the frame after the layers are re-added.
      await page.mouse.move(x, y); await sleep(500);
      assert.equal((await frame.evaluate(target => map.getFeatureState(target), hoveredFeature)).hover, true);
      await frame.evaluate(() => { orderKey = null; render(state); });
      assert.deepEqual(await frame.evaluate(target => [map.getFeatureState(target), hovered], hoveredFeature), [{}, null]);
      await settle(3000);
    });
    await step('map: shift-drag box select and ctrl-drag radius select', async () => {
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      await page.keyboard.down('Shift'); await page.mouse.move(cx - 120, cy - 90); await page.mouse.down(); await page.mouse.move(cx + 120, cy + 90, {steps:6}); await page.mouse.up(); await page.keyboard.up('Shift'); await sleep(1800);
      const boxed = await api('/selection'); assert.ok(boxed.total > 1 && boxed.bbox, 'box selection: ' + boxed.total);
      await page.keyboard.down('Control'); await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + 110, cy, {steps:6}); await page.mouse.up(); await page.keyboard.up('Control'); await sleep(1800);
      const round = await api('/selection'); assert.ok(round.radius && round.radius.meters > 0 && round.total > 0, 'radius selection: ' + JSON.stringify(round.radius));
    });
    await step('measure mode draws a length and select mode returns', async () => {
      await page.getByRole('button', {name:'Measure', exact:true}).click(); await sleep(500);
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      await page.mouse.click(cx - 100, cy - 40); await sleep(300); await page.mouse.click(cx + 100, cy + 40); await sleep(300);
      await page.keyboard.press('Enter'); await sleep(700);
      const label = await frame.evaluate(() => { const el = document.getElementById('measure-label'); return getComputedStyle(el).display === 'none' ? '' : el.textContent; });
      assert.ok(/km|\bm\b|mi/.test(label), 'measure label: ' + label);
      await page.keyboard.press('Escape'); await page.getByRole('button', {name:'Select', exact:true}).click(); await sleep(400);
    });
    await step('workspace name is saved', async () => {
      await page.locator('summary', {hasText:'Workspace name'}).click();
      const input = page.getByLabel('Workspace name', {exact:true}); await input.fill('QA workspace');
      await input.locator('xpath=ancestor::details[1]').getByRole('button').first().click(); await sleep(1200);
      assert.equal((await summaryOf()).name, 'QA workspace');
    });
    await step('paste GeoJSON, preview, import', async () => {
      await page.locator('summary', {hasText:'Paste data'}).click();
      await page.getByLabel('Paste GeoJSON, CSV text or an ArcGIS layer URL').fill(JSON.stringify({type:'FeatureCollection', features:[{type:'Feature', properties:{k:'a'}, geometry:{type:'Point', coordinates:[-102.01, 32.01]}}, {type:'Feature', properties:{k:'b'}, geometry:{type:'Point', coordinates:[-101.99, 31.99]}}]}));
      await page.getByRole('button', {name:'Use pasted data', exact:true}).click(); await sleep(700);
      await page.getByLabel('Dataset name', {exact:true}).fill('Pasted points');
      await page.getByRole('button', {name:'Import dataset', exact:true}).click(); await sleep(2500);
      assert.equal((await layerNamed('Pasted points')).feature_count, 2);
    });
    await step('CSV file: cancel leaves nothing, then import with chosen columns', async () => {
      const csv = 'site,LAT,LON,rate\nS1,31.98,-102.02,120\nS2,32.02,-101.96,85\n';
      const choose = () => page.getByLabel('Choose a data file').setInputFiles({name:'stations.csv', mimeType:'text/csv', buffer:Buffer.from(csv)});
      const count = (await summaryOf()).layers.length;
      await choose(); await sleep(700);
      await page.getByRole('button', {name:'Cancel import', exact:true}).click(); await sleep(600);
      assert.equal((await summaryOf()).layers.length, count, 'cancel imports nothing');
      await choose(); await sleep(700);
      await page.getByLabel('Latitude column', {exact:true}).selectOption('LAT');
      await page.getByLabel('Longitude column', {exact:true}).selectOption('LON');
      await page.getByLabel('Dataset name', {exact:true}).fill('CSV stations');
      await page.getByRole('button', {name:'Import dataset', exact:true}).click(); await sleep(2500);
      assert.equal((await layerNamed('CSV stations')).feature_count, 2);
    });
    await step('CSV without coordinates is geocoded by an address column (geocoder stubbed)', async () => {
      const csv = 'name,address\nOffice,300 N Main St Midland TX\nGhost,nowhere at all\nYard,100 W Wall St Midland TX\n';
      await page.getByLabel('Choose a data file').setInputFiles({name:'addresses.csv', mimeType:'text/csv', buffer:Buffer.from(csv)}); await sleep(800);
      await page.getByLabel('Address column (geocodes rows without coordinates)', {exact:true}).selectOption('address');
      await page.getByLabel('Dataset name', {exact:true}).fill('Geocoded sites');
      await page.getByRole('button', {name:'Import dataset', exact:true}).click();
      await page.getByText(/geocoded 2/).first().waitFor({timeout:20000});
      assert.equal((await layerNamed('Geocoded sites')).feature_count, 2);
    });
    if (process.env.MAPDESK_NETWORK_TESTS === '1') await step('public ArcGIS import from the form (live Census TIGERweb request)', async () => {
      await page.locator('summary', {hasText:'Public ArcGIS source'}).click();
      await page.getByLabel('HTTPS layer URL', {exact:true}).fill('https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1');
      await page.getByLabel('Where (optional)', {exact:true}).fill("STATE='48' AND BASENAME='Midland'");
      await page.getByLabel('Layer name (optional)', {exact:true}).fill('ArcGIS Midland');
      await page.getByLabel('West,south,east,north', {exact:true}).fill('-102.4,31.6,-101.1,32.6');
      await page.getByRole('button', {name:'Import ArcGIS', exact:true}).click();
      for (let i = 0; i < 45 && !(await layerNamed('ArcGIS Midland')); i++) await sleep(1000);
      assert.equal((await layerNamed('ArcGIS Midland')).feature_count, 1);
    });
    await step('use current map view fills the ArcGIS envelope', async () => {
      // The live import step above is opt-in, so open the section here when it was skipped.
      const section = page.locator('details', {has:page.locator('summary', {hasText:'Public ArcGIS source'})}).last();
      if (!(await section.evaluate(el => el.open))) await section.locator('summary').first().click();
      await page.getByRole('button', {name:'Use current map view', exact:true}).click(); await sleep(400);
      const value = await page.getByLabel('West,south,east,north', {exact:true}).inputValue();
      assert.equal(value.split(',').length, 4); assert.ok(value.split(',').every(v => Number.isFinite(Number(v))), value);
    });
    await step('provenance shows where a layer came from', async () => {
      await page.locator('summary', {hasText:'Provenance'}).first().click(); await sleep(300);
      assert.ok(await page.getByText(/geojson|arcgis|csv|analysis/i).first().isVisible());
    });
    await step('remove a layer from its card: keep, then confirm', async () => {
      const count = (await summaryOf()).layers.length;
      await page.getByRole('button', {name:'Remove layer', exact:true}).first().click();
      await page.getByRole('button', {name:'Keep layer', exact:true}).click(); await sleep(500);
      assert.equal((await summaryOf()).layers.length, count);
      await page.getByRole('button', {name:'Remove layer', exact:true}).first().click();
      await page.getByRole('button', {name:'Yes, remove', exact:true}).click(); await sleep(1500);
      assert.equal((await summaryOf()).layers.length, count - 1);
    });
    await step('save map, change the workspace, restore it', async () => {
      await page.getByRole('button', {name:'Save map', exact:true}).click();
      await page.getByText(/Saved maps · 1/).waitFor({timeout:8000});
      const saved = (await summaryOf()).layers.map(l => l.name).sort();
      await command('remove_layer', {layer_id:(await summaryOf()).layers[0].id}); await page.evaluate(() => window.__socket({type:'revision'})); await sleep(1200);
      await page.locator('summary', {hasText:'Saved maps'}).click();
      await page.getByRole('button', {name:'Restore', exact:true}).first().click();
      for (let i = 0; i < 30 && (await summaryOf()).layers.length !== saved.length; i++) await sleep(700);
      assert.deepEqual((await summaryOf()).layers.map(l => l.name).sort(), saved);
      await page.getByText('Saved maps · 2').waitFor({timeout:8000}); // the map that was replaced is archived too, and listed at once
    });
    await step('delete a saved map from the list', async () => {
      const list = page.locator('details', {has:page.locator('summary', {hasText:'Saved maps'})}).last();
      if (!(await list.evaluate(el => el.open))) await list.locator('summary').first().click();
      const before = Number((await page.locator('summary', {hasText:'Saved maps'}).textContent()).match(/(\d+)/)[1]);
      await page.getByRole('button', {name:'Delete', exact:true}).first().click();
      await page.getByRole('button', {name:'Yes, delete', exact:true}).click(); await sleep(1200);
      const after = Number((await page.locator('summary', {hasText:'Saved maps'}).textContent()).match(/(\d+)/)[1]);
      assert.equal(after, before - 1);
    });
    await step('clear map archives first, and Undo brings it back', async () => {
      const names = (await summaryOf()).layers.map(l => l.name).sort();
      await page.getByRole('button', {name:'Clear map', exact:true}).first().click(); await sleep(400);
      await page.getByRole('group', {name:'Confirm clearing the workspace'}).getByRole('button', {name:'Clear map', exact:true}).click();
      for (let i = 0; i < 30 && (await summaryOf()).layers.length; i++) await sleep(700);
      assert.equal((await summaryOf()).layers.length, 0, 'workspace cleared');
      await page.getByRole('button', {name:'Undo', exact:true}).click();
      for (let i = 0; i < 40 && (await summaryOf()).layers.length !== names.length; i++) await sleep(700);
      assert.deepEqual((await summaryOf()).layers.map(l => l.name).sort(), names);
    });
    await step('refresh keeps the work and the map', async () => {
      const count = (await summaryOf()).layers.length;
      await page.getByRole('button', {name:'Refresh', exact:true}).click(); await sleep(2500);
      assert.equal((await summaryOf()).layers.length, count);
      await page.getByText('Map ready', {exact:true}).waitFor({timeout:30000});
    });
    console.log(JSON.stringify({passed:passed.length, failed:failed.length, failures:failed, passedSteps:passed}, null, 1));
    if (health.length) console.log('RENDER HEALTH ' + health.filter(entry => !entry.endsWith('=true')).join('; ') + ' | checked ' + health.length);
    assert.deepEqual(failed, [], 'every pane control works');
    assert.deepEqual(pageErrors, [], 'no page errors');
  } catch (error) {
    if (page) { try { console.error('Visible buttons: '+(await page.getByRole('button').allTextContents()).filter(Boolean).slice(0,60).join(' | ')); } catch (_) {} }
    throw error;
  } finally {
    if (browser) await browser.close();
    try { await fetch(base+'/__stop__',{method:'POST'}); } catch (_) {}
    server.kill();
    try { fs.rmSync(scratch,{recursive:true,force:true}); } catch (_) {}
  }
})().catch(error => { console.error(String(error.message).slice(0,900)); process.exitCode = 1; });
