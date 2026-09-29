const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const ROOT=path.resolve(__dirname,'..');
const regularized=process.argv.includes('--regularized');
const OUT=path.join(ROOT,'results',regularized?'browser_regularized':'browser');
fs.mkdirSync(OUT,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const browserLog=fs.openSync(path.join(OUT,'browser.log'),'w');
 const browserExecutable=process.env.BROWSER_EXECUTABLE||(process.platform==='win32'?'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe':'chromium');
 const browser=spawn(browserExecutable,
 ['--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=9337',
 '--user-data-dir='+path.join(OUT,'profile'),'--window-size=1440,1000','--use-angle=swiftshader','--enable-unsafe-swiftshader','about:blank'],{stdio:['ignore',browserLog,browserLog]});
 browser.on('error',error=>fs.appendFileSync(path.join(OUT,'browser.log'),String(error)));
 let ws;
 try{
  let pages;
  for(let i=0;i<100;i++){try{pages=await (await fetch('http://127.0.0.1:9337/json/list')).json();if(pages.length)break;}catch{}await sleep(200);}
  if(!pages?.length)throw Error('Browser debugging endpoint not available');
  ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  let seq=0;const pending=new Map(),exceptions=[];
  ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}}if(m.method==='Runtime.exceptionThrown')exceptions.push(m.params.exceptionDetails);};
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  await send('Runtime.enable');await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:pathToFileURL(path.join(ROOT,'web','index.html')).href});
  for(let i=0;i<150;i++){const status=await evaluate('({ready:document.body?.dataset.ready,texture:document.body?.dataset.texture,error:document.body?.dataset.error})');if(status.error)throw Error(status.error);if(status.ready&&status.texture==='loaded')break;if(i===149)throw Error('Viewer never became ready');await sleep(200);}
  const screenshot=async name=>{const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(OUT,name+'.png'),Buffer.from(r.data,'base64'));};
  const initial=await evaluate('window.tunnelViewer.stats()');
  const comparisons=[];
  if(regularized){
   await evaluate('window.tunnelViewer.setFrame(20);window.tunnelViewer.setMode("follow");window.tunnelViewer.setSurface("regularized");window.tunnelViewer.selectCandidate(0)');
   await sleep(300);comparisons.push(await evaluate('window.tunnelViewer.stats()'));await screenshot('optimized');
   await evaluate('window.tunnelViewer.setSurface("raw");window.tunnelViewer.selectCandidate(0)');
   comparisons.push(await evaluate('window.tunnelViewer.stats()'));await screenshot('raw_same_view');
   await evaluate('window.tunnelViewer.setSurface("regularized");document.getElementById("showInferred").click()');
   await screenshot('support_regions');
   await evaluate('document.getElementById("showInferred").click();document.getElementById("showTexture").click();document.getElementById("overview").click()');
   await screenshot('overview_geometry');
   await evaluate('document.getElementById("showCaps").click()');await screenshot('closed_geometry');
   const closedLabel=await evaluate('document.getElementById("stats").textContent');
   if(!closedLabel.includes('0边界边'))throw Error('Closed surface must display zero boundary edges');
   await evaluate('document.getElementById("showCaps").click();document.getElementById("showTexture").click()');
   if(comparisons[0].surface!=='regularized'||comparisons[0].triangles>=comparisons[1].triangles||!comparisons[0].markerCount)throw Error('Surface comparison or candidate mapping failed');
  }
  await screenshot('overview');
  await evaluate('window.tunnelViewer.setFrame(10); window.tunnelViewer.setMode("follow");window.tunnelViewer.selectCandidate(0)');
  await sleep(500);const follow=await evaluate('window.tunnelViewer.stats()');await screenshot('camera_and_defect');
  await evaluate('document.getElementById("focus").click()');await sleep(200);await screenshot('defect_focus');
  await evaluate('window.tunnelViewer.setFrame(window.TUNNEL_DATA.cameras.length-1)');
  const last=await evaluate('window.tunnelViewer.stats()');
  const payload={initial,comparisons,follow,last,exceptions,ready:await evaluate('({...document.body.dataset})')};
  fs.writeFileSync(path.join(OUT,'verification.json'),JSON.stringify(payload,null,2));
  if(exceptions.length||initial.glError||follow.glError||last.glError)throw Error('Browser/GL errors found');
  if(initial.triangles<1||last.frame!==39||!follow.selected)throw Error('Timeline/selection/mesh assertion failed');
  console.log(JSON.stringify(payload,null,2));
  await send('Browser.close');
 }finally{if(ws)ws.close();browser.kill();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
