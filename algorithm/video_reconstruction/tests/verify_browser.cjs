const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const ROOT=path.resolve(__dirname,'..');
const option=(name,fallback)=>{const i=process.argv.indexOf(name);return i<0?fallback:process.argv[i+1];};
const regularized=process.argv.includes('--regularized'),gallery=process.argv.includes('--gallery');
const WEB=path.resolve(ROOT,option('--web-dir','web'));
const OUT=path.resolve(ROOT,option('--out','results/'+(regularized?'browser_regularized':'browser')));
fs.mkdirSync(OUT,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const browserLog=fs.openSync(path.join(OUT,'browser.log'),'w');
 const browserExecutable=process.env.BROWSER_EXECUTABLE||(process.platform==='win32'?'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe':'chromium');
 const profile=fs.mkdtempSync(path.join(OUT,'profile-'));
 const browser=spawn(browserExecutable,['--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',
  '--user-data-dir='+profile,'--window-size=1440,1000','--use-angle=swiftshader','--enable-unsafe-swiftshader','about:blank'],{stdio:['ignore',browserLog,browserLog]});
 browser.on('error',error=>fs.appendFileSync(path.join(OUT,'browser.log'),String(error)));
 let ws;
 try{
  let pages;
  for(let i=0;i<100;i++){
   try{const port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);pages=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();if(pages.length)break;}catch{}
   await sleep(200);
  }
  if(!pages?.length)throw Error('Browser debugging endpoint not available');
  ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  let seq=0;const pending=new Map(),exceptions=[];
  ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}}if(m.method==='Runtime.exceptionThrown')exceptions.push(m.params.exceptionDetails);};
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  await send('Runtime.enable');await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  const screenshot=async name=>{const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(OUT,name+'.png'),Buffer.from(r.data,'base64'));};
  await send('Page.navigate',{url:pathToFileURL(path.join(WEB,'index.html')).href});
  if(gallery){
   let report;
   for(let i=0;i<100;i++){report=await evaluate(`({cards:[...document.querySelectorAll('a.card')].map(a=>({title:a.querySelector('h2').textContent,url:a.getAttribute('href')})),images:[...document.images].map(i=>i.complete&&i.naturalWidth>0)})`);if(report.cards.length===4&&report.images.length===5&&report.images.every(Boolean))break;await sleep(100);}
   if(report.cards.length!==4||!report.images.every(Boolean))throw Error('Gallery assets failed to load');
   for(const card of report.cards)if(!fs.existsSync(path.resolve(WEB,card.url)))throw Error('Broken gallery link: '+card.url);
   await screenshot('gallery');report.exceptions=exceptions;fs.writeFileSync(path.join(OUT,'verification.json'),JSON.stringify(report,null,2));
   if(exceptions.length)throw Error('Gallery browser errors');console.log(JSON.stringify(report,null,2));await send('Browser.close');return;
  }
  for(let i=0;i<150;i++){const status=await evaluate('({ready:document.body?.dataset.ready,texture:document.body?.dataset.texture,error:document.body?.dataset.error})');if(status.error)throw Error(status.error);if(status.ready&&status.texture==='loaded')break;if(i===149)throw Error('Viewer never became ready');await sleep(200);}
  const initial=await evaluate('window.tunnelViewer.stats()');
  const setup=await evaluate(`(()=>{const D=window.TUNNEL_DATA,mid=Math.floor(D.cameras.length/2);const items=D.defects.filter(d=>(d.fitted?.points||d.points).length).sort((a,b)=>Math.abs(D.cameras.findIndex(c=>c.image_name===a.image_name)-mid)-Math.abs(D.cameras.findIndex(c=>c.image_name===b.image_name)-mid));const d=items[0];return{frame:d?D.cameras.findIndex(c=>c.image_name===d.image_name):mid,id:d?.id||null,count:D.cameras.length,surfaceKind:D.regularized?.report.surface_kind||'regularized_tunnel',hasRefinement:!!D.regularized};})()`);
  await evaluate(`window.tunnelViewer.setFrame(${setup.frame});window.tunnelViewer.setMode('follow')`);
  const select=async()=>{if(setup.id)await evaluate(`window.tunnelViewer.selectCandidateId?window.tunnelViewer.selectCandidateId(${JSON.stringify(setup.id)}):window.tunnelViewer.selectCandidate(0)`);};
  const comparisons=[];
  if(regularized){
   if(!setup.hasRefinement)throw Error('Expected a refined surface');
   await evaluate('window.tunnelViewer.setSurface("regularized")');await select();await sleep(300);
   comparisons.push(await evaluate('window.tunnelViewer.stats()'));await screenshot('optimized');
   await evaluate('window.tunnelViewer.setSurface("raw")');await select();comparisons.push(await evaluate('window.tunnelViewer.stats()'));await screenshot('raw_same_view');
   await evaluate('window.tunnelViewer.setSurface("regularized");document.getElementById("showInferred").click()');await screenshot('support_regions');
   await evaluate('document.getElementById("showInferred").click();document.getElementById("showTexture").click();document.getElementById("overview").click()');await screenshot('overview_geometry');
   if(setup.surfaceKind==='regularized_tunnel'){
    await evaluate('document.getElementById("showCaps").click()');await screenshot('closed_geometry');
    if(!(await evaluate('document.getElementById("stats").textContent')).includes('0边界边'))throw Error('Closed surface must display zero boundary edges');
    await evaluate('document.getElementById("showCaps").click()');
   }else if(!(await evaluate('document.getElementById("showCaps").disabled')))throw Error('Unobserved end caps must stay disabled');
   await evaluate('document.getElementById("showTexture").click()');
   if(comparisons[0].surface!=='regularized'||comparisons[1].surface!=='raw')throw Error('Surface comparison failed');
  }
  await screenshot('overview');
  await evaluate(`window.tunnelViewer.setFrame(${setup.frame});window.tunnelViewer.setMode('follow')`);await select();
  await sleep(300);const follow=await evaluate('window.tunnelViewer.stats()');await screenshot('camera_and_defect');
  await evaluate('document.getElementById("focus").click()');await screenshot('defect_focus');
  const projection=await evaluate(`(()=>{const V=window.tunnelViewer,D=window.TUNNEL_DATA;if(!V.projectSourcePixel)return null;V.setSurface('raw');V.setMode('follow');const d=D.defects.find(d=>d.points.length);if(!d)return null;V.setFrame(D.cameras.findIndex(c=>c.image_name===d.image_name));const c=D.cameras[V.stats().frame],canvas=document.getElementById('scene'),pixels=d.pixels.filter((_,i)=>d.pixel_hits[i]);let max=0;for(let i=0;i<Math.min(20,d.points.length);i++){const q=V.projectSourcePixel(d.points[i]);if(!q)throw Error('Original-image projection failed');const u=q[0],v=q[1];max=Math.max(max,Math.hypot(u-pixels[i][0],v-pixels[i][1]));}return{model:c.model,scope:'Calibrated original-image projection; mesh viewport uses virtual perspective',max_pixel_error:max,samples:Math.min(20,d.points.length)};})()`);
  if(projection&&projection.max_pixel_error>.2)throw Error('Browser camera projection differs from source pixels: '+JSON.stringify(projection));
  const repeated=await evaluate(`(()=>{const box=document.getElementById('repeatOnly');if(!box||box.disabled)return null;const before=window.tunnelViewer.stats().currentCandidates;box.click();const after=window.tunnelViewer.stats().currentCandidates;box.click();return{before,after};})()`);
  if(repeated&&repeated.after>repeated.before)throw Error('Evidence filter added candidates');
  const linkedView=await evaluate(`(()=>{const V=window.tunnelViewer,D=window.TUNNEL_DATA,group=D.multiview?.groups.find(g=>g.observation_count>1);if(!group)return null;const d=D.defects.find(d=>d.id===group.observation_ids[0]);V.setSurface('raw');V.setFrame(D.cameras.findIndex(c=>c.image_name===d.image_name));V.selectCandidateId(d.id);const before=V.stats(),button=document.querySelector('#relatedViews button');if(!button)throw Error('Related-frame link missing');button.click();return{before,after:V.stats()};})()`);
  if(linkedView&&(linkedView.before.frame===linkedView.after.frame||!linkedView.after.selected))throw Error('Related-frame navigation failed');
  await evaluate('window.tunnelViewer.setFrame(window.TUNNEL_DATA.cameras.length-1)');
  const last=await evaluate('window.tunnelViewer.stats()');
  const payload={initial,setup,comparisons,follow,last,projection,repeated,linkedView,exceptions,ready:await evaluate('({...document.body.dataset})')};
  fs.writeFileSync(path.join(OUT,'verification.json'),JSON.stringify(payload,null,2));
  if(exceptions.length||initial.glError||follow.glError||last.glError)throw Error('Browser/GL errors found');
  if(initial.triangles<1||last.frame!==setup.count-1||(setup.id&&!follow.selected))throw Error('Timeline/selection/mesh assertion failed');
  console.log(JSON.stringify(payload,null,2));await send('Browser.close');
 }finally{if(ws)ws.close();browser.kill();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
