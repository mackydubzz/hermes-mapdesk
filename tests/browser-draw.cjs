// Real-GL draw check: data must actually DRAW, not just pass string contracts.
// Opt-in (needs Edge and network access to unpkg.com / tiles.openfreemap.org): npm --prefix dev run test:draw
// Mapbox guest (ONE billable map load, token read into process memory only, never printed, no HAR/trace):
//   MAPDESK_LIVE_TEST=1 MAPDESK_DRAW_GUEST=mapbox node tests/browser-draw.cjs
// MAPDESK_DRAW_BASEMAP=dark|light|streets|satellite picks the basemap the guest boots with (default dark).
// MAPDESK_DRAW_VIEW=lon,lat,zoom moves the camera before the MAPDESK_DRAW_SHOT screenshot.
// The 0.3.0 guests passed every unit test while MapLibre refused the data source and both GL
// libraries refused the line/circle/cluster layers; this loads the shipped file and looks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = process.env.MAPDESK_PLAYWRIGHT_PATH ? require(process.env.MAPDESK_PLAYWRIGHT_PATH) : require('./dev-tools.cjs')('playwright');
(async () => {
  const mapbox = process.env.MAPDESK_DRAW_GUEST === 'mapbox';
  let token = '';
  if (mapbox) {
    if (process.env.MAPDESK_LIVE_TEST !== '1') throw new Error('Set MAPDESK_LIVE_TEST=1 to allow a live Mapbox map load.');
    token = execFileSync('python', ['-c', 'from agent.secret_scope import get_secret; import sys; sys.stdout.write(get_secret("MAPDESK_MAPBOX_ACCESS_TOKEN") or "")'], {encoding:'utf8'}).trim();
    assert.ok(token.startsWith('pk.'), 'A scoped public token is required');
  }
  const clean = s => (token ? String(s).split(token).join('[REDACTED]') : String(s)).replace(/access_token=[^\s&"']+/g, 'access_token=[REDACTED]');
  const browser = await chromium.launch({channel:'msedge',headless:true,args:['--enable-gpu','--ignore-gpu-blocklist','--use-angle=d3d11']});
  try {
    const page = await browser.newPage({viewport:{width:1000,height:700}});
    const mapErrors = [];
    page.on('console',m=>{const t=m.text();if(t.startsWith('MAPERR '))mapErrors.push(clean(t.slice(7,240)));});
    page.on('pageerror',e=>mapErrors.push('pageerror: '+clean(String(e.message).slice(0,200))));
    const shipped = fs.readFileSync(path.join(__dirname,mapbox?'../desktop/map.html':'../desktop/map_keyless.html'),'utf8');
    // Test-only: surface the message the guest deliberately keeps out of its bridge events.
    const marker = "map.on('error',()=>{";
    assert.ok(shipped.includes(marker),'guest error hook moved; update this test');
    const html = shipped.replace(marker,"map.on('error',(ev)=>{console.log('MAPERR '+(ev&&ev.error&&ev.error.message));");
    await page.setContent('<html><body style="margin:0"><iframe sandbox="allow-scripts" referrerpolicy="no-referrer" style="border:0;width:100vw;height:100vh"></iframe></body></html>');
    await page.evaluate(html => {
      window.events=[];
      const frame=document.querySelector('iframe');
      window.addEventListener('message',e=>{if(e.source===frame.contentWindow)window.events.push(e.data);});
      window.send=(type,payload)=>frame.contentWindow.postMessage({version:1,type,channel:'draw-test-channel-0123456789',...payload},'*');
      frame.src='data:text/html;charset=utf-8,'+encodeURIComponent(html);
    },html);
    await page.waitForFunction(()=>window.events.some(e=>e.type==='mapdesk:ready'),null,{timeout:60000});
    const features=[];
    for(let i=0;i<120;i++)features.push({type:'Feature',id:'w'+i,properties:{name:'Well '+i,status:['A','B','C'][i%3],depth:5000+i*20},geometry:{type:'Point',coordinates:[-102.3+(i%12)*0.05,31.8+Math.floor(i/12)*0.05]}});
    features.push({type:'Feature',id:'line',properties:{name:'Line',status:'A',depth:1},geometry:{type:'LineString',coordinates:[[-102.4,31.7],[-101.6,32.3]]}});
    features.push({type:'Feature',id:77,properties:{name:'Poly',status:'B',depth:2},geometry:{type:'Polygon',coordinates:[[[-101.7,31.8],[-101.5,31.8],[-101.5,32.0],[-101.7,32.0],[-101.7,31.8]]]}});
    const layer=(id,geojson)=>({id,name:id,visible:true,fields:['name','status','depth'],style:{color:'#3388ff',opacity:0.8,color_by:'status',label_field:'name'},data_revision:1,geojson});
    const second={type:'FeatureCollection',features:[{type:'Feature',id:1,properties:{name:'Other',status:'A',depth:3},geometry:{type:'Point',coordinates:[-101.9,32.2]}}]};
    const state={revision:1,name:'draw-test',viewport:{center:[-102,32],zoom:9},selection:{layer_id:'L1',feature_ids:[],bbox:null},layers:[layer('L2',second),layer('L1',{type:'FeatureCollection',features})]};
    // Bodies are sent once; a slim state arriving before the map loads must not lose them.
    await page.evaluate(([state,token,basemap])=>{window.send('mapdesk:init',{token,state,basemap,terrain:false});const slim={...state,revision:2,layers:state.layers.map(({geojson,...rest})=>rest)};window.send('mapdesk:state',{state:slim});},[state,token,process.env.MAPDESK_DRAW_BASEMAP||'dark']);
    await page.waitForFunction(()=>window.events.some(e=>e.type==='mapdesk:loaded'),null,{timeout:90000});
    const frame=page.frames().find(f=>f!==page.mainFrame());
    const settle=(ms=3000)=>frame.evaluate(ms=>new Promise(r=>{let d=false;const f=()=>{if(!d){d=true;r();}};map.once('idle',f);setTimeout(f,ms);}),ms);
    const dataLayers=()=>frame.evaluate(()=>map.getStyle().layers.filter(l=>l.metadata&&l.metadata.mapdesk).map(l=>l.id));
    await settle();
    assert.deepEqual(mapErrors,[],'the GL library must accept every source and layer');
    assert.deepEqual(await frame.evaluate(()=>Object.keys(map.getStyle().sources).filter(id=>id.startsWith('data-')).sort()),['data-L1','data-L2'],'both layers get a source, including the one whose body arrived before load');
    const ids=await dataLayers();
    for(const suffix of ['fill','line','circle','label'])assert.ok(ids.includes('data-L1-'+suffix),'missing data-L1-'+suffix);
    const drawn=await frame.evaluate(()=>{const count=suffix=>map.queryRenderedFeatures({layers:['data-L1-'+suffix]}).length;return {circle:count('circle'),line:count('line'),fill:count('fill')};});
    assert.ok(drawn.circle>50,'points must draw');assert.ok(drawn.line>0,'lines must draw');assert.ok(drawn.fill>0,'polygons must draw');
    await page.evaluate(()=>window.send('mapdesk:heatmap',{on:true}));await settle(1500);
    assert.ok((await dataLayers()).includes('data-L1-heatmap'),'heatmap layer');
    await page.evaluate(()=>{window.send('mapdesk:heatmap',{on:false});window.send('mapdesk:cluster',{on:true});});await settle(2500);
    assert.ok((await dataLayers()).includes('data-L1-clusters'),'cluster halo layer');
    await page.evaluate(()=>window.send('mapdesk:cluster',{on:false}));await settle(2000);
    assert.deepEqual(mapErrors,[],'heatmap and cluster must add without style errors');
    // Snapshot PNG must be a picture, not a transparent canvas.
    await page.evaluate(()=>{window.events=window.events.filter(e=>e.type!=='mapdesk:png');window.send('mapdesk:export-png',{});});
    await page.waitForFunction(()=>window.events.some(e=>e.type==='mapdesk:png'),null,{timeout:15000});
    const png=await page.evaluate(async()=>{const hit=window.events.find(e=>e.type==='mapdesk:png');const img=new Image();img.src=hit.dataUrl;await img.decode();const c=document.createElement('canvas');c.width=img.width;c.height=img.height;const g=c.getContext('2d');g.drawImage(img,0,0);const d=g.getImageData(0,0,c.width,c.height).data;const seen=new Set();let opaque=0,n=0;for(let i=0;i<d.length;i+=4*97){n++;seen.add(d[i]<<16|d[i+1]<<8|d[i+2]);if(d[i+3]>0)opaque++;}return {width:img.width,colors:seen.size,opaque:opaque/n};});
    assert.ok(png.colors>20&&png.opaque>0.9,'Snapshot PNG is blank: '+JSON.stringify(png));
    assert.equal(await page.evaluate(()=>window.events.filter(e=>e.type==='mapdesk:error').length),0,'no bridge errors');
    // The camera: an unchanged push must not re-tile, a resize must not be reported as a move, a real move is reported once.
    const quiet=await frame.evaluate(async state=>{let setData=0;for(const id of Object.keys(map.getStyle().sources)){const s=map.getSource(id);if(id.startsWith('data-')&&s.setData){const o=s.setData.bind(s);s.setData=d=>{setData++;return o(d);};}}window.__setData=()=>setData;return true;},null);
    await page.evaluate(state=>{window.events=window.events.filter(e=>e.type!=='mapdesk:viewport');const slim={...state,revision:3,viewport:null,selection:{layer_id:'L1',feature_ids:['w3'],bbox:null},layers:state.layers.map(({geojson,...rest})=>rest)};window.send('mapdesk:state',{state:slim});},state);
    await settle(1200);
    await frame.evaluate(()=>map.resize());await settle(800);
    const camera={setDataOnUnchangedPush:await frame.evaluate(()=>window.__setData()),viewportMsgsAfterPushAndResize:await page.evaluate(()=>window.events.filter(e=>e.type==='mapdesk:viewport').length)};
    await frame.evaluate(()=>map.panBy([120,0],{duration:0}));await settle(1500);
    camera.viewportMsgsAfterOnePan=await page.evaluate(()=>window.events.filter(e=>e.type==='mapdesk:viewport').length);
    assert.deepEqual(camera,{setDataOnUnchangedPush:0,viewportMsgsAfterPushAndResize:0,viewportMsgsAfterOnePan:1});
    // The keyless guest restyles the basemap's own border layers; they must be opaque and unblurred on every vector basemap.
    if(!mapbox){
      const borders=await frame.evaluate(()=>map.getStyle().layers.filter(l=>l.type==='line'&&l['source-layer']==='boundary').map(l=>({id:l.id,opacity:map.getPaintProperty(l.id,'line-opacity'),blur:map.getPaintProperty(l.id,'line-blur'),color:map.getPaintProperty(l.id,'line-color'),dash:map.getPaintProperty(l.id,'line-dasharray'),minzoom:l.minzoom||0})));
      if(process.env.MAPDESK_DRAW_DEBUG)console.log('BORDERS '+JSON.stringify(borders));
      if((process.env.MAPDESK_DRAW_BASEMAP||'dark')!=='satellite'){assert.ok(borders.length>=2,'basemap border layers found');for(const border of borders.filter(border=>border.id!=='mapdesk-county-borders')){assert.ok(border.opacity>=0.9,border.id+' opacity');assert.equal(border.blur,0,border.id+' blur');}assert.ok(borders.some(border=>!/country/.test(border.id)&&border.minzoom<=3),'state lines show from low zoom');assert.equal(borders.some(border=>border.id==='mapdesk-county-borders'),(process.env.MAPDESK_DRAW_BASEMAP||'dark')==='dark','county lines are added to the dark basemap only');}
    }
    // The free satellite carries roads, borders and place names as two reference overlays above the imagery.
    if(!mapbox&&process.env.MAPDESK_DRAW_BASEMAP==='satellite'){
      const rasters=await frame.evaluate(()=>map.getStyle().layers.filter(l=>l.type==='raster').map(l=>l.id));
      assert.deepEqual(rasters,['esri-imagery','esri-roads','esri-places']);
      assert.ok(await frame.evaluate(()=>map.isSourceLoaded('esri-roads')&&map.isSourceLoaded('esri-places')),'reference overlay tiles loaded');
    }
    if(process.env.MAPDESK_DRAW_VIEW){const [lon,lat,zoom]=process.env.MAPDESK_DRAW_VIEW.split(',').map(Number);await frame.evaluate(v=>map.jumpTo({center:[v[0],v[1]],zoom:v[2]}),[lon,lat,zoom]);await settle(6000);}
    if(process.env.MAPDESK_DRAW_SHOT)await page.locator('iframe').screenshot({path:process.env.MAPDESK_DRAW_SHOT});
    console.log(JSON.stringify({passed:true,guest:mapbox?'mapbox (live, isolated Edge, not Hermes Desktop)':'keyless',tests:['source and layers accepted','points, lines and polygons drawn','body kept across a pre-load slim state','heatmap','cluster','non-blank PNG','no re-tile on unchanged push','camera reported only on a real move'],drawn,png,camera},null,2));
  } finally { await browser.close(); }
})().catch(error=>{console.error(String(error.message).replace(/pk\.[A-Za-z0-9_.-]+/g,'[REDACTED]').replace(/access_token=[^\s&"']+/g,'access_token=[REDACTED]'));process.exitCode=1;});
