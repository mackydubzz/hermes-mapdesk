const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = process.env.MAPDESK_PLAYWRIGHT_PATH ? require(process.env.MAPDESK_PLAYWRIGHT_PATH) : require('./dev-tools.cjs')('playwright');
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1000,height:650}});
    const html = fs.readFileSync(path.join(__dirname,'../desktop/map.html'),'utf8');
    await page.setContent('<html><body style="margin:0"><iframe title="MapDesk test" sandbox="allow-scripts" referrerpolicy="no-referrer" style="border:0;width:100vw;height:100vh"></iframe></body></html>');
    await page.evaluate(html => {
      window.events=[];
      const frame=document.querySelector('iframe');
      window.addEventListener('message',e=>{if(e.source===frame.contentWindow)window.events.push(e.data);});
      frame.src='data:text/html;charset=utf-8,'+encodeURIComponent(html);
    },html);
    await page.waitForFunction(()=>window.events.some(e=>e.type==='mapdesk:ready'),{timeout:45000});
    assert.ok(await page.evaluate(()=>window.events.every(e=>e.version===1)),'Bridge messages must be versioned');
    const frame=page.frames().find(f=>f!==page.mainFrame());
    const gl=await frame.evaluate(()=>({library:typeof mapboxgl,webgl:typeof mapboxgl!=='undefined'&&mapboxgl.supported(),origin:self.origin,preload:typeof window.electron}));
    assert.equal(gl.library,'object','Real pinned Mapbox GL JS must load inside sandbox');
    assert.equal(gl.origin,'null');
    assert.equal(gl.preload,'undefined');
    await page.evaluate(()=>document.querySelector('iframe').contentWindow.postMessage({version:1,type:'mapdesk:init',channel:'browser-test-channel-123456',token:'',state:{revision:0,layers:[],viewport:{center:[-102.08,31.99],zoom:10}}},'*'));
    await frame.getByRole('status').filter({hasText:'Mapbox setup required'}).waitFor();
    assert.equal(await frame.locator('canvas').count(),0,'No map initialized without user token');
    assert.equal(await frame.evaluate(()=>typeof originalFeatureId),'function','Selection must restore original typed IDs');
    assert.deepEqual(await frame.evaluate(()=>{const layer={geojson:{features:[{id:1},{id:'1'},{id:'001'}]}};return [0,1,2].map(i=>originalFeatureId(layer,i));}),[1,'1','001']);
    await page.setViewportSize({width:620,height:430});
    assert.equal(await frame.evaluate(()=>innerWidth),620);
    assert.equal(await frame.evaluate(()=>innerHeight),430);
    const out=path.join(__dirname,'../artifacts');fs.mkdirSync(out,{recursive:true});
    await page.screenshot({path:path.join(out,'sandbox-token-gate.png')});
    console.log(JSON.stringify({passed:true,tests:['pinned library loaded','opaque sandbox','no preload bridge','missing token setup gate','resize'],gl},null,2));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
