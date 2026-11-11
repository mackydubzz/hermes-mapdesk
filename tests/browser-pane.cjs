// Pane integration check: the real pane (React + React Query), the real HTTP API and SQLite store, and
// the real keyless map guest in a sandboxed iframe. Opt-in (needs Edge and network for the map library
// and tiles): npm --prefix dev run test:pane
// It asserts what the user sees and what it costs: every layer drawn, one request burst per action, no
// tile rebuild for an unchanged layer, the camera saved quietly, saved maps and every export button.
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
    const stub = `<html><body>Stub map<script>addEventListener('message',e=>{const d=e.data;if(d&&d.type==='mapdesk:init')parent.postMessage({version:1,type:String(d.token).includes('fail')?'mapdesk:error':'mapdesk:loaded',channel:d.channel},'*');});parent.postMessage({version:1,type:'mapdesk:ready'},'*');</script></body></html>`;
    await page.route('**/api/bootstrap', route => route.fulfill({json:tokenMode.startsWith('public')?{token:tokenMode==='public-ok'?'pk.test-only-ok':'pk.test-only-fail',basemap:'mapbox',token_rejected:false}:{token:'',basemap:'keyless',token_rejected:tokenMode==='rejected'}}));
    await page.route('**/api/renderer', route => route.fulfill({json:tokenMode.startsWith('public')?{html:stub,kind:'mapbox'}:{html:fs.readFileSync(path.join(root,'desktop/map_keyless.html'),'utf8'),kind:'keyless'}}));
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

    // 1. Every stored layer is fetched once and drawn, and opening the pane writes nothing.
    const opened = await requests();
    assert.equal(opened['GET /layer/:id'], 3, 'each visible layer body is fetched once: '+JSON.stringify(opened));
    assert.ok(!Object.keys(opened).some(key => key.startsWith('POST')), 'opening the pane must not write: '+JSON.stringify(opened));
    const drawn = await frame.evaluate(() => ({sources:Object.keys(map.getStyle().sources).filter(id=>id.startsWith('data-')).length,features:map.queryRenderedFeatures({layers:map.getStyle().layers.filter(l=>l.metadata&&l.metadata.mapdesk).map(l=>l.id)}).length}));
    assert.equal(drawn.sources, 3); assert.ok(drawn.features > 100, 'features are on screen: '+JSON.stringify(drawn));
    passed.push('all layers fetched once and drawn');

    // 2. Hide a layer: one write, one refresh, nothing re-tiled.
    await mark(); await page.getByRole('checkbox').nth(0).click(); await sleep(1800);
    let seen = await requests();
    assert.equal(seen['POST update_layer'], 1); assert.ok((seen['GET /summary']||0) <= 2 && !seen['GET /layer/:id'], 'toggle cost: '+JSON.stringify(seen));
    assert.equal((await guest()).setData, 0, 'hiding a layer must not rebuild tiles');
    await page.getByRole('checkbox').nth(0).click(); await sleep(1500);
    passed.push('layer toggle without re-tiling');

    // 3. Pan: the camera is saved once, quietly, and never pushed back at the map.
    await mark(); await frame.evaluate(() => map.panBy([160,0],{duration:200})); await sleep(2200);
    seen = await requests();
    assert.equal(seen['POST viewport'], 1, 'one camera save per move: '+JSON.stringify(seen));
    assert.deepEqual(await guest(), {setData:0,jump:0}, 'a saved camera must not come back as a jump or a re-tile');
    const saved = (await api('/summary')).viewport, live = await frame.evaluate(() => ({center:map.getCenter().toArray(),zoom:map.getZoom()}));
    assert.ok(Math.abs(saved.center[0]-live.center[0]) < 1e-6 && Math.abs(saved.zoom-live.zoom) < 1e-6, 'the stored camera is the one on screen');
    passed.push('camera saved once, no echo');

    // 4. Resizing the attributes panel is not a camera move.
    await mark(); const sash = await page.getByRole('separator').boundingBox();
    await page.mouse.move(sash.x+sash.width/2, sash.y+4); await page.mouse.down();
    for (let i = 1; i <= 25; i++) { await page.mouse.move(sash.x+sash.width/2, sash.y+4-i*4); await sleep(16); }
    await page.mouse.up(); await sleep(1200);
    seen = await requests();
    assert.ok(!seen['POST viewport'], 'dragging the sash must not save the camera: '+JSON.stringify(seen));
    passed.push('panel resize writes nothing');

    // 5. A camera change from outside (the agent) does move the map.
    await mark(); await command('viewport',{center:[-101.5,32.3],zoom:9.25});
    await page.evaluate(() => window.__socket && window.__socket({type:'revision'})); await sleep(2000);
    const moved = await frame.evaluate(() => ({center:map.getCenter().toArray(),zoom:map.getZoom()}));
    assert.ok(Math.abs(moved.center[0]+101.5) < 1e-6 && Math.abs(moved.zoom-9.25) < 1e-6, 'external viewport applied: '+JSON.stringify(moved));
    assert.ok(!(await requests())['POST viewport'], 'an applied external camera is not written back');
    await command('viewport',{center:[-102,32],zoom:8.5}); await page.evaluate(() => window.__socket({type:'revision'})); await sleep(1800);
    passed.push('external camera change applied once');

    // 6. Filter: the table answers at once, the map redraws once after the pause.
    await mark(); const filter = page.getByPlaceholder('Filter rows and map');
    await filter.click(); await page.keyboard.type('plugged',{delay:60});
    await page.getByText(/100 of 300 on the map/).first().waitFor({timeout:4000});
    await sleep(900);
    assert.ok((await guest()).setData <= 1, 'typing redraws the filtered layer once, not per keystroke');
    assert.deepEqual(await requests(), {}, 'filtering is local');
    await filter.fill(''); await sleep(900);
    passed.push('filter redraws once');

    // 7. Row click selects through the store.
    await mark(); await page.getByRole('row').nth(3).click(); await sleep(1500);
    assert.equal((await requests())['POST select'], 1);
    assert.equal((await api('/selection')).total, 1);
    passed.push('row selection');

    // 8. Save map writes a snapshot and lists it.
    await page.getByRole('button',{name:'Save map',exact:true}).click();
    await page.getByText(/Saved maps · 1/).waitFor({timeout:8000});
    const files = await page.evaluate(() => [...window.__files.keys()]);
    assert.ok(files.includes('data/saved-maps.json') && files.some(name => name.startsWith('data/saved-map-m')), 'snapshot files: '+files.join(', '));
    const snapshot = await page.evaluate(() => { const key=[...window.__files.keys()].find(name=>name.startsWith('data/saved-map-m')); return JSON.parse(window.__files.get(key)); });
    assert.equal(snapshot.layers.length, 3); assert.ok(snapshot.layers.every(layer => layer.geojson && layer.geojson.features.length), 'a saved map carries its features');
    passed.push('saved map snapshot');

    // 9. Every export button produces a real file.
    const download = async (label, pattern) => {
      const [file] = await Promise.all([page.waitForEvent('download',{timeout:20000}), page.getByRole('button',{name:label,exact:true}).first().click()]);
      const target = path.join(scratch, 'dl-'+file.suggestedFilename()); await file.saveAs(target);
      const size = fs.statSync(target).size;
      assert.ok(pattern.test(file.suggestedFilename()), label+' filename '+file.suggestedFilename()); assert.ok(size > 200, label+' is empty ('+size+' bytes)');
      return {label, file:file.suggestedFilename(), bytes:size, target};
    };
    const exportsDone = [];
    await page.getByText('Export ▾').first().click(); // the per-layer export menu of the first layer card
    for (const [label, pattern] of [['GeoJSON',/\.geojson$/],['CSV',/\.csv$/],['KMZ',/\.kmz$/],['Shapefile',/\.zip$/],['GeoPackage',/\.gpkg$/],['Export GeoJSON',/\.geojson$/],['Export workspace',/\.json$/],['Export GeoPackage',/\.gpkg$/]]) exportsDone.push(await download(label, pattern));
    const geojson = JSON.parse(fs.readFileSync(exportsDone[0].target,'utf8'));
    assert.equal(geojson.type,'FeatureCollection'); assert.ok(geojson.features.length > 0);
    const png = await Promise.all([page.waitForEvent('download',{timeout:20000}), page.getByRole('button',{name:'Snapshot PNG',exact:true}).click()]);
    const pngPath = path.join(scratch,'snapshot.png'); await png[0].saveAs(pngPath);
    assert.ok(fs.statSync(pngPath).size > 20000, 'Snapshot PNG is a real image ('+fs.statSync(pngPath).size+' bytes)');
    passed.push('exports: '+exportsDone.map(item => item.label).join(', ')+', Snapshot PNG');

    // 10. An empty workspace offers clearly labeled synthetic sample data, and loading it draws three layers.
    for (const id of [leases, roads, wells]) await command('remove_layer',{layer_id:id});
    await page.evaluate(() => window.__socket({type:'revision'}));
    await page.getByRole('button',{name:'Load sample data',exact:true}).click();
    await page.getByText('Sample sites (synthetic)').first().waitFor({timeout:20000});
    await sleep(2500); await settle(4000);
    const sample = (await api('/summary')).layers;
    assert.deepEqual(sample.map(layer => layer.name), ['Sample areas (synthetic)','Sample routes (synthetic)','Sample sites (synthetic)']);
    assert.deepEqual(sample.map(layer => layer.feature_count), [12,5,180]);
    assert.ok(sample.every(layer => layer.style.color_by), 'each sample layer is colored by a field: '+JSON.stringify(sample.map(layer => layer.style.color_by)));
    const sampleDrawn = await frame.evaluate(() => ({sources:Object.keys(map.getStyle().sources).filter(id=>id.startsWith('data-')).length,features:map.queryRenderedFeatures({layers:map.getStyle().layers.filter(l=>l.metadata&&l.metadata.mapdesk).map(l=>l.id)}).length}));
    assert.equal(sampleDrawn.sources, 3); assert.ok(sampleDrawn.features > 100, 'sample features on screen: '+JSON.stringify(sampleDrawn));
    assert.equal(await page.getByRole('button',{name:'Load sample data',exact:true}).count(), 0, 'the sample offer goes away once there is data');
    passed.push('sample data loads and draws');

    // 11. Settings: Setup & help says what is true for each token state, and Check setup swaps the map without touching the data.
    const setup = page.locator('details[aria-label="MapDesk setup"]');
    await setup.locator('summary').click();
    const says = text => setup.getByText(text).first().waitFor({timeout:40000});
    const checkSetup = async mode => { tokenMode = mode; await setup.getByRole('button',{name:'Check setup',exact:true}).click(); };
    await says('Basemap: free OpenFreeMap. No token or account needed.');
    await says('Layers: 3 saved layers.');
    await says('Storage: connected.');
    await checkSetup('public-ok'); await says('Basemap: Mapbox, loaded with your token.');
    await checkSetup('public-fail'); await says('a Mapbox token is saved, but the map did not load');
    await checkSetup('rejected'); await says('The saved token is not a public token');
    await checkSetup('none'); await says('Basemap: free OpenFreeMap. No token or account needed.');
    await page.getByText('Map ready',{exact:true}).waitFor({timeout:60000});
    const lastFrame = page.frames().filter(f => f !== page.mainFrame() && !f.isDetached()).pop();
    await lastFrame.evaluate(() => new Promise(r => { let done=false; const finish=()=>{if(!done){done=true;r();}}; map.once('idle',finish); setTimeout(finish,5000); }));
    assert.equal(await lastFrame.evaluate(() => Object.keys(map.getStyle().sources).filter(id => id.startsWith('data-')).length), 3, 'the reloaded map draws the same three layers');
    assert.equal((await api('/summary')).layers.length, 3, 'Check setup never touches stored layers');
    if (process.env.MAPDESK_PANE_SHOT) await setup.screenshot({path:process.env.MAPDESK_PANE_SHOT});
    passed.push('settings: free, Mapbox loaded, Mapbox failed, rejected token, back to free');

    assert.deepEqual(pageErrors, [], 'no page errors');
    console.log(JSON.stringify({passed:true,checks:passed,exports:exportsDone.map(({label,file,bytes})=>({label,file,bytes}))},null,2));
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
