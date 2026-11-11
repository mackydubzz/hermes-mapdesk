import { host, atom, useValue, useQuery, SandboxedFrame, Button, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@hermes/plugin-sdk';
import { useState, useEffect, useRef, useMemo } from 'react';
import { jsx } from 'react/jsx-runtime';

const BORDER='1px solid var(--ui-stroke-secondary)';
const MUTED={color:'var(--ui-text-tertiary)',fontSize:11};
const ROW={display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'};
const FIELD={background:'transparent',color:'inherit',border:BORDER,borderRadius:4,padding:'5px 7px',minWidth:0};
function boundsForViewport(viewport,width=800,height=500){
 const world=512*2**viewport.zoom,clamp=(v,low,high)=>Math.max(low,Math.min(high,v)),round=v=>Math.round(v*1e4)/1e4;
 const radians=clamp(viewport.center[1],-85.05112878,85.05112878)*Math.PI/180,y=(1-Math.log(Math.tan(radians)+1/Math.cos(radians))/Math.PI)/2;
 const latitude=v=>Math.atan(Math.sinh(Math.PI*(1-2*clamp(v,0,1))))*180/Math.PI,half=width/2/world*360;
 return [clamp(viewport.center[0]-half,-180,180),latitude(y+height/2/world),clamp(viewport.center[0]+half,-180,180),latitude(y-height/2/world)].map(round);
}
function parseBounds(text){
 const parts=text.split(',').map(part=>part.trim());
 if(parts.length!==4||parts.some(part=>part===''))return null;
 const [west,south,east,north]=parts.map(Number);
 return [west,south,east,north].every(Number.isFinite)&&west>=-180&&east<=180&&south>=-90&&north<=90&&west<east&&south<north?[west,south,east,north]:null;
}
function arcgisName(url){
 const match=/\/([^\/?#]+)\/(?:Feature|Map)Server\/(\d+)\/?$/i.exec(url.trim());
 if(!match)return 'ArcGIS layer';
 let service=match[1];try{service=decodeURIComponent(service);}catch(_){}
 return `${service.replace(/_/g,' ')} · layer ${match[2]}`;
}
function button(label,onClick,disabled=false,extra){return jsx(Button,{type:'button',variant:'outline',size:'sm',onClick,disabled,...extra,children:label});}
function toggleButton(label,active,onClick,disabled){return button(active?label+' · on':label,onClick,disabled,{'aria-pressed':!!active,style:active?{borderColor:'var(--ui-accent)',color:'var(--ui-accent)'}:undefined});}
const NONE='mapdesk:none';
function choiceMenu(label,value,onChange,items,placeholder,style,disabled){
 const list=items||[];
 const currentKey=list.some(item=>String(item.value)===String(value))?String(value):placeholder!=null?NONE:String((list[0]||{}).value??NONE);
 return jsx(Select,{value:currentKey,onValueChange:next=>{if(next===NONE)return onChange(null);const hit=list.find(item=>String(item.value)===next);if(hit!=null)onChange(hit.value);},disabled,children:[
  jsx(SelectTrigger,{'aria-label':label,style,children:jsx(SelectValue,{})}),
  jsx(SelectContent,{children:[
   placeholder!=null?jsx(SelectItem,{value:NONE,children:placeholder},NONE):null,
   ...list.map(item=>jsx(SelectItem,{value:String(item.value),children:item.label},String(item.value)))
  ]})
 ]});
}
function fieldMenu(label,value,onChange,placeholder,options,style,disabled){
 return choiceMenu(label,value,next=>onChange(next==null?'':next),(options||[]).filter(option=>option&&option!==NONE).map(option=>({value:option,label:option})),placeholder,style,disabled);
}
function textInput(label,value,onChange){return jsx('input',{'aria-label':label,placeholder:label,value,onChange:e=>onChange(e.target.value),style:FIELD});}
function scopeNow(){return JSON.stringify([host.state.connectionId.get(),host.state.profile.get()]);}
function persistSetting(ctx,key,value){try{if(ctx.storage?.set)ctx.storage.set(key,value);else localStorage.setItem('hermes.plugin.mapdesk.'+key,JSON.stringify(value));}catch(_){}}
function SetupGuide({tokenStatus,renderer,layerCount,onCheck,clearOnSession,onClearOnSession,open,onOpen}){
 // One plain sentence per state. `renderer` is what the map frame last reported;
 // `tokenStatus` is what the saved setting says. Neither alone tells the user what they are looking at.
 const ready=renderer==='Map ready',failed=renderer==='Mapbox setup required'||renderer.startsWith('Map service unavailable');
 const basemap=tokenStatus==='checking'?'Basemap: reading your settings…'
  :tokenStatus==='failed'?'Basemap: MapDesk settings could not be read. Click Check setup.'
  :tokenStatus==='configured'
   ?(ready?'Basemap: Mapbox, loaded with your token.'
     :failed?'Basemap: a Mapbox token is saved, but the map did not load. Check the token, your Mapbox account and your connection, then click Check setup.'
     :'Basemap: Mapbox, loading…')
   :(ready?(tokenStatus==='rejected'
       ?'Basemap: free OpenFreeMap. The saved token is not a public token (it does not start with pk.), so it is not used.'
       :'Basemap: free OpenFreeMap. No token or account needed.')
     :failed?'Basemap: the free OpenFreeMap map did not load. Check your connection, then click Check setup.'
     :'Basemap: free OpenFreeMap, loading…');
 const layerLine=layerCount
  ?`Layers: ${layerCount} saved ${layerCount===1?'layer':'layers'}.`
  :'Layers: none yet. Choose a file under Add your data, drop one anywhere in this pane, or click Load sample data.';
 const heading=text=>jsx('h4',{style:{fontSize:12,margin:'12px 0 4px'},children:text});
 const link=(href,text)=>jsx('a',{href,target:'_blank',rel:'noopener noreferrer',children:text});
 // `open` is held by the workspace: Check setup rebuilds the pane around this panel, and it must
 // still be open afterwards so the result of the check is visible.
 return jsx('details',{'aria-label':'MapDesk setup',open:!!open,onToggle:e=>onOpen(e.currentTarget.open),style:{border:BORDER,borderRadius:6,padding:10,marginTop:10},children:[
  jsx('summary',{children:'Setup & help'}),
  heading('Status'),
  jsx('ul',{style:{paddingLeft:18,lineHeight:1.6,margin:'0 0 6px'},children:[
   jsx('li',{children:'Storage: connected. Layers, styling, selection and the map view are saved for this Hermes profile.'}),
   jsx('li',{children:basemap}),
   jsx('li',{children:layerLine})
  ]}),
  jsx('div',{style:ROW,children:[button('Check setup',onCheck),jsx('span',{style:MUTED,children:'Rereads your settings and reloads only the map. Your layers stay.'})]}),
  heading('Basemap'),
  jsx('p',{style:{...MUTED,margin:'0 0 6px'},children:'MapDesk needs no account. It uses the free OpenFreeMap basemap unless a Mapbox token is saved.'}),
  jsx('p',{style:{margin:'0 0 2px'},children:'To use Mapbox instead:'}),
  jsx('ol',{style:{paddingLeft:18,lineHeight:1.6,margin:'0 0 6px'},children:[
   jsx('li',{children:['Create a dedicated public token in your Mapbox account. It starts with pk. and needs the styles:read and fonts:read scopes. Never use a secret token (sk.). ',link('https://console.mapbox.com/account/access-tokens/','Get a Mapbox token'),' · ',link('https://docs.mapbox.com/accounts/guides/tokens/','Token help')]}),
   jsx('li',{children:['Paste it into Settings → Plugins → MapDesk, not into chat. ',button('Mapbox settings',()=>host.navigate('/settings?tab=plugins&agent=mapdesk'))]}),
   jsx('li',{children:'Come back here and click Check setup.'})
  ]}),
  jsx('p',{style:{...MUTED,margin:'0 0 6px'},children:'To go back to the free basemap, remove the token in the same place and click Check setup.'}),
  jsx('p',{style:{...MUTED,margin:'0 0 6px'},children:'Mapbox may bill map loads to your account, and each Check setup with a token is a new map load. Tokens restricted to specific URLs do not work here, because the map runs in an isolated frame that has no site address.'}),
  heading('Workspace'),
  jsx('label',{style:{...ROW,cursor:'pointer',margin:'0 0 6px'},children:[
   jsx('input',{type:'checkbox',checked:!!clearOnSession,onChange:e=>onClearOnSession(e.target.checked)}),
   jsx('span',{children:'Clear the map when I switch to a different chat session. The map is archived first and an Undo banner appears.'})
  ]}),
  jsx('p',{style:{...MUTED,margin:0},children:'The command palette entry Open MapDesk starts a blank map; the current map is archived to Saved maps first. Switching tabs and Refresh keep your work.'})
 ]});
}
function parseCsvPreview(text){
 const rows=[];let row=[],cell='',quoted=false,closed=false,started=false;
 const finish=()=>{if(started||cell||row.length){row.push(cell);rows.push(row);if(rows.length>5001)throw new Error('CSV exceeds 5,000 data rows. Split it into smaller files.');}row=[];cell='';closed=false;started=false;};
 text=text.replace(/^\uFEFF/,'');
 for(let i=0;i<text.length;i++){
  const c=text[i];
  if(quoted){if(c==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else cell+=c;continue;}
  if(c===','){row.push(cell);cell='';closed=false;started=true;continue;}
  if(c==='\r'||c==='\n'){if(c==='\r'&&text[i+1]==='\n')i++;finish();continue;}
  if(closed)throw new Error('Malformed CSV: unexpected text after a quoted field.');
  if(c==='"'&&cell===''){quoted=true;started=true;}else{cell+=c;started=true;}
 }
 if(quoted)throw new Error('Malformed CSV: a quoted field was not closed.');
 finish();
 const headers=rows.shift()||[];
 if(!headers.length||headers.some(h=>!h)||new Set(headers).size!==headers.length)throw new Error('CSV needs unique, nonempty column headers.');
 if(!rows.length)throw new Error('CSV has no data rows.');
 if(rows.some(r=>r.length!==headers.length))throw new Error('CSV rows must have the same number of fields as the header.');
 return {headers,rows};
}
function suggestColumn(headers,aliases){const matches=headers.filter(h=>aliases.includes(h.toLowerCase().trim()));return matches.length===1?matches[0]:'';}
function cameraForBounds(west,south,east,north,width=800,height=500){
 const mercator=lat=>{const radians=Math.max(-85.05112878,Math.min(85.05112878,lat))*Math.PI/180;return (1-Math.log(Math.tan(radians)+1/Math.cos(radians))/Math.PI)/2;};
 const top=mercator(north),bottom=mercator(south),spanX=(east-west)/360,spanY=bottom-top;
 const zoom=spanX===0&&spanY===0?13:Math.max(0,Math.min(16,Math.log2(Math.max(100,width-80)/(512*Math.max(spanX,1e-9))),Math.log2(Math.max(100,height-80)/(512*Math.max(spanY,1e-9)))));
 return {center:[(west+east)/2,Math.atan(Math.sinh(Math.PI*(1-top-bottom)))*180/Math.PI],zoom};
}
function viewportForBbox(bbox,width=800,height=500){return cameraForBounds(bbox[0],bbox[1],bbox[2],bbox[3],width,height);}
function boundsForGeojson(geojson){
 let west=Infinity,east=-Infinity,south=Infinity,north=-Infinity;
 function coordinates(value){
  if(!Array.isArray(value))return;
  if(typeof value[0]==='number'&&typeof value[1]==='number'&&Number.isFinite(value[0])&&Number.isFinite(value[1])){west=Math.min(west,value[0]);east=Math.max(east,value[0]);south=Math.min(south,value[1]);north=Math.max(north,value[1]);}
  else value.forEach(coordinates);
 }
 function geometry(g){if(g?.type==='GeometryCollection')g.geometries.forEach(geometry);else coordinates(g?.coordinates);}
 geojson.features.forEach(f=>geometry(f.geometry));
 if(!Number.isFinite(west))throw new Error('No geometry available to zoom to.');
 return [west,south,east,north];
}
// The guest now fits bounds itself (mapdesk:fit); viewportForGeojson stays as
// the tested helper for any local camera math (tests/import-helpers.cjs).
function viewportForGeojson(geojson,width=800,height=500){const [west,south,east,north]=boundsForGeojson(geojson);return cameraForBounds(west,south,east,north,width,height);}
function csvCell(value){const text=String(value??'');return /[",\r\n]/.test(text)?'"'+text.replace(/"/g,'""')+'"':text;}
function toBase64(buffer){const bytes=new Uint8Array(buffer);let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));return btoa(binary);}
function hashString(text){let hash=0;for(let i=0;i<text.length;i++)hash=(hash*31+text.charCodeAt(i))>>>0;return hash.toString(36);}
function downloadBlob(content,type,filename){const blob=content instanceof Blob?content:new Blob([content],{type});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=filename;link.click();URL.revokeObjectURL(url);}
function hasPointFeatures(geojson){
 const isPoint=geometry=>geometry?.type==='Point'||geometry?.type==='MultiPoint'||(geometry?.type==='GeometryCollection'&&(geometry.geometries||[]).some(part=>part?.type==='Point'||part?.type==='MultiPoint'));
 return (geojson?.features||[]).some(feature=>isPoint(feature.geometry));
}
function decodeBase64ToBlob(base64,mime){const binary=atob(base64);const bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);return new Blob([bytes],{type:mime});}
const EXPORT_MIMES={export_gpkg:'application/geopackage+sqlite3',export_shp:'application/zip',export_kmz:'application/vnd.google-earth.kmz'};
const BASEMAP_CHOICES=[{value:'dark',label:'Dark'},{value:'light',label:'Light'},{value:'streets',label:'Streets'},{value:'satellite',label:'Satellite'}];
function csvFromFeatures(features){
 const headers=[];
 for(const feature of features)for(const key of Object.keys(feature.properties||{}))if(!headers.includes(key))headers.push(key);
 const rows=features.map(feature=>headers.map(key=>{const value=feature.properties?.[key];return value==null?'':typeof value==='object'?JSON.stringify(value):String(value);}));
 return {headers,rows};
}
const IMPORT_ACTIONS={geojson:'import_geojson',csv:'import_csv',kml:'import_kml',gpx:'import_gpx',kmz:'import_kmz',xlsx:'import_xlsx',shp:'import_shp'};
// Catalog entries were count-verified against each live ArcGIS layer at release time.
const PUBLIC_CATALOG=[
 {label:'US Census TIGERweb · states and territories',url:'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0',note:'All 56 states and territories as polygons; leave Where empty.'},
 {label:'US Census TIGERweb · counties of one state',url:'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1',where:"STATE='48'",note:'254 counties of state FIPS 48. Change the STATE value in Where to another state FIPS code.'},
 {label:'HIFLD · electric substations',url:'https://services.arcgis.com/njFNhDsUCentVYJW/ArcGIS/rest/services/Substations/FeatureServer/0',note:'US electric power substations of 69 kV and above (HIFLD). About 2,200 features; a 1,000-feature import is a labeled sample.'}
];
const CLASS_METHODS=[{value:'auto',label:'Auto'},{value:'unique',label:'Unique'},{value:'quantile',label:'Quantile'},{value:'jenks',label:'Natural breaks'},{value:'equal',label:'Equal interval'},{value:'continuous',label:'Continuous'}];
const CLASS_COUNTS=[3,4,5,6,7].map(count=>({value:count,label:String(count)}));
const PALETTE_CHOICES=['classic','okabe','viridis','sunset'].map(palette=>({value:palette,label:palette}));
function FileImporter({onImport,isCurrent,busy,setBusy,rest,intake,onArcgisUrl}){
 const [draft,setDraft]=useState(null),[title,setTitle]=useState(''),[lat,setLat]=useState(''),[lon,setLon]=useState(''),[addr,setAddr]=useState(''),[problem,setProblem]=useState(''),[notice,setNotice]=useState(''),[pasteText,setPasteText]=useState(''),[pasteProblem,setPasteProblem]=useState(''),[geocoding,setGeocoding]=useState(null);
 const cancelGeocode=useRef(false);
 function takeTextDraft(kind,text,name){
  if(kind==='csv'){
   const preview=parseCsvPreview(text);
   setLat(suggestColumn(preview.headers,['latitude','lat']));setLon(suggestColumn(preview.headers,['longitude','lon','lng']));setAddr('');
   setDraft({kind:'csv',text,...preview});
  }else if(kind==='kml'||kind==='gpx'){setDraft({kind,text});}
  else{
   let geojson;try{geojson=JSON.parse(text.replace(/^\uFEFF/,''));}catch(_){throw new Error('This file is not valid JSON. Export a WGS84 GeoJSON FeatureCollection.');}
   if(geojson?.type!=='FeatureCollection'||!Array.isArray(geojson.features)||!geojson.features.length)throw new Error('Expected a GeoJSON FeatureCollection with at least one feature.');
   if(geojson.features.length>5000)throw new Error('GeoJSON exceeds 5,000 features. Split it into smaller files.');
   setDraft({kind:'geojson',geojson});
  }
  setTitle(name);
 }
 async function ingest(file){
  if(!file)return;
  setDraft(null);setProblem('');setNotice('');setPasteProblem('');setGeocoding(null);cancelGeocode.current=false;
  if(file.size>10*1024*1024){setProblem('File exceeds 10 MiB. Split it into smaller files.');return;}
  const ext=((/\.([^.]+)$/.exec(file.name)||[])[1]||'').toLowerCase();
  if(!['kml','gpx','geojson','json','csv','kmz','xlsx','zip'].includes(ext)){setProblem('Supported: .geojson .json .csv .kml .gpx .kmz .xlsx .zip (a zipped shapefile with .shp+.dbf).');return;}
  setBusy(true);
  try{
   if(ext==='kmz'||ext==='xlsx'||ext==='zip'){
    const base64=toBase64(await file.arrayBuffer());if(!isCurrent())return;
    setDraft({kind:ext==='kmz'?'kmz':ext==='xlsx'?'xlsx':'shp',base64});setTitle(file.name);
   }else{
    const text=await file.text();if(!isCurrent())return;
    takeTextDraft(ext==='csv'?'csv':ext==='kml'?'kml':ext==='gpx'?'gpx':'geojson',text,file.name);
   }
  }catch(e){if(isCurrent())setProblem(e.message||'File could not be read.');}
  finally{if(isCurrent())setBusy(false);}
 }
 async function choose(event){const file=event.target.files?.[0];event.target.value='';await ingest(file);}
 useEffect(()=>{if(intake)intake.current=ingest;});
 async function usePasted(){
  const text=pasteText.trim();
  if(!text){setPasteProblem('Paste GeoJSON, CSV text, or an ArcGIS FeatureServer/MapServer layer URL.');return;}
  if(/^https?:\/\//i.test(text)){
   if(!/\/(Feature|Map)Server\/\d+/i.test(text)){setPasteProblem('The URL must end in FeatureServer/<n> or MapServer/<n>.');return;}
   onArcgisUrl(text);setPasteText('');setPasteProblem('');return;
  }
  setBusy(true);
  try{
   if(text.startsWith('{')||text.startsWith('[')){
    let geojson;try{geojson=JSON.parse(pasteText.replace(/^\uFEFF/,''));}catch(_){throw new Error('That is not valid JSON. Paste a WGS84 GeoJSON FeatureCollection.');}
    if(geojson?.type!=='FeatureCollection'||!Array.isArray(geojson.features)||!geojson.features.length)throw new Error('Expected a GeoJSON FeatureCollection with at least one feature.');
    if(geojson.features.length>5000)throw new Error('GeoJSON exceeds 5,000 features. Split it into smaller files.');
    setDraft({kind:'geojson',geojson});setTitle('Pasted data');setPasteText('');setPasteProblem('');
   }else{
    const preview=parseCsvPreview(pasteText);
    setLat(suggestColumn(preview.headers,['latitude','lat']));setLon(suggestColumn(preview.headers,['longitude','lon','lng']));setAddr('');
    setDraft({kind:'csv',text:pasteText,...preview});setTitle('Pasted data');setPasteText('');setPasteProblem('');
   }
  }catch(e){setPasteProblem(e.message||'Could not use the pasted data.');}
  finally{if(isCurrent())setBusy(false);}
 }
 async function geocodeImport(){
  const addrIndex=draft.headers.indexOf(addr);
  if(addrIndex<0)throw new Error('Choose an address column first.');
  cancelGeocode.current=false;
  const entries=[];
  let skipped=0;
  for(const row of draft.rows){
   const address=String(row[addrIndex]??'').trim();
   if(address&&address.length<=300)entries.push({row,address});else skipped++;
  }
  if(!entries.length)throw new Error('No usable addresses in that column (max 300 characters each).');
  const results=[];
  for(let i=0;i<entries.length;i+=25){
   if(!isCurrent())return false;
   if(cancelGeocode.current){setProblem('Geocoding cancelled. Nothing was imported.');return false;}
   const batch=entries.slice(i,i+25);
   const response=await rest('/geocode',{method:'POST',body:{addresses:batch.map(entry=>entry.address)},timeoutMs:120000});
   if(!Array.isArray(response?.results)||response.results.length!==batch.length)throw new Error('The geocoding service returned an unexpected response.');
   results.push(...response.results);
   if(isCurrent())setGeocoding({done:Math.min(i+batch.length,entries.length),total:entries.length});
  }
  const headers=[...draft.headers,'mapdesk_lon','mapdesk_lat'];
  const kept=[];
  entries.forEach((entry,i)=>{const hit=results[i];if(hit&&Number.isFinite(hit.lon)&&Number.isFinite(hit.lat))kept.push([...entry.row,hit.lon,hit.lat]);else skipped++;});
  if(!kept.length)throw new Error(`No addresses could be located; ${skipped} rows skipped. Nothing was imported.`);
  const text=[headers,...kept].map(row=>row.map(csvCell).join(',')).join('\r\n');
  const result=await onImport('import_csv',{name:title,text,latitude_column:'mapdesk_lat',longitude_column:'mapdesk_lon'});
  if(isCurrent()&&result){setNotice(`Imported ${title}${result.colorBy?` · colored by ${result.colorBy}`:''} · geocoded ${kept.length.toLocaleString()}, skipped ${skipped.toLocaleString()}`);setDraft(null);}
  return true;
 }
 const csvReady=draft?.kind==='csv'&&((lat&&lon&&lat!==lon)||(addr&&addr!==lat&&addr!==lon));
 async function submit(){
  if(!draft||busy||!title.trim())return;
  if(draft.kind==='csv'&&!csvReady)return;
  setBusy(true);setProblem('');setNotice('');
  try{
   if(draft.kind==='csv'&&!(lat&&lon&&lat!==lon)){await geocodeImport();}
   else{
    const action=IMPORT_ACTIONS[draft.kind];
    const args={name:title};
    if(draft.kind==='geojson')args.geojson=draft.geojson;
    else if(draft.kind==='csv'){args.text=draft.text;args.latitude_column=lat;args.longitude_column=lon;}
    else if(draft.kind==='kml'||draft.kind==='gpx')args.text=draft.text;
    else args.base64=draft.base64;
    const result=await onImport(action,args);
    if(isCurrent()&&result){setNotice(`Imported ${title}${result.colorBy?` · colored by ${result.colorBy}`:''}`);setDraft(null);}
   }
  }catch(e){if(isCurrent())setProblem(e.message||'Import failed. Nothing was confirmed saved.');}
  finally{if(isCurrent()){setBusy(false);setGeocoding(null);}}
 }
 const chooser=(label,value,set)=>jsx('label',{style:{display:'block',marginTop:8},children:[label,fieldMenu(label,value,set,'Choose a column',draft?.headers||[],{width:'100%',marginTop:3},busy)]});
 // Each entry is a function: only the one for this draft's kind may run. As a plain object every
 // template was evaluated for every draft, so a GeoJSON draft read draft.rows.length (and a CSV
 // draft read draft.geojson.features) and the whole pane crashed as soon as a file was chosen.
 const reviewCopy=draft?({
  geojson:()=>`${draft.geojson.features.length} features. Geometry and coordinates will be validated before saving.`,
  csv:()=>`${draft.rows.length} rows · ${draft.headers.length} columns. Confirm the coordinate columns, or geocode from an address column; other values stay as text.`,
  kml:()=>`${draft.text.length.toLocaleString()} characters of KML. Features are validated on import.`,
  gpx:()=>`${draft.text.length.toLocaleString()} characters of GPX. Features are validated on import.`,
  kmz:()=>'KMZ (zipped KML). Features are validated on import.',
  xlsx:()=>'XLSX workbook. It must have explicit latitude and longitude columns (e.g. latitude/longitude, lat/lon, lng).',
  shp:()=>'Zipped shapefile. It must contain a .shp and a .dbf file (plus an optional .prj); features are validated on import.'
 }[draft.kind]?.()||''):'';
 return jsx('section',{'aria-label':'Import your data',style:{borderBottom:BORDER,paddingBottom:12,marginBottom:12},children:[
  jsx('h3',{style:{fontSize:13,margin:'0 0 7px'},children:'Add your data'}),
  jsx('p',{style:MUTED,children:'GeoJSON, CSV, KML, GPX, KMZ, XLSX or a zipped shapefile (.shp+.dbf) · WGS84 longitude/latitude · up to 5,000 features and 10 MiB. Drop a file anywhere in this pane, or paste below. CSV rows without coordinates can be geocoded from an address column.'}),
  jsx('input',{type:'file',accept:'.geojson,.json,.csv,.kml,.gpx,.kmz,.xlsx,.zip','aria-label':'Choose a data file',disabled:busy,onChange:choose,style:{width:'100%'}}),
  problem&&jsx('p',{role:'alert',style:{overflowWrap:'anywhere'},children:problem}),
  notice&&jsx('p',{role:'status',children:notice}),
  jsx('details',{style:{marginTop:8},children:[
   jsx('summary',{children:'Paste data'}),
   jsx('p',{style:MUTED,children:'GeoJSON, CSV text, or an ArcGIS layer URL (https://…/FeatureServer/0 or MapServer/0).'}),
   jsx('textarea',{'aria-label':'Paste GeoJSON, CSV text or an ArcGIS layer URL',placeholder:'Paste GeoJSON, CSV or an ArcGIS FeatureServer/MapServer layer URL',value:pasteText,onChange:e=>setPasteText(e.target.value),style:{...FIELD,width:'100%',minHeight:64,marginTop:6,resize:'vertical',fontFamily:'inherit'}}),
   jsx('div',{style:{...ROW,marginTop:4},children:[button(busy?'Working…':'Use pasted data',usePasted,busy||!pasteText.trim())]}),
   pasteProblem&&jsx('p',{role:'alert',children:pasteProblem})
  ]}),
  draft&&jsx('div',{style:{border:BORDER,borderRadius:6,padding:8,marginTop:10},children:[
   jsx('h4',{style:{margin:'0 0 8px'},children:'Review import'}),textInput('Dataset name',title,setTitle),
   jsx('p',{style:MUTED,children:reviewCopy}),
   draft.kind==='csv'&&jsx('div',{children:[
    chooser('Latitude column',lat,setLat),chooser('Longitude column',lon,setLon),
    (!lat||!lon)&&jsx('div',{children:[chooser('Address column (geocodes rows without coordinates)',addr,setAddr),jsx('p',{style:MUTED,children:'No latitude/longitude columns were detected. Pick an address column to geocode rows online in batches of 25; rows that cannot be located are skipped.'})]}),
    lat&&lat===lon&&jsx('p',{role:'alert',children:'Latitude and longitude must use different columns.'}),
    jsx('details',{children:[jsx('summary',{children:'Preview first 3 rows / 6 columns'}),jsx('div',{style:{overflowX:'auto'},children:jsx('table',{style:{fontSize:10,borderCollapse:'collapse'},children:[jsx('thead',{children:jsx('tr',{children:draft.headers.slice(0,6).map(h=>jsx('th',{scope:'col',style:{textAlign:'left',padding:4,borderBottom:BORDER},children:h},h))})}),jsx('tbody',{children:draft.rows.slice(0,3).map((r,i)=>jsx('tr',{children:r.slice(0,6).map((v,j)=>jsx('td',{style:{padding:4,borderBottom:BORDER},children:v},j))},i))})]})})]})
   ]}),
   geocoding&&jsx('div',{style:{...ROW,marginTop:6},children:[jsx('span',{role:'status',children:`Geocoding ${geocoding.done}/${geocoding.total}…`}),button('Cancel geocoding',()=>{cancelGeocode.current=true;})]}),
   jsx('p',{style:MUTED,children:'Nothing is added until you confirm. Existing layers are kept.'}),
   jsx('div',{style:ROW,children:[button(busy?'Importing…':'Import dataset',submit,busy||!title.trim()||(draft.kind==='csv'&&!csvReady)),button('Cancel import',()=>{setDraft(null);setProblem('');setGeocoding(null);cancelGeocode.current=true;},busy)]})
  ]})
 ]});
}
function tableFields(layer){
 const skip=/^(fid|objectid|phone|website|address|zipcode|notes|descriptio|naics|naics_desc|method|source|date|lines|sub_1|sub_2|country)$/i;
 const fields=layer.fields||[],picked=[];
 const push=field=>{if(field&&fields.includes(field)&&!picked.includes(field))picked.push(field);};
 push(fields.find(field=>/name$/i.test(field))||fields.find(field=>/name/i.test(field)));
 push(layer.style?.color_by);
 for(const field of fields){if(picked.length>=5)break;if(!skip.test(field))push(field);}
 return picked.length?picked:fields.slice(0,5);
}
const STYLE_FIELDS=new WeakMap(); // geojson object -> ordered fields; a layer body never changes after import
function styleFields(layer){
 const features=layer.geojson?.features||[],fields=layer.fields||[];
 const key=layer.geojson&&fields.join('\u0000'),hit=layer.geojson&&STYLE_FIELDS.get(layer.geojson);
 if(hit&&hit.key===key)return hit.fields;
 // Count distinct values once per field (this used to run inside the sort comparator on every render).
 const counts=new Map();
 for(const field of fields){const seen=new Set();for(const feature of features)seen.add(String(feature.properties?.[field]??''));counts.set(field,seen.size);}
 const rank=field=>{const n=counts.get(field);return n>1&&n<=24?0:1;};
 const ordered=[...fields].sort((a,b)=>rank(a)-rank(b)||counts.get(a)-counts.get(b)||a.localeCompare(b));
 if(layer.geojson)STYLE_FIELDS.set(layer.geojson,{key,fields:ordered});
 return ordered;
}
function cellText(value){return value==null?'':String(value);}
function sampleLayers(){
 // Synthetic demonstration data, generated here: invented sites, routes and areas on a grid.
 // Not real wells, parcels or roads, and labeled as synthetic wherever it is shown.
 let seed=7;const rnd=()=>(seed=(seed*1664525+1013904223)>>>0)/4294967296;
 const west=-102.35,south=31.75,districts=['South','Central','North'],statuses=['Active','Active','Active','Idle','Planned'];
 const areas=[],sites=[],routes=[];
 for(let r=0;r<3;r++)for(let c=0;c<4;c++){const x=west+c*0.2,y=south+r*0.16;areas.push({type:'Feature',properties:{area:districts[r]+' '+(c+1),district:districts[r],acres:Math.round(4000+rnd()*9000)},geometry:{type:'Polygon',coordinates:[[[x,y],[x+0.19,y],[x+0.19,y+0.15],[x,y+0.15],[x,y]]]}});}
 for(let i=0;i<5;i++){const y=south+0.05+i*0.09,line=[];for(let k=0;k<=8;k++)line.push([+(west+k*0.0985).toFixed(5),+(y+Math.sin(k+i)*0.02).toFixed(5)]);routes.push({type:'Feature',properties:{route:'R-'+(i+1),kind:i%2?'Gathering':'Trunk',miles:Math.round(40+rnd()*20)},geometry:{type:'LineString',coordinates:line}});}
 for(let i=0;i<180;i++)sites.push({type:'Feature',properties:{site:'S-'+String(i+1).padStart(3,'0'),status:statuses[Math.floor(rnd()*statuses.length)],depth_ft:Math.round(5000+rnd()*7000)},geometry:{type:'Point',coordinates:[+(west+rnd()*0.79).toFixed(5),+(south+rnd()*0.47).toFixed(5)]}});
 const collection=features=>({type:'FeatureCollection',features});
 return [{name:'Sample areas (synthetic)',geojson:collection(areas)},{name:'Sample routes (synthetic)',geojson:collection(routes)},{name:'Sample sites (synthetic)',geojson:collection(sites)}];
}
function viewKey(view){return view&&Array.isArray(view.center)?Number(view.center[0]).toFixed(6)+','+Number(view.center[1]).toFixed(6)+','+Number(view.zoom).toFixed(4):'';}
const MAP_INDEX='saved-maps.json';
function sessionId(){try{return String(host.state.activeSessionId?.get?.()||'');}catch(_){return '';}}
function mapFingerprint(state){return (state?.layers||[]).map(layer=>[layer.id,layer.name,layer.feature_count,layer.style?.color_by||''].join('|')).join(';')+'::'+(state?.name||'');}
async function readSavedMaps(ctx){
 if(!ctx.files?.readText)return [];
 const result=await ctx.files.readText('data',MAP_INDEX);
 if(!result?.ok)return [];
 try{const parsed=JSON.parse(result.text);return Array.isArray(parsed)?parsed:[];}catch(_){return [];}
}
async function saveMapSnapshot(ctx,state,reason){
 if(!ctx.files?.writeText||!state?.layers?.length)return null;
 const fingerprint=mapFingerprint(state);
 const index=await readSavedMaps(ctx);
 const existing=index.find(item=>item.fingerprint===fingerprint);
 if(existing)return existing;
 const id='m'+Date.now().toString(36)+Math.random().toString(36).slice(2,8);
 const saved_at=new Date().toISOString();
 const record={id,name:state.name||'Untitled map',session_id:sessionId(),saved_at,reason,fingerprint,viewport:state.viewport,layers:state.layers};
 const body=JSON.stringify(record);
 if(body.length>3500000){host.notify({kind:'error',message:'This map is too large to archive (over 3.5 MB).'});return null;}
 const written=await ctx.files.writeText('data','saved-map-'+id+'.json',body);
 if(!written?.ok){host.notify({kind:'error',message:`Could not archive this map. ${written?.message||''}`.slice(0,240)});return null;}
 const summary={id,name:record.name,session_id:record.session_id,saved_at,reason,fingerprint,layer_count:state.layers.length,feature_count:state.layers.reduce((n,layer)=>n+(layer.feature_count||0),0)};
 const next=[summary,...index.filter(item=>item.fingerprint!==fingerprint)].slice(0,80);
 const indexed=await ctx.files.writeText('data',MAP_INDEX,JSON.stringify(next));
 return indexed?.ok?summary:null;
}
function matchingRows(layer,needle){
 const fields=tableFields(layer),features=layer?.geojson?.features||[];
 const q=needle.trim().toLowerCase();
 return q?features.filter(feature=>fields.some(field=>cellText(feature.properties?.[field]).toLowerCase().includes(q))):features;
}
function shownState(data,layerId,needle){
 if(!data||!layerId||!(needle||'').trim())return data;
 const active=data.layers.find(layer=>layer.id===layerId);
 if(!active||!active.geojson)return data;
 const features=matchingRows(active,needle);
 return {...data,layers:data.layers.map(layer=>layer.id!==layerId?layer:{...layer,feature_count:features.length,geojson:{...layer.geojson,features},client_revision:'f'+hashString(layerId+'|'+needle.trim().toLowerCase())})};
}
function downloadGeojson(layer,features){
 downloadBlob(JSON.stringify({type:'FeatureCollection',features}),'application/geo+json',(layer.name||'mapdesk').replace(/[^\w.-]+/g,'_').slice(0,80)+'.geojson');
}
function AttributeTable({layer,layers,query,onQuery,onClearFilter,selectedIds,onSelect,onSelectMany,onSpatial,busy,panelHeight}){
 const [sort,setSort]=useState(null),[page,setPage]=useState(0),[target,setTarget]=useState(null),[spatialOp,setSpatialOp]=useState('intersects');
 const filterRef=useRef(null);
 const fields=tableFields(layer),features=layer.geojson?.features||[];
 let rows=matchingRows(layer,query);
 if(sort)rows=[...rows].sort((a,b)=>{const av=a.properties?.[sort.field],bv=b.properties?.[sort.field],an=Number(av),bn=Number(bv);const cmp=Number.isFinite(an)&&Number.isFinite(bn)?an-bn:cellText(av).localeCompare(cellText(bv));return sort.dir*cmp;});
 const pageSize=Math.max(8,Math.floor(((panelHeight||200)-110)/25)),pages=Math.max(1,Math.ceil(rows.length/pageSize)),safePage=Math.min(page,pages-1),view=rows.slice(safePage*pageSize,safePage*pageSize+pageSize);
 const numeric=fields.find(field=>features.filter(feature=>Number.isFinite(Number(feature.properties?.[field]))).length>=Math.min(5,features.length));
 const nums=numeric?rows.map(feature=>Number(feature.properties?.[numeric])).filter(Number.isFinite):[];
 const summary=nums.length?`${numeric} sum ${nums.reduce((a,b)=>a+b,0).toLocaleString(undefined,{maximumFractionDigits:1})} · min ${Math.min(...nums).toLocaleString()} · max ${Math.max(...nums).toLocaleString()}`:'';
 const selected=new Set((selectedIds||[]).map(String));
 const allIds=features.map(feature=>feature.id).filter(id=>typeof id==='string'||Number.isSafeInteger(id)).slice(0,5000);
 const ids=rows.map(feature=>feature.id).filter(id=>typeof id==='string'||Number.isSafeInteger(id)).slice(0,5000);
 const complement=allIds.filter(id=>!selected.has(String(id)));
 const targets=layers.filter(item=>item.id!==layer.id);
 return jsx('div',{style:{display:'flex',flexDirection:'column',minHeight:0,flex:1},onKeyDown:e=>{ // '/' focuses the filter box unless the user is already typing
  if(e.key!=='/'||e.ctrlKey||e.metaKey||e.altKey)return;
  const tag=(e.target?.tagName||'').toLowerCase();
  if(tag==='input'||tag==='textarea'||e.target?.isContentEditable)return;
  e.preventDefault();
  try{filterRef.current?.focus();}catch(_){}
 },children:[
  jsx('div',{style:{...ROW,justifyContent:'space-between'},children:[
   jsx('strong',{children:'Attributes'}),
   jsx('span',{style:MUTED,children:`${rows.length.toLocaleString()} of ${features.length.toLocaleString()} on the map${summary?' · '+summary:''}`})
  ]}),
  jsx('div',{style:{...ROW,marginTop:4,alignItems:'flex-start'},children:[
   jsx('input',{ref:filterRef,'aria-label':`Filter ${layer.name}`,title:'Filters this layer only — the table and the map draw. Stored features are unchanged.',placeholder:'Filter rows and map',value:query,onChange:e=>{onQuery(e.target.value);setPage(0);},onKeyDown:e=>{if(e.key==='Escape'){onClearFilter();setPage(0);try{filterRef.current?.blur();}catch(_){}}},style:{...FIELD,flex:1,minWidth:120}}),
   button('Clear',()=>{onClearFilter();setPage(0);},!query.trim()),
   button('Select filtered',()=>onSelectMany(ids),!ids.length),
   button('Invert',()=>onSelectMany(complement),!allIds.length),
   button('Export GeoJSON',()=>downloadGeojson(layer,rows),!rows.length),
   jsx('details',{children:[jsx('summary',{style:{cursor:'pointer'},children:'By location'}),jsx('div',{style:{...ROW,marginTop:4},children:[
    choiceMenu(`Target layer for ${layer.name}`,target,next=>setTarget(next),targets.map(item=>({value:item.id,label:item.name})),'Target layer',{width:150,minWidth:0},busy),
    choiceMenu(`Spatial operator for ${layer.name}`,spatialOp,next=>setSpatialOp(next||'intersects'),[{value:'intersects',label:'intersects'},{value:'within',label:'within'},{value:'contains',label:'contains'}],null,{width:110,minWidth:0},busy),
    button('Apply',()=>{if(target&&spatialOp)onSpatial(spatialOp,target);},busy||!target)
   ]})]})
  ]}),
  jsx('div',{style:{flex:1,minHeight:0,overflowX:'auto',marginTop:4},children:jsx('table',{style:{borderCollapse:'collapse',width:'100%'},children:[
   jsx('thead',{children:jsx('tr',{children:fields.map(field=>jsx('th',{scope:'col',onClick:()=>setSort(prev=>({field,dir:prev?.field===field?prev.dir*-1:1})),style:{textAlign:'left',fontWeight:600,padding:'3px 6px',borderBottom:BORDER,cursor:'pointer',whiteSpace:'nowrap'},children:field+(sort?.field===field?(sort.dir>0?' ↑':' ↓'):'')},field))})}),
   jsx('tbody',{children:view.map(feature=>jsx('tr',{onClick:()=>feature.id!=null&&onSelect(feature.id),style:{cursor:'pointer',background:selected.has(String(feature.id))?'color-mix(in srgb, var(--ui-accent) 18%, transparent)':'transparent'},children:fields.map(field=>jsx('td',{style:{padding:'3px 6px',borderBottom:BORDER,whiteSpace:'nowrap',maxWidth:140,overflow:'hidden',textOverflow:'ellipsis'},children:cellText(feature.properties?.[field])},field))},String(feature.id)))})
  ]})}),
  pages>1&&jsx('div',{style:{...ROW,marginTop:4},children:[button('Prev',()=>setPage(p=>Math.max(0,p-1)),safePage===0),jsx('span',{style:MUTED,children:`${safePage+1} / ${pages}`}),button('Next',()=>setPage(p=>Math.min(pages-1,p+1)),safePage>=pages-1)]})
 ]});
}
function MapDesk({ctx,lifecycle}){
 const cycle=useValue(lifecycle.cycle);
 const scope=JSON.stringify([useValue(host.state.connectionId),useValue(host.state.profile)]);
 if(cycle.opening)return jsx('p',{role:'status',style:{padding:20},children:'Starting a blank map…'});
 return jsx(Workspace,{ctx,scope,lifecycle,epoch:cycle.epoch},JSON.stringify([scope,cycle.epoch]));
}
function Workspace({ctx,scope,lifecycle,epoch}){
 const frame=useRef(null),channel=useRef(crypto.randomUUID()),initialized=useRef(false),live=useRef(true),latest=useRef(null),modeRef=useRef('select'),modeSent=useRef(null),dropDepth=useRef(0),renameLayers=useRef({}),renameMaps=useRef({}),intake=useRef(null),attrHeight=useRef(200),basemapRef=useRef('dark'),styleSent=useRef(null),terrainRef=useRef(false),terrainSent=useRef(null),heatmapRef=useRef(false),clusterRef=useRef(false),pointLayers=useRef(new Set()),bodyLoads=useRef(new Map()),sentBodies=useRef(new Map()),liveView=useRef(null),viewTimer=useRef(null),writtenView=useRef(''),ownViews=useRef([]),seenView=useRef(null),externalView=useRef(false),lastLocal=useRef(0);
 const [ready,setReady]=useState(false),[token,setToken]=useState(''),[renderer,setRenderer]=useState('Waiting for renderer');
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[name,setName]=useState('');
 const [tokenStatus,setTokenStatus]=useState('checking'),[frameEpoch,setFrameEpoch]=useState(0),[guestEpoch,setGuestEpoch]=useState(0),refreshBootstrap=useRef(()=>{});
 const [url,setUrl]=useState(''),[where,setWhere]=useState(''),[bounds,setBounds]=useState(''),[sourceName,setSourceName]=useState(''),[sourceNotice,setSourceNotice]=useState(''),[removing,setRemoving]=useState(null),[savedMaps,setSavedMaps]=useState([]),[filters,setFilters]=useState({}),[mode,setModeState]=useState('select'),[dropping,setDropping]=useState(false),[arcOpen,setArcOpen]=useState(false),[deletingMap,setDeletingMap]=useState(null),[attrPanelHeight,setAttrPanelHeight]=useState(200),[confirmClear,setConfirmClear]=useState(false),[clearOnSession,setClearOnSessionState]=useState(true),[basemap,setBasemapState]=useState('dark'),[terrain,setTerrainState]=useState(false),[heatmap,setHeatmapState]=useState(false),[cluster,setClusterState]=useState(false),[exporting,setExporting]=useState(null),[exportingWorkspace,setExportingWorkspace]=useState(false),[savingMap,setSavingMap]=useState(false),[saveNotice,setSaveNotice]=useState('');
 const clearOnSessionRef=useRef(true),lastSessionRef=useRef(null),busyRef=useRef(false),framedRef=useRef(null);
 const activeSession=useValue(host.state.activeSessionId);
 const [bodies,setBodies]=useState({}),[bodyErrors,setBodyErrors]=useState({}),[pushFilters,setPushFilters]=useState({}),[setupOpen,setSetupOpen]=useState(false);
 const summary=useQuery({queryKey:[ctx.source,scope,'mapdesk-summary',epoch],queryFn:()=>ctx.rest('/summary'),refetchInterval:5000,retry:1});
 // Cache key MUST vary with the guest kind: /renderer serves mapbox or keyless
 // by token presence, and an infinite staleTime would pin a stale guest forever.
 const shell=useQuery({queryKey:[ctx.source,scope,'mapdesk-renderer-v11',tokenStatus==='configured'?'mapbox':'keyless'],queryFn:()=>ctx.rest('/renderer'),enabled:tokenStatus!=='checking',staleTime:Infinity,retry:1});
 const selected=useQuery({queryKey:[ctx.source,scope,'mapdesk-selection',epoch],queryFn:()=>ctx.rest('/selection'),refetchInterval:5000,retry:1});
 latest.current=summary.data;
 const layers=summary.data?.layers||[];
 const activeBase=layers.find(layer=>layer.id===summary.data?.selection?.layer_id)||layers.at(-1)||null;
 // Layer bodies: every VISIBLE layer (and the active one) is fetched once per
 // data_revision. Before this, only the active layer ever reached the map, so
 // a reopened multi-layer workspace drew a single layer.
 useEffect(()=>{
  for(const layer of layers){
   if(layer.id==null||!Number.isFinite(layer.data_revision)||!(layer.visible||layer.id===activeBase?.id))continue;
   if(bodies[layer.id]?.revision===layer.data_revision||bodyLoads.current.get(layer.id)===layer.data_revision)continue;
   const id=layer.id,revision=layer.data_revision;
   bodyLoads.current.set(id,revision);
   ctx.rest('/layer/'+encodeURIComponent(id)).then(data=>{
    if(!live.current)return;
    if(!data?.geojson||!Array.isArray(data.geojson.features))throw new Error('Layer data could not be read.');
    if(hasPointFeatures(data.geojson))pointLayers.current.add(id);else pointLayers.current.delete(id);
    setBodies(prev=>({...prev,[id]:{revision,geojson:data.geojson}}));
    setBodyErrors(prev=>{if(!prev[id])return prev;const next={...prev};delete next[id];return next;});
   }).catch(()=>{if(live.current)setBodyErrors(prev=>({...prev,[id]:true}));}).finally(()=>{if(bodyLoads.current.get(id)===revision)bodyLoads.current.delete(id);});
  }
  const known=new Set(layers.map(layer=>layer.id));
  if(Object.keys(bodies).some(id=>!known.has(id)))setBodies(prev=>Object.fromEntries(Object.entries(prev).filter(([id])=>known.has(id))));
 },[summary.data,activeBase?.id,bodies]);
 const state=useMemo(()=>{
  if(!summary.data)return null;
  return {...summary.data,layers:layers.map(layer=>{
   const body=bodies[layer.id];
   if(body&&body.revision===layer.data_revision)return {...layer,geojson:body.geojson};
   if(layer.geojson){const slim={...layer};delete slim.geojson;return slim;}
   return layer;
  })};
 },[summary.data,bodies]);
 const archived=useValue(lifecycle.lastArchive);
 const current=()=>live.current&&scopeNow()===scope&&!lifecycle.cycle.get().opening&&lifecycle.cycle.get().epoch===epoch;
 const post=(type,payload={})=>frame.current?.contentWindow?.postMessage({version:1,type,channel:channel.current,...payload},'*');
 const setMode=next=>{if(next===modeRef.current)return;modeRef.current=next;setModeState(next);post('mapdesk:mode',{mode:next});};
 const setBasemapValue=next=>{
  if(!next||next===basemapRef.current)return;
  basemapRef.current=next;setBasemapState(next);
  persistSetting(ctx,'basemap',next);
  post('mapdesk:style',{basemap:next});
  styleSent.current=next;
 };
 const toggleTerrain=()=>{
  terrainRef.current=!terrainRef.current;setTerrainState(terrainRef.current);
  persistSetting(ctx,'terrain',terrainRef.current);
  post('mapdesk:terrain',{on:terrainRef.current});
  terrainSent.current=terrainRef.current;
 };
 const toggleHeatmap=()=>{heatmapRef.current=!heatmapRef.current;setHeatmapState(heatmapRef.current);post('mapdesk:heatmap',{on:heatmapRef.current});};
 const toggleCluster=()=>{clusterRef.current=!clusterRef.current;setClusterState(clusterRef.current);post('mapdesk:cluster',{on:clusterRef.current});};
 const fitSelection=()=>{
  const active=state.layers.find(layer=>layer.id===state.selection.layer_id);
  const wanted=new Set((state.selection.feature_ids||[]).map(String));
  if(!active||!active.geojson||!wanted.size)return;
  const features=active.geojson.features.filter(feature=>wanted.has(String(feature.id)));
  if(!features.length)return;
  try{post('mapdesk:fit',{bbox:boundsForGeojson({type:'FeatureCollection',features})});}catch(_){}
 };
 useEffect(()=>{ // remembered preferences (plugin-scoped storage, sync and async shapes)
  let dead=false;
  const restoreValue=(key,apply)=>{
   try{
    let value;
    try{value=ctx.storage?ctx.storage.get(key):JSON.parse(localStorage.getItem('hermes.plugin.mapdesk.'+key));}catch(_){}
    const use=v=>{if(!dead)apply(v);};
    if(value&&typeof value.then==='function')value.then(use).catch(()=>{});
    else use(typeof value==='object'&&value&&'value'in value?value.value:value);
   }catch(_){}
  };
  restoreValue('attributesHeight',v=>{if(typeof v==='number'&&v>=96&&v<=560){attrHeight.current=v;setAttrPanelHeight(v);}});
  restoreValue('clearOnSession',v=>{if(typeof v==='boolean'){clearOnSessionRef.current=v;setClearOnSessionState(v);}});
  restoreValue('lastSessionId',v=>{if(typeof v==='string'&&v)lastSessionRef.current=v;});
  restoreValue('basemap',v=>{if(BASEMAP_CHOICES.some(choice=>choice.value===v)){basemapRef.current=v;setBasemapState(v);}});
  restoreValue('terrain',v=>{if(typeof v==='boolean'){terrainRef.current=v;setTerrainState(v);}});
  return()=>{dead=true;};
 },[ctx]);
 const startAttrDrag=event=>{ // imperative drag between the map and the attributes panel
  if(event.button!==0&&event.pointerType==='mouse')return;
  event.preventDefault();
  const startY=event.clientY,startH=attrHeight.current;
  const move=ev=>{const next=Math.max(96,Math.min(560,startH-(ev.clientY-startY)));attrHeight.current=next;setAttrPanelHeight(next);};
  const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);try{if(ctx.storage?.set)ctx.storage.set('attributesHeight',attrHeight.current);else localStorage.setItem('hermes.plugin.mapdesk.attributesHeight',String(attrHeight.current));}catch(_){}};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);
 };
 async function command(action,args){
  if(!current())return;
  setError('');
  let pending;
  try{
   pending=ctx.rest('/command',{method:'POST',body:{action,args},timeoutMs:60000});
   lifecycle.pending.add(pending);
   const result=await pending;
   lastLocal.current=Date.now();
   if(current()){await summary.refetch();await selected.refetch();}
   return result;
  }catch(e){if(current())setError(e.message||'Workspace request failed');throw e;}
  finally{lifecycle.pending.delete(pending);lastLocal.current=Date.now();}
 }
 // The camera is the guest's: its moves are saved quietly (debounced, no
 // refetch, no state push back). Before this every pan cost a write, two
 // summary and two selection fetches and a full re-tile, and dragging the
 // attributes sash queued dozens of them.
 const writeViewport=()=>{
  viewTimer.current=null;
  const view=liveView.current,key=viewKey(view);
  if(!key||key===writtenView.current)return;
  writtenView.current=key;
  ownViews.current.push(key);if(ownViews.current.length>32)ownViews.current.shift();
  const pending=ctx.rest('/command',{method:'POST',body:{action:'viewport',args:{center:view.center,zoom:view.zoom}},timeoutMs:60000}).catch(()=>{}).finally(()=>{lifecycle.pending.delete(pending);lastLocal.current=Date.now();});
  lifecycle.pending.add(pending);
 };
 const dropPendingView=()=>{if(viewTimer.current){clearTimeout(viewTimer.current);viewTimer.current=null;}};
 const perform=(action,args)=>command(action,args).catch(()=>{});
 async function checkSetup(){
  await refreshBootstrap.current();if(!current())return;
  initialized.current=false;modeSent.current=null;channel.current=crypto.randomUUID();setReady(false);setRenderer('Waiting for renderer');setFrameEpoch(n=>n+1);
  summary.refetch();selected.refetch();
 }
 async function fetchLayerGeojson(id){
  const data=await ctx.rest('/layer/'+encodeURIComponent(id));
  if(!data?.geojson||!Array.isArray(data.geojson.features))throw new Error('Layer data could not be read.');
  if(hasPointFeatures(data.geojson))pointLayers.current.add(id);else pointLayers.current.delete(id);
  return data.geojson;
 }
 async function zoomToLayer(layer){
  let geojson=layer.geojson;
  if(!geojson)geojson=await fetchLayerGeojson(layer.id);
  post('mapdesk:fit',{bbox:boundsForGeojson(geojson)}); // the guest fits; its moveend write-back persists the camera
 }
 async function importDataset(action,args){
  const result=await command(action,args);if(!result||!current())return result;
  try{
   const fresh=await summary.refetch();if(!current())return result;
   const layer=fresh.data?.layers.find(item=>item.id===result.id);
   if(layer){
    let geojson=layer.geojson;
    if(!geojson){
     const fetched=await ctx.rest('/layer/'+encodeURIComponent(layer.id));
     if(fetched?.geojson&&Number.isFinite(fetched.data_revision)){
      setBodies(prev=>({...prev,[layer.id]:{revision:fetched.data_revision,geojson:fetched.geojson}}));
      geojson=fetched.geojson;
     }
    }
    if(geojson){
     if(hasPointFeatures(geojson))pointLayers.current.add(layer.id);else pointLayers.current.delete(layer.id);
     post('mapdesk:fit',{bbox:boundsForGeojson(geojson)});
     const top=styleFields({fields:layer.fields,geojson})[0];
     const distinct=top?new Set(geojson.features.map(feature=>String(feature.properties?.[top]??''))).size:0;
     if(top&&distinct>=2&&distinct<=24){await command('update_layer',{layer_id:layer.id,changes:{style:{color_by:top}}});return {...result,colorBy:top};}
    }
   }
  }catch(_){if(current())setError('Dataset saved, but the map view could not update. Use Zoom to layer; do not re-import it.');}
  return result;
 }
 async function loadSample(){
  if(busy)return;
  setBusy(true);setError('');
  try{for(const item of sampleLayers()){await importDataset('import_geojson',{name:item.name,geojson:item.geojson});if(!current())return;}}
  catch(e){if(current())setError(e.message||'Sample data could not be loaded.');}
  finally{if(current())setBusy(false);}
 }
 const snapshotPng=()=>{if(ready)post('mapdesk:export-png');};
 async function exportWorkspace(){
  try{
   const full=await ctx.rest('/state');
   downloadBlob(JSON.stringify(full,null,2),'application/json',(full.name||'mapdesk').replace(/[^\w.-]+/g,'_').slice(0,80)+'.json');
  }catch(e){if(current())setError(e.message||'Workspace export failed.');}
 }
 function moveLayer(id,delta){
  const ids=(latest.current?.layers||[]).map(layer=>layer.id),i=ids.indexOf(id),j=i+delta;
  if(i<0||j<0||j>=ids.length)return;
  const next=[...ids];[next[i],next[j]]=[next[j],next[i]];
  perform('reorder',{layer_ids:next});
 }
 async function exportLayer(layer,asCsv){
  try{
   const geojson=await fetchLayerGeojson(layer.id);
   const safe=(layer.name||'mapdesk').replace(/[^\w.-]+/g,'_').slice(0,80);
   if(asCsv){const table=csvFromFeatures(geojson.features);downloadBlob('\uFEFF'+[table.headers,...table.rows].map(row=>row.map(csvCell).join(',')).join('\r\n'),'text/csv',safe+'.csv');}
   else downloadBlob(JSON.stringify({type:'FeatureCollection',features:geojson.features}),'application/geo+json',safe+'.geojson');
  }catch(e){host.notify({kind:'error',message:e.message||'Export failed.'});}
 }
 async function exportLayerFile(layer,action){
  if(exporting)return;
  setExporting(layer.id);setError('');
  try{
   const result=await command(action,action==='export_shp'?{layer_id:layer.id}:{layer_ids:[layer.id]});
   if(!result||typeof result.base64!=='string'||!result.filename)throw new Error('The export response was incomplete.');
   downloadBlob(decodeBase64ToBlob(result.base64,EXPORT_MIMES[action]),EXPORT_MIMES[action],String(result.filename));
  }catch(e){if(current())setError(e.message||'Export failed.');}
  finally{if(current())setExporting(null);}
 }
 async function exportWorkspaceGpkg(){
  if(exportingWorkspace||!state.layers.length)return;
  setExportingWorkspace(true);setError('');
  try{
   const result=await command('export_gpkg',{layer_ids:state.layers.map(layer=>layer.id)});
   if(!result||typeof result.base64!=='string'||!result.filename)throw new Error('The export response was incomplete.');
   downloadBlob(decodeBase64ToBlob(result.base64,EXPORT_MIMES.export_gpkg),EXPORT_MIMES.export_gpkg,String(result.filename));
  }catch(e){if(current())setError(e.message||'GeoPackage export failed.');}
  finally{if(current())setExportingWorkspace(false);}
 }
 async function renameSavedMap(id,nextName){
  try{
   const file=await ctx.files.readText('data','saved-map-'+id+'.json');
   if(file?.ok){try{const record=JSON.parse(file.text);record.name=nextName;await ctx.files.writeText('data','saved-map-'+id+'.json',JSON.stringify(record));}catch(_){}}
   const index=await readSavedMaps(ctx);
   const written=await ctx.files.writeText('data',MAP_INDEX,JSON.stringify(index.map(item=>item.id===id?{...item,name:nextName}:item)));
   if(written?.ok)setSavedMaps(await readSavedMaps(ctx));else setError('Saved map list could not be updated.');
  }catch(e){setError(e.message||'Rename failed.');}
 }
 async function deleteSavedMap(id){
  try{
   const index=await readSavedMaps(ctx);
   const written=await ctx.files.writeText('data',MAP_INDEX,JSON.stringify(index.filter(item=>item.id!==id)));
   if(written?.ok)setSavedMaps(await readSavedMaps(ctx));else setError('Saved map list could not be updated.');
  }catch(e){setError(e.message||'Saved map list could not be updated.');}
  finally{setDeletingMap(null);}
 }
 useEffect(()=>{
  live.current=true;let disposed=false;
  const bootstrap=()=>{
   if(!disposed&&current())setTokenStatus('checking');
   return ctx.rest('/bootstrap').then(data=>{if(!disposed&&current()){setToken(data.token||'');setTokenStatus(data.token?'configured':data.token_rejected?'rejected':'missing');}}).catch(()=>{if(!disposed&&current()){setTokenStatus('failed');setError('Token setup could not be read. Check that MapDesk is enabled.');}});
  };
  refreshBootstrap.current=bootstrap;
  bootstrap();
  const stop=ctx.onEvent('plugin.mapdesk.settings.changed',bootstrap);
  return()=>{disposed=true;live.current=false;stop?.();};
 },[ctx,scope]);
 useEffect(()=>{ // live sync: revision pushes refresh the queries; the 5s poll stays untouched as the fallback
  if(typeof ctx.socket!=='function')return;
  let stop=null;
  try{stop=ctx.socket('/events',frame=>{
   try{
    const data=typeof frame==='string'?JSON.parse(frame):frame;
    // A push that only announces this pane's own write is redundant: command() already refetched.
    if(data&&data.type==='revision'&&!lifecycle.pending.size&&Date.now()-lastLocal.current>1500){summary.refetch();selected.refetch();}
   }catch(_){}
  });}catch(_){return;}
  return()=>{try{if(typeof stop==='function')stop();else if(stop&&typeof stop.dispose==='function')stop.dispose();}catch(_){}};
 },[ctx,scope]);
 useEffect(()=>{
  function receive(event){
   if(event.source !== frame.current?.contentWindow||!current())return;
   const data=event.data;
   if(!data||typeof data!=='object'||data.version!==1)return;
   if(data.type==='mapdesk:ready'){
    // A ready event means a guest document just booted. If the renderer was
    // swapped (guest-kind key change, src change), this is a FRESH guest that
    // knows nothing: reset the init handshake so the state push re-sends
    // mapdesk:init instead of streaming mapdesk:state into a channel it never bound.
    if(initialized.current){initialized.current=false;modeSent.current=null;styleSent.current=null;terrainSent.current=null;heatmapRef.current=false;clusterRef.current=false;setHeatmapState(false);setClusterState(false);}
    setReady(true);setGuestEpoch(epoch=>epoch+1);return;
   }
   if(data.channel!==channel.current)return;
   if(data.type==='mapdesk:loaded')setRenderer('Map ready');
   if(data.type==='mapdesk:needs-token')setRenderer('Mapbox setup required');
   if(data.type==='mapdesk:error')setRenderer('Map service unavailable — check token and connection');
   if(data.type==='mapdesk:png'&&typeof data.dataUrl==='string'&&data.dataUrl.startsWith('data:image/png')){
    const link=document.createElement('a');link.href=data.dataUrl;link.download='mapdesk-snapshot-'+new Date().toISOString().replace(/[:.]/g,'-')+'.png';link.click();return;
   }
   if(data.type==='mapdesk:viewport'&&Array.isArray(data.center)&&data.center.length===2&&data.center.every(Number.isFinite)&&Number.isFinite(data.zoom)){
    liveView.current={center:data.center,zoom:data.zoom};
    dropPendingView();viewTimer.current=setTimeout(writeViewport,350);
   }
   if(data.type==='mapdesk:select'&&typeof data.layer_id==='string'&&latest.current?.layers.some(l=>l.id===data.layer_id)){
    if(Array.isArray(data.feature_ids)&&data.feature_ids.length<=5000&&data.feature_ids.every(id=>typeof id==='string'||Number.isSafeInteger(id)))perform('select',{layer_id:data.layer_id,feature_ids:data.feature_ids});
    else if(Array.isArray(data.bbox)&&data.bbox.length===4&&data.bbox.every(Number.isFinite))perform('select',{layer_id:data.layer_id,bbox:data.bbox});
    else if(data.radius&&Array.isArray(data.radius.center)&&data.radius.center.length===2&&data.radius.center.every(Number.isFinite)&&Number.isFinite(data.radius.meters))perform('select',{layer_id:data.layer_id,radius:{center:data.radius.center,meters:data.radius.meters}});
   }
  }
  window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
 },[scope,ctx]);
 useEffect(()=>()=>{ // leaving the pane: save a camera move that is still waiting on its debounce
  if(!viewTimer.current)return;
  dropPendingView();
  if(scopeNow()===scope&&!lifecycle.cycle.get().opening)writeViewport();
 },[]);
 useEffect(()=>{const timer=setTimeout(()=>setPushFilters(filters),160);return()=>clearTimeout(timer);},[filters]); // the table filters at once; the map redraw waits for a typing pause
 useEffect(()=>{ // tell an EXTERNAL camera change (agent, restore, another window) from this pane's own write-backs
  const view=summary.data?.viewport,key=viewKey(view);
  if(!key)return;
  if(seenView.current===null){seenView.current=key;return;}
  if(key===seenView.current)return;
  seenView.current=key;
  const own=ownViews.current.indexOf(key);
  if(own>=0){ownViews.current.splice(0,own+1);return;}
  if(viewKey(liveView.current)===key)return;
  liveView.current={center:view.center,zoom:view.zoom};writtenView.current=key;
  externalView.current=true;
 },[summary.data]);
 useEffect(()=>{
  if(!ready||!state)return;
  const activeId=state.selection?.layer_id||state.layers.at(-1)?.id;
  const shown=shownState(state,activeId,pushFilters[activeId]||'');
  // Each layer body crosses into the guest once per revision; after that the
  // layer travels slim and the guest draws from its cache.
  if(!initialized.current)sentBodies.current=new Map();
  // viewport: the guest boots with the newest camera this pane knows; afterwards it only hears about external changes.
  const viewport=!initialized.current?(liveView.current||shown.viewport):externalView.current?shown.viewport:null;
  externalView.current=false;
  const outgoing={...shown,viewport,layers:shown.layers.map(layer=>{
   if(!layer.geojson)return layer;
   const revision=layer.client_revision??layer.data_revision;
   if(sentBodies.current.get(layer.id)===revision){const slim={...layer};delete slim.geojson;return slim;}
   sentBodies.current.set(layer.id,revision);
   return layer;
  })};
  if(!initialized.current){
   post('mapdesk:init',{token,state:outgoing,basemap:basemapRef.current,terrain:terrainRef.current});initialized.current=true;
   styleSent.current=basemapRef.current;terrainSent.current=terrainRef.current;
  }
  else {
   post('mapdesk:state',{state:outgoing});if(token)post('mapdesk:token',{token});
   if(styleSent.current!==basemapRef.current){post('mapdesk:style',{basemap:basemapRef.current});styleSent.current=basemapRef.current;}
   if(terrainSent.current!==terrainRef.current){post('mapdesk:terrain',{on:terrainRef.current});terrainSent.current=terrainRef.current;}
  }
  if(modeSent.current!==modeRef.current){post('mapdesk:mode',{mode:modeRef.current});modeSent.current=modeRef.current;}
 },[ready,state,token,pushFilters,guestEpoch]);
 useEffect(()=>{
  if(!archived)return;
  const timer=setTimeout(()=>{try{lifecycle.lastArchive.set(null);}catch(_){}},30000);
  return()=>clearTimeout(timer);
 },[archived,lifecycle]);
 const sourceBounds=parseBounds(bounds);
 async function importArcgis(){
  if(!sourceBounds||!url.trim())return;
  setBusy(true);setSourceNotice('');
  try{
   const label=sourceName.trim()||arcgisName(url);
   const result=await importDataset('import_arcgis',{name:label,url:url.trim(),bbox:sourceBounds,limit:1000,where:where.trim()||null});
   if(current()&&result)setSourceNotice(`Imported ${label}${result.colorBy?` · colored by ${result.colorBy}`:''}`);
  }
  catch(_){}finally{if(current())setBusy(false);}
 }
 async function saveMapNow(){
  if(savingMap||busy)return;
  setSavingMap(true);setSaveNotice('');
  try{
   const full=await ctx.rest('/state');
   const saved=await saveMapSnapshot(ctx,full,'manual');
   if(!current())return;
   if(saved){
    setSavedMaps(await readSavedMaps(ctx));
    setSaveNotice(`Saved “${saved.name}” to Saved maps.`);
   }else if(full?.layers?.length){
    setSaveNotice('Not saved — this map is too large to archive (over 3.5 MB).');
   }else{
    setSaveNotice('Nothing to save — the map has no layers.');
   }
  }catch(e){if(current())setSaveNotice(e.message||'Could not save this map.');}
  finally{if(current()){setSavingMap(false);setTimeout(()=>{if(current())setSaveNotice('');},6000);}}
 }
 async function restoreMap(id){
  if(!ctx.files?.readText){setError('This desktop build cannot read saved maps.');return;}
  if(busy)return;
  const result=await ctx.files.readText('data','saved-map-'+id+'.json');
  if(!result?.ok){setError('Saved map could not be read.');return;}
  let record;
  try{record=JSON.parse(result.text);}catch(_){setError('Saved map file is not valid JSON.');return;}
  if(!Array.isArray(record.layers)||!record.layers.length){setError('Saved map has no layers.');return;}
  setBusy(true);setError('');
  try{
   const full=await ctx.rest('/state').catch(()=>null);
   if(full?.layers?.length)await saveMapSnapshot(ctx,full,'before-restore');
   for(const layer of [...(full?.layers||latest.current?.layers||[])])await command('remove_layer',{layer_id:layer.id});
   for(const layer of record.layers){
    const added=await command('import_geojson',{name:layer.name,geojson:layer.geojson});
    if(added?.id)await command('update_layer',{layer_id:added.id,changes:{visible:layer.visible!==false,style:layer.style||{color:'#3388ff',opacity:0.8,color_by:null}}});
   }
   if(record.name)await command('save',{name:record.name});
   if(Array.isArray(record.viewport?.center))await command('viewport',{center:record.viewport.center,zoom:record.viewport.zoom});
  }catch(e){if(current())setError(e.message||'Restore failed. The archive was not deleted.');}
  // Restoring archives the map it replaces; show that new entry in Saved maps right away.
  finally{if(current()){setBusy(false);readSavedMaps(ctx).then(list=>{if(current())setSavedMaps(list);}).catch(()=>{});}}
 }
 async function clearWorkspace(reason,allowNoArchive){
  if(busyRef.current)return;
  busyRef.current=true;
  dropPendingView(); // the camera is about to be reset; a queued write-back would fight the verification
  setBusy(true);setError('');
  let keepConfirm=false;
  try{
   const scopeAtStart=scopeNow();
   await Promise.allSettled([...lifecycle.pending]); // drain in-flight pane writes first
   const previous=await ctx.rest('/state');
   if(scopeNow()!==scopeAtStart)throw new Error('Connection or profile changed');
   let archived=null;
   if(previous.layers?.length){
    archived=await saveMapSnapshot(ctx,previous,reason||'before-clear');
    if(!archived&&!allowNoArchive){
     if(reason==='session-change')throw new Error('The map could not be archived, so it was not cleared automatically. Use Clear map to clear it without an archive.');
     keepConfirm=true;setConfirmClear('no-archive');return; // manual: offer a clear-without-archive stage
    }
   }
   for(const layer of previous.layers)await command('remove_layer',{layer_id:layer.id});
   await command('select',{layer_id:null,feature_ids:[]});
   await command('viewport',{center:[0,0],zoom:2});
   await command('save',{name:'Untitled map'});
   const fresh=await ctx.rest('/state');
   if(scopeNow()!==scopeAtStart)throw new Error('Connection or profile changed');
   if(fresh.layers.length||fresh.name!=='Untitled map'||fresh.selection.layer_id!==null||fresh.viewport.zoom!==2||fresh.viewport.center.some(v=>v!==0))throw new Error('Workspace changed during clear');
   setFilters({});
   setSavedMaps(await readSavedMaps(ctx));
   if(archived){try{lifecycle.lastArchive.set(archived);}catch(_){}}
  }catch(e){if(current())setError(e.message||'Clear failed. Some changes may already be saved — run Clear map again.');}
  finally{busyRef.current=false;if(current()){setBusy(false);if(!keepConfirm)setConfirmClear(false);}}
 }
 useEffect(()=>{ // frame request: hand the bbox to the guest via mapdesk:fit; its moveend write-back persists the camera
  const request=latest.current?.viewport_request;
  if(!request||!Array.isArray(request.bbox)||request.bbox.length!==4||request.bbox.some(v=>!Number.isFinite(v)))return;
  if(framedRef.current===request.request_id)return;
  framedRef.current=request.request_id;
  if(!current())return;
  post('mapdesk:fit',{bbox:request.bbox});
 },[state?.viewport_request?.request_id]);
 useEffect(()=>{ // auto-clear when a different session becomes active (archive-first, preference-gated)
  if(!activeSession)return;
  const currentId=String(activeSession);
  if(!lastSessionRef.current){lastSessionRef.current=currentId;persistSetting('lastSessionId',currentId);return;}
  if(lastSessionRef.current===currentId)return;
  lastSessionRef.current=currentId;
  persistSetting('lastSessionId',currentId);
  if(clearOnSessionRef.current&&latest.current?.layers?.length&&!busyRef.current&&current())clearWorkspace('session-change');
 },[activeSession]);
 if(!state||!shell.data)return jsx('div',{style:{padding:20},children:[jsx('h2',{children:'MapDesk'}),jsx('p',{role:'status',children:summary.error||shell.error?'Cannot reach the MapDesk workspace.':'Opening the persistent workspace…'}),jsx('p',{children:'Enable both the agent and Desktop components in Settings → Plugins → MapDesk. If Hermes reports a dependency restart is required, save your work before restarting.'}),button('Open Mapbox settings',()=>host.navigate('/settings?tab=plugins&agent=mapdesk')),button('Retry',()=>{summary.refetch();shell.refetch();refreshBootstrap.current();})]});
 const visiblePoints=state.layers.some(layer=>layer.visible&&hasPointFeatures(layer.geojson))||layers.some(layer=>layer.visible&&pointLayers.current.has(layer.id));
 const selectionLayer=state.layers.find(layer=>layer.id===state.selection.layer_id);
 const selectionFitOk=(state.selection.feature_ids||[]).length>0&&Boolean(selectionLayer&&selectionLayer.geojson);
 return jsx('section',{'aria-label':'MapDesk workspace',onDragEnter:e=>{if(Array.isArray(e.dataTransfer?.types)&&e.dataTransfer.types.includes('Files')){dropDepth.current++;setDropping(true);}},onDragOver:e=>{if(Array.isArray(e.dataTransfer?.types)&&e.dataTransfer.types.includes('Files')){e.preventDefault();setDropping(true);}},onDragLeave:()=>{dropDepth.current=Math.max(0,dropDepth.current-1);if(!dropDepth.current)setDropping(false);},onDrop:e=>{if(!e.dataTransfer?.files?.length)return;e.preventDefault();dropDepth.current=0;setDropping(false);if(intake.current)intake.current(e.dataTransfer.files[0]);},style:{display:'flex',flexDirection:'column',height:'100%',minHeight:300,fontSize:12,color:'var(--ui-text-primary)',...(dropping?{outline:'2px dashed var(--ui-accent)',outlineOffset:-6}:{})},children:[
  jsx('header',{style:{padding:12,borderBottom:BORDER},children:[
   jsx('div',{style:{...ROW,justifyContent:'space-between'},children:[jsx('strong',{style:{fontSize:15},children:state.name}),jsx('span',{style:MUTED,children:`${state.layers.length} layers · WGS84 · saved r${state.revision}`})]}),
   jsx('div',{style:{...ROW,marginTop:8},children:[
    jsx('span',{role:'status',style:MUTED,children:renderer}),
    button('Refresh',()=>{summary.refetch();selected.refetch();refreshBootstrap.current();}),
    confirmClear==='no-archive'?jsx('div',{role:'group','aria-label':'Confirm clearing without an archive',style:ROW,children:[
     jsx('span',{children:'This map is too large to archive — clearing it cannot be undone.'}),
     button(busy?'Clearing…':'Clear anyway',()=>clearWorkspace('before-clear',true),busy),
     button('Keep map',()=>setConfirmClear(false),busy)
    ]}):confirmClear?jsx('div',{role:'group','aria-label':'Confirm clearing the workspace',style:ROW,children:[
     jsx('span',{children:'Archive the current map and clear the workspace?'}),
     button(busy?'Clearing…':'Clear map',()=>clearWorkspace('before-clear',false),busy),
     button('Keep map',()=>setConfirmClear(false),busy)
    ]}):button('Clear map',()=>setConfirmClear(true),busy),
    button(savingMap?'Saving…':'Save map',saveMapNow,busy||savingMap||!state.layers.length),
    saveNotice&&jsx('span',{role:'status',style:MUTED,children:saveNotice}),
    jsx('span',{style:{flex:1}}),
    button('Select',()=>setMode('select'),!ready||mode==='select'),
    button('Measure',()=>setMode('measure'),!ready||mode==='measure'),
    button('Snapshot PNG',snapshotPng,!ready),
    button('Export workspace',exportWorkspace),
    button(exportingWorkspace?'Exporting…':'Export GeoPackage',exportWorkspaceGpkg,exportingWorkspace||!state.layers.length)
   ]}),
   jsx('div',{style:{...ROW,marginTop:6},children:[
    choiceMenu('Basemap',basemap,setBasemapValue,BASEMAP_CHOICES,null,{width:104,minWidth:0},false),
    toggleButton('Terrain 3D',terrain,toggleTerrain,false),
    toggleButton('Heatmap',heatmap,toggleHeatmap,!ready||!visiblePoints),
    toggleButton('Cluster',cluster,toggleCluster,!ready||!visiblePoints),
    button('Fit to selection',fitSelection,!ready||!selectionFitOk)
   ]}),
   jsx(SetupGuide,{tokenStatus,renderer,open:setupOpen,onOpen:setSetupOpen,layerCount:state.layers.length,onCheck:checkSetup,clearOnSession,onClearOnSession:value=>{clearOnSessionRef.current=value;setClearOnSessionState(value);persistSetting(ctx,'clearOnSession',value);}}),
   (error||summary.error)&&jsx('div',{role:'alert',style:{marginTop:8,padding:8,border:BORDER},children:error||summary.error.message}),
   archived&&jsx('div',{role:'status',style:{...ROW,marginTop:8,padding:'6px 8px',border:BORDER},children:[
    jsx('span',{children:(archived.reason==='before-clear'?'Map cleared':archived.reason==='session-change'?'Map cleared for the new session':'Blank map started')+' — previous map archived.'}),
    button('Undo',()=>{try{lifecycle.lastArchive.set(null);}catch(_){}restoreMap(archived.id);},busy),
    button('Dismiss',()=>{try{lifecycle.lastArchive.set(null);}catch(_){}})
   ]})
  ]}),
  jsx('div',{style:{display:'flex',flex:1,minHeight:0},children:[
   jsx('aside',{'aria-label':'Map layers',style:{width:280,maxWidth:'55%',flexShrink:0,overflowY:'auto',borderRight:BORDER,padding:10},children:[
    jsx(FileImporter,{onImport:importDataset,isCurrent:current,busy,setBusy,rest:ctx.rest,intake,onArcgisUrl:text=>{setUrl(text);setWhere('');setSourceNotice('');setArcOpen(true);}}),
    !state.layers.length&&jsx('div',{style:{...ROW,alignItems:'center',margin:'0 0 12px'},children:[button(busy?'Loading…':'Load sample data',loadSample,busy),jsx('span',{style:MUTED,children:'Three small synthetic layers (not real data) to try the map, table and tools.'})]}),
    jsx('div',{style:{...MUTED,letterSpacing:1,marginBottom:8},children:'LAYERS / SOURCE SNAPSHOTS'}),
    ...state.layers.map((layer,index)=>jsx('article',{style:{border:BORDER,borderRadius:6,padding:8,marginBottom:8},children:[
     jsx('label',{style:ROW,children:[jsx('input',{type:'checkbox',checked:layer.visible,onChange:e=>perform('update_layer',{layer_id:layer.id,changes:{visible:e.target.checked}})}),jsx('strong',{children:layer.name})]}),
     jsx('p',{style:MUTED,children:`${layer.feature_count.toLocaleString()} features · ${layer.source.complete===false?'INCOMPLETE SAMPLE':'complete input'}`}),
     layer.source.total_count!=null&&jsx('p',{style:MUTED,children:`${layer.source.total_count.toLocaleString()} matched at source`}),
     jsx('div',{style:ROW,children:[jsx('input',{type:'color','aria-label':`Color for ${layer.name}`,value:layer.style.color,onChange:e=>perform('update_layer',{layer_id:layer.id,changes:{style:{color:e.target.value,color_by:null}}}),style:{width:30,height:25,border:0,background:'transparent'}}),fieldMenu(`Color field for ${layer.name}`,layer.style.color_by||'',value=>perform('update_layer',{layer_id:layer.id,changes:{style:{color_by:value||null}}}),'Single color',styleFields(layer),{width:140,minWidth:0})]}),
     jsx('input',{type:'range',min:0,max:1,step:0.05,defaultValue:layer.style.opacity,'aria-label':`Opacity for ${layer.name}`,onMouseUp:e=>perform('update_layer',{layer_id:layer.id,changes:{style:{opacity:Number(e.target.value)}}}),onKeyUp:e=>perform('update_layer',{layer_id:layer.id,changes:{style:{opacity:Number(e.target.value)}}}),style:{width:'100%',marginTop:6}}),
     jsx('details',{style:{marginTop:6},children:[
      jsx('summary',{children:'Labels & classes'}),
      jsx('div',{style:ROW,children:[
       fieldMenu(`Label field for ${layer.name}`,layer.style.label_field||'',next=>perform('update_layer',{layer_id:layer.id,changes:{style:{label_field:next||null}}}),'No labels',layer.fields||[],{width:110,minWidth:0}),
       choiceMenu(`Class method for ${layer.name}`,layer.style.class_method||'auto',next=>perform('update_layer',{layer_id:layer.id,changes:{style:{class_method:next||'auto'}}}),CLASS_METHODS,null,{width:130,minWidth:0}),
       choiceMenu(`Class count for ${layer.name}`,layer.style.class_count||5,next=>perform('update_layer',{layer_id:layer.id,changes:{style:{class_count:Number(next)||5}}}),CLASS_COUNTS,null,{width:80,minWidth:0}),
       choiceMenu(`Palette for ${layer.name}`,layer.style.palette||'classic',next=>perform('update_layer',{layer_id:layer.id,changes:{style:{palette:next||'classic'}}}),PALETTE_CHOICES,null,{width:100,minWidth:0})
      ]}),
      jsx('p',{style:MUTED,children:'Labels, classes and palettes restyle the layer; stored data is unchanged.'})
     ]}),
     jsx('div',{style:ROW,children:[
      jsx(Button,{type:'button',variant:'outline',size:'sm',onClick:()=>moveLayer(layer.id,-1),disabled:index===0,'aria-label':`Move ${layer.name} up`,children:'▲'}),
      jsx(Button,{type:'button',variant:'outline',size:'sm',onClick:()=>moveLayer(layer.id,1),disabled:index===state.layers.length-1,'aria-label':`Move ${layer.name} down`,children:'▼'}),
      button(state.selection.layer_id===layer.id?'Active selection layer':'Select in this layer',()=>perform('select',{layer_id:layer.id,feature_ids:[]})),
      button('Zoom to layer',()=>zoomToLayer(layer).catch(()=>{})),
      jsx('details',{style:{marginTop:6},children:[
       jsx('summary',{style:{cursor:'pointer'},children:exporting===layer.id?'Exporting…':'Export ▾'}),
       jsx('div',{style:{...ROW,marginTop:4},children:[
        button('GeoJSON',()=>exportLayer(layer,false),exporting===layer.id),
        button('CSV',()=>exportLayer(layer,true),exporting===layer.id),
        button('GeoPackage',()=>exportLayerFile(layer,'export_gpkg'),exporting===layer.id),
        button('Shapefile',()=>exportLayerFile(layer,'export_shp'),exporting===layer.id),
        button('KMZ',()=>exportLayerFile(layer,'export_kmz'),exporting===layer.id)
       ]})
      ]}),
      removing===layer.id?jsx('div',{role:'group','aria-label':`Confirm removing ${layer.name}`,style:{...ROW,marginTop:6},children:[jsx('span',{children:'Remove this layer and its stored features? This cannot be undone.'}),button('Yes, remove',()=>{setRemoving(null);perform('remove_layer',{layer_id:layer.id});}),button('Keep layer',()=>setRemoving(null))]}):button('Remove layer',()=>setRemoving(layer.id))
     ]}),
     jsx('details',{style:{marginTop:6},children:[jsx('summary',{children:'Rename'}),jsx('div',{style:{...ROW,marginTop:4},children:[
      jsx('input',{'aria-label':`New name for ${layer.name}`,defaultValue:layer.name,onChange:e=>{renameLayers.current[layer.id]=e.target.value;},style:FIELD}),
      button('Save name',()=>{const next=String(renameLayers.current[layer.id]??layer.name).trim();if(next&&next!==layer.name)perform('update_layer',{layer_id:layer.id,changes:{name:next}});})
     ]})]}),
     jsx('details',{style:{marginTop:6},children:[jsx('summary',{children:'Provenance'}),jsx('div',{style:{...MUTED,overflowWrap:'anywhere'},children:[jsx('p',{children:layer.source.url||'User-provided file'}),jsx('p',{children:layer.source.fetched_at?`Retrieved ${layer.source.fetched_at}`:'Retrieval time unavailable'}),jsx('p',{children:'Snapshot only; retrieval time is not source freshness. Check the original source before relying on it.'})]})]})
    ]},layer.id)),
    !state.layers.length&&jsx('p',{style:MUTED,children:'No saved layers yet. Import a file above or add a public ArcGIS source below.'}),
    jsx('details',{style:{margin:'8px 0'},children:[jsx('summary',{children:`Saved maps · ${savedMaps.length}`}),jsx('p',{style:MUTED,children:'Save map snapshots the current workspace here. Clear map and Open MapDesk also archive first — maps over 3.5 MB cannot be archived. Restore does not delete the archive.'}),savedMaps.length?savedMaps.map(item=>jsx('div',{style:{marginTop:8},children:[
     jsx('div',{style:ROW,children:[jsx('strong',{children:item.name}),jsx('span',{style:MUTED,children:`${(item.saved_at||'').slice(0,16).replace('T',' ')} · ${item.feature_count??'?'} features${item.session_id?` · ${item.session_id.slice(0,8)}`:''}`}),button('Restore',()=>restoreMap(item.id),busy)]}),
     jsx('div',{style:ROW,children:[
      jsx('details',{children:[jsx('summary',{children:'Rename'}),jsx('div',{style:{...ROW,marginTop:4},children:[jsx('input',{'aria-label':`New name for ${item.name}`,defaultValue:item.name,onChange:e=>{renameMaps.current[item.id]=e.target.value;},style:FIELD}),button('Save name',()=>{const next=String(renameMaps.current[item.id]??item.name).trim();if(next&&next!==item.name)renameSavedMap(item.id,next);})]})]}),
      deletingMap===item.id?jsx('span',{role:'group','aria-label':`Confirm deleting ${item.name}`,style:ROW,children:[jsx('span',{children:'Delete from this list? The archive file itself stays on disk.'}),button('Yes, delete',()=>deleteSavedMap(item.id)),button('Keep',()=>setDeletingMap(null))]}):button('Delete',()=>setDeletingMap(item.id))
     ]})
    ]},item.id)):jsx('p',{style:MUTED,children:'No archived maps yet.'})]}),
    jsx('details',{open:arcOpen,onToggle:e=>setArcOpen(e.target.open),children:[
     jsx('summary',{children:'Public ArcGIS source'}),
     jsx('details',{style:{marginBottom:6},children:[
      jsx('summary',{children:'Public data'}),
      jsx('p',{style:MUTED,children:'A small verified catalog; each entry below returned a live feature count at release time and prefills this form. Edit the URL or Where clause as needed.'}),
      ...PUBLIC_CATALOG.map(entry=>jsx('div',{style:{...ROW,marginTop:6,alignItems:'flex-start'},children:[
       jsx('div',{style:{flex:1},children:[jsx('div',{children:entry.label}),entry.note&&jsx('p',{style:MUTED,children:entry.note})]}),
       button('Use',()=>{setUrl(entry.url);setWhere(entry.where||'');setSourceNotice('');},busy)
      ]},entry.url))
     ]}),
     jsx('p',{style:MUTED,children:'Any public HTTPS FeatureServer/number or MapServer/number, with a WGS84 query envelope. A live network query; up to 1,000 results, and samples stay labeled incomplete.'}),
     textInput('HTTPS layer URL',url,setUrl),
     textInput('Where (optional)',where,setWhere),
     textInput('Layer name (optional)',sourceName,setSourceName),
     textInput('West,south,east,north',bounds,setBounds),
     bounds.trim()&&!sourceBounds&&jsx('p',{role:'alert',children:'Enter four WGS84 numbers: west,south,east,north, with west below east and south below north.'}),
     sourceNotice&&jsx('p',{role:'status',children:sourceNotice}),
     jsx('div',{style:{...ROW,marginTop:6},children:[button('Use current map view',()=>setBounds(boundsForViewport(liveView.current||state.viewport,frame.current?.clientWidth||800,frame.current?.clientHeight||500).join(','))),button(busy?'Importing…':'Import ArcGIS',importArcgis,busy||!url.trim()||!sourceBounds)]})
    ]}),
    jsx('details',{style:{marginTop:14},children:[jsx('summary',{children:'Workspace name'}),textInput('Workspace name',name,setName),button('Save name',()=>perform('save',{name}),!name.trim())]})
   ]}),
   jsx('div',{style:{flex:1,minWidth:0,minHeight:220,position:'relative'},children:jsx(SandboxedFrame,{ref:frame,src:'data:text/html;charset=utf-8,'+encodeURIComponent(shell.data.html),title:'MapDesk interactive map',style:{width:'100%',height:'100%',border:0}},frameEpoch)})
  ]}),
  jsx('div',{role:'separator','aria-label':'Drag to resize the attributes panel','aria-orientation':'horizontal',title:'Drag to resize',onPointerDown:startAttrDrag,style:{height:8,flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center',cursor:'row-resize',touchAction:'none',borderTop:BORDER},children:jsx('div',{style:{width:44,height:3,borderRadius:2,background:'var(--ui-stroke-secondary)'}})}),
  jsx('footer',{style:{height:attrPanelHeight,flexShrink:0,padding:'4px 12px 8px',display:'flex',flexDirection:'column',minHeight:0},children:[
   jsx('div',{style:{...ROW,justifyContent:'space-between',marginBottom:2},children:[
    jsx('strong',{children:`Selection · ${selected.data?.total??0} features`}),
    jsx('span',{style:MUTED,children:'Click a row to select · Shift-drag box · Ctrl-drag radius'})
   ]}),
   (()=>{
    const active=state.layers.find(l=>l.id===state.selection.layer_id)||state.layers.at(-1);
    if(!active)return null;
    if(!active.geojson)return jsx('p',{style:{...MUTED,margin:'4px 0'},children:bodyErrors[active.id]?'Active layer data could not be loaded; the layer was not removed.':'Loading active layer attributes…'});
    const ids=state.selection.layer_id===active.id?state.selection.feature_ids:[];
    return jsx('div',{style:{flex:1,minHeight:0,overflowY:'auto'},children:[jsx(AttributeTable,{layer:active,layers:state.layers,query:filters[active.id]||'',onQuery:value=>setFilters(prev=>({...prev,[active.id]:value})),onClearFilter:()=>setFilters(prev=>({...prev,[active.id]:''})),selectedIds:ids,onSelect:id=>perform('select',{layer_id:active.id,feature_ids:[id]}),onSelectMany:featureIds=>perform('select',{layer_id:active.id,feature_ids:featureIds}),onSpatial:(op,target)=>perform('select',{layer_id:active.id,spatial:{op,layer_id:target}}),busy,panelHeight:attrPanelHeight},active.id)]},active.id);
   })()
  ]})
 ]});
}
export default {
 id:'mapdesk',name:'MapDesk',
 register(ctx){
  const lifecycle={cycle:atom({epoch:0,opening:false}),lastArchive:atom(null),pending:new Set()};
  const render=()=>jsx(MapDesk,{ctx,lifecycle});
  async function openBlank(){
   if(lifecycle.cycle.get().opening)return;
   const scope=scopeNow(),epoch=lifecycle.cycle.get().epoch+1;
   lifecycle.cycle.set({epoch,opening:true});
   lifecycle.lastArchive.set(null);
   const request=(path,options)=>{
    if(scopeNow()!==scope)throw new Error('Connection or profile changed');
    return ctx.rest(path,options);
   };
   const command=(action,args)=>request('/command',{method:'POST',body:{action,args},timeoutMs:60000});
   try{
    // Use the existing backend commands so Desktop hot-reload needs no host restart.
    // Old frames are gated immediately; drain their writes before reading the layers.
    await Promise.allSettled([...lifecycle.pending]);
    const previous=await request('/state');
    let archived=null;
    if(previous.layers?.length){
     archived=await saveMapSnapshot(ctx,previous,'before-blank');
     if(!archived)throw new Error('Could not archive the current map, so it was not cleared');
    }
    for(const layer of previous.layers)await command('remove_layer',{layer_id:layer.id});
    await command('select',{layer_id:null,feature_ids:[]});
    await command('viewport',{center:[0,0],zoom:2});
    await command('save',{name:'Untitled map'});
    const fresh=await request('/state');
    if(scopeNow()!==scope)throw new Error('Connection or profile changed');
    if(fresh.layers.length||fresh.name!=='Untitled map'||fresh.selection.layer_id!==null||fresh.selection.feature_ids.length||fresh.selection.bbox!==null||fresh.viewport.zoom!==2||fresh.viewport.center.some(v=>v!==0))throw new Error('Workspace changed during reset');
    lifecycle.lastArchive.set(archived);
    host.openWorkspace('mapdesk',{title:'MapDesk',render});
    return archived;
   }catch(error){
    const reason=String(error?.message||'Workspace request failed').slice(0,240);
    lifecycle.lastArchive.set(null);
    host.notify({kind:'error',message:`MapDesk could not finish starting a blank map: ${reason}. Some changes may already be saved. Run Open MapDesk again to retry.`});
    return null;
   }finally{lifecycle.cycle.set({epoch,opening:false});}
  }
  ctx.register({id:'map',area:'panes',title:'MapDesk',data:{placement:'right',width:'760px'},render});
  // Palette-free access: a sidebar row that opens the map as a full workspace
  // page. Non-destructive — it shows the CURRENT map; only the palette command
  // "Open MapDesk" archives and starts a blank one.
  ctx.register({id:'map-route',area:ROUTES_AREA,data:{path:'/mapdesk'},render});
  ctx.register({id:'map-nav',area:SIDEBAR_NAV_AREA,data:{path:'/mapdesk',label:'MapDesk',codicon:'map'}});
  ctx.register({id:'open',area:PALETTE_AREA,data:{id:'mapdesk.open',label:'Open MapDesk',run:openBlank}});
 }
};
