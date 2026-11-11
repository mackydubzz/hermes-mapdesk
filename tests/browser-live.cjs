// Opt-in live Mapbox verification. Token stays in process memory; no HAR/trace.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('./dev-tools.cjs')('playwright');
(async()=>{
 if(process.env.MAPDESK_LIVE_TEST!=='1') throw new Error('Set MAPDESK_LIVE_TEST=1 to allow a live Mapbox map load.');
 const token=execFileSync('python',['-c','from agent.secret_scope import get_secret; import sys; sys.stdout.write(get_secret("MAPDESK_MAPBOX_ACCESS_TOKEN") or "")'],{encoding:'utf8'}).trim();
 assert.ok(token.startsWith('pk.'),'A scoped public token is required');
 const clean=s=>String(s).split(token).join('[REDACTED]').replace(/access_token=[^\s&"']+/g,'access_token=[REDACTED]');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:700}});
  const failures=[],requests=[];
  page.on('response',r=>{const u=new URL(r.url());if(u.protocol.startsWith('http'))requests.push({host:u.hostname,path:u.pathname,http_status:r.status()});});
  page.on('requestfailed',r=>failures.push('Network '+new URL(r.url()).hostname+': '+(r.failure()?.errorText||'failed')));
  page.on('pageerror',e=>failures.push(clean(e.message)));
  page.on('console',m=>{if(m.type()==='error')failures.push(clean(m.text()));});
  const html=fs.readFileSync(path.join(__dirname,'../desktop/map.html'),'utf8');
  await page.setContent('<iframe id="map" sandbox="allow-scripts" referrerpolicy="no-referrer" style="width:1000px;height:650px;border:0"></iframe>');
  await page.evaluate(()=>{window.events=[];addEventListener('message',e=>{if(e.source===document.querySelector('iframe').contentWindow)window.events.push({type:e.data.type,version:e.data.version,channel:e.data.channel});});});
  await page.locator('iframe').evaluate((f,src)=>f.src=src,'data:text/html;charset=utf-8,'+encodeURIComponent(html));
  await page.waitForFunction(()=>events.some(e=>e.type==='mapdesk:ready'),null,{timeout:30000});
  await page.evaluate(token=>document.querySelector('iframe').contentWindow.postMessage({version:1,type:'mapdesk:init',channel:'mapdesk-live-test-channel',token,state:{revision:1,layers:[],viewport:{center:[-102.08,31.99],zoom:10},selection:{}}},'*'),token);
  const frame=page.frames().find(f=>f.parentFrame());
  let result;
  try{
   await page.waitForFunction(()=>events.some(e=>e.type==='mapdesk:loaded'),null,{timeout:45000});
   result=await frame.evaluate(()=>({loaded:loaded,map_loaded:map.loaded(),style_loaded:map.isStyleLoaded(),status_visible:status.style.display!=='none',canvas_width:map.getCanvas().width,canvas_height:map.getCanvas().height,rendered_basemap_features:map.queryRenderedFeatures().length}));
   assert.ok(result.loaded&&result.style_loaded&&!result.status_visible,'Actual basemap must load');
   assert.ok(result.rendered_basemap_features>0,'Actual basemap must contain rendered features');
   const dest=path.join(__dirname,'../artifacts/mapbox-isolated-live.png');
   await page.locator('iframe').screenshot({path:dest});
   console.log(JSON.stringify({environment:'isolated Edge, not Hermes Desktop',...result,screenshot:dest,errors:failures},null,2));
  }catch(e){
   console.log(JSON.stringify({environment:'isolated Edge, not Hermes Desktop',status:await frame.locator('#status').textContent(),diagnostics:await frame.evaluate(()=>({initialized:!!channel,loaded,map_created:!!map,mapbox_library:typeof mapboxgl,status_type:typeof status,style_loaded:map?.isStyleLoaded(),style_layers:map?.getStyle()?.layers?.length,canvas:[map?.getCanvas().width,map?.getCanvas().height]})),requests,events:await page.evaluate(()=>window.events),errors:failures},null,2));
   throw new Error(clean(e.message));
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
