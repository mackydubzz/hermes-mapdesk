// Test-only SDK adapter. Real React/Query, actual plugin and HTTP/SQLite backend.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import plugin from '../desktop/plugin.js';
const client = new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}});
window.__rest=[];window.__files=new Map();
const ctx = {
 source:'mapdesk-ui-test',
 async rest(path, options={}) {
  const response=await fetch('/api'+path,{method:options.method||'GET',headers:{'Content-Type':'application/json'},body:options.body?JSON.stringify(options.body):undefined});
  const data=await response.json();
  window.__rest.push({path:path.replace(/\/layer\/.*/,'/layer/:id'),action:options.body&&options.body.action});
  if(!response.ok)throw new Error(data.detail||'Backend unavailable');
  // The real host pushes a revision frame after every command; a test opts in to the same.
  if(path==='/command'&&window.__socket&&window.__simulatePush)setTimeout(()=>window.__socket&&window.__socket({type:'revision'}),0);
  return data;
 },
 files:{ // in-memory stand-in for the host's per-plugin file store (saved maps)
  async readText(scope,name){const key=scope+'/'+name;return window.__files.has(key)?{ok:true,text:window.__files.get(key)}:{ok:false,message:'not found'};},
  async writeText(scope,name,text){window.__files.set(scope+'/'+name,String(text));return {ok:true};},
 },
 socket(path,listener){window.__socket=listener;return ()=>{window.__socket=null;};},
 onEvent(){return ()=>{};},
 register(item){if(item.area==='panes')window.renderMapDesk=item.render;if(item.area==='palette')window.openMapDesk=item.data.run;}
};
plugin.register(ctx);
createRoot(document.getElementById('root')).render(<QueryClientProvider client={client}>{window.renderMapDesk()}</QueryClientProvider>);
