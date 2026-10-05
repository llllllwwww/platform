const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(__dirname,'output'),videos=process.env.PUBLIC_VIDEO_DIR||path.join(out,'public-videos');
const cases=require('./tunnel-video-cases.json').map(c=>({...c,file:c.playbackFile||c.file}));
const expectedVideos=cases.length*3;
const near=(a,b,eps=1e-6)=>assert(Math.abs(a-b)<eps,`${a} != ${b}`);
(async()=>{
 for(const c of cases)if(!fs.existsSync(path.join(videos,c.file)))throw Error('请先运行 python tests/download_public_videos.py 下载公开验证素材');
 const manifest=JSON.parse(fs.readFileSync(path.join(videos,'sources.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true}),checks=[],errors=[],videoResults=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 const pass=(name,extra)=>{checks.push({name,...extra});console.log('PASS '+name);};
 const snapshot=()=>page.evaluate(()=>SLZJ.snapshot());
 const frame=async()=>{await page.waitForFunction(()=>document.querySelector('#twinFrame')?.contentWindow?.SLZJScene);return page.frames().find(f=>f.url().includes('tunnel-scene.html'));};
 const scene=async()=>{const f=await frame();return f.evaluate(()=>SLZJScene.snapshot());};
 const seek=async time=>{await page.evaluate(time=>{const v=document.querySelector('#monitorVideo');v.pause();v.currentTime=time;},time);await page.waitForFunction(time=>Math.abs(document.querySelector('#monitorVideo').currentTime-time)<.02&&!document.querySelector('#monitorVideo').seeking,time);};
 async function annotate(point){
  await seek(point.time);await page.locator('[data-evidence-action="capture-video"]').click();
  await page.locator('#evidenceFrameWrap').waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelector('#evidenceFrame').dataset.frameReady==='true');
  const box=await page.locator('#evidenceFrame').boundingBox();await page.locator('#evidenceFrame').click({position:{x:box.width*point.u,y:box.height*point.v}});
  assert.equal(await page.locator('#evidenceType').count(),0);const before=(await snapshot()).evidenceRecords.length;
  await page.locator('#evidenceSave').click();await page.waitForFunction(n=>SLZJ.snapshot().evidenceRecords.length===n+1,before);
  return (await snapshot()).evidenceRecords.at(-1);
 }
 try{
  await page.goto('http://127.0.0.1:8765/index.html#tasks');await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('#evidenceLink').waitFor();
  const initial=await snapshot();
  await page.locator('#evidenceSceneMode').selectOption('evidence');await page.waitForFunction(()=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().visibleIds.length===0);
  pass('无现场标注时对应场景为空，不用演示台账填充');
  let lastIds=[];
  for(const c of cases){
   const source=manifest.find(x=>(x.playbackFile||x.file)===c.file);assert(source);
   await page.locator('#monitorFile').setInputFiles(path.join(videos,c.file));
   await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready&&TunnelVideoMonitor.evidenceIdentity().sourceId?.startsWith('VF-'));
   const identity=await page.evaluate(()=>TunnelVideoMonitor.evidenceIdentity().sourceId);assert.equal(identity,'VF-'+(source.playbackSha256||source.sha256));
   pass('公开视频真实解码且SHA-256匹配：'+c.file);
   await page.waitForFunction(()=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().visibleIds.length===0);pass('新录像自动进入空的现场对应模式，既有点不混入：'+c.file);
   const duration=await page.locator('#monitorVideo').evaluate(v=>v.duration),records=[];
   for(const point of c.points){
    const r=await annotate(point);records.push(r);near(r.mileage,3128+48*point.time/duration);
    near(r.position.x,r.mileage-3128);assert.equal(r.type,null);assert.equal(r.classificationStatus,'pending');assert.equal(r.anchors[0].sourceId,identity);
    const f=await frame();await f.waitForFunction(id=>SLZJScene.snapshot().visibleIds.includes(id),r.id);
    const pos=(await scene()).positions.find(x=>x.id===r.id);assert.equal(pos.type,'suspected');assert.equal(pos.typeName,'疑似点');near(pos.x,r.position.x);near(pos.y,r.position.y);near(pos.z,r.position.z);
   }
   for(let i=0;i<records.length;i++){
    const r=records[i];await seek(r.anchors[0].timeSec);
    await page.waitForFunction(id=>!!document.querySelector(`.monitor-evidence-point[data-evidence-id="${id}"]`),r.id);
    const actual=await page.locator(`.monitor-evidence-point[data-evidence-id="${r.id}"]`).evaluate(el=>{const v=document.querySelector('#monitorVideo'),b=v.getBoundingClientRect(),p=el.getBoundingClientRect(),scale=Math.min(b.width/v.videoWidth,b.height/v.videoHeight);return {number:el.textContent,x:p.x+p.width/2,y:p.y+p.height/2,left:b.x+(b.width-v.videoWidth*scale)/2,top:b.y+(b.height-v.videoHeight*scale)/2,width:v.videoWidth*scale,height:v.videoHeight*scale};});
    assert.equal(actual.number,String(i+1));near(actual.x,actual.left+r.anchors[0].u*actual.width,.08);near(actual.y,actual.top+r.anchors[0].v*actual.height,.08);
    assert.equal((await scene()).positions.find(p=>p.id===r.id).sequence,i+1);
   }
   await seek(c.points[2].time+1);assert.equal(await page.locator('.monitor-evidence-point').count(),0);
   pass('录像编号点落在原像素位置，含黑边换算且仅在记录时刻出现：'+c.file);
   assert(records[0].mileage<records[1].mileage&&records[1].mileage<records[2].mileage);
   pass('视频三处疑似点不判断类型、顺序及XYZ与仿真一致：'+c.file,{ids:records.map(r=>r.id)});
   lastIds=records.map(r=>r.id);const visible=(await scene()).visibleIds;assert.deepEqual(visible.sort(),lastIds.slice().sort());
   pass('不同视频来源的病害不会混入当前对应场景：'+c.file);
   await page.locator(`#evidenceList [data-evidence-action="locate"][data-id="${records[1].id}"]`).click();
   await page.waitForFunction(x=>Math.abs(document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().carX-x)<1e-6,records[1].position.x);
   near((await scene()).carX,records[1].position.x);pass('定位按钮按同一位置对齐检测车：'+c.file);
   await page.locator('#evidenceFollowVideo').check();await seek(c.points[2].time);await page.waitForFunction(id=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().selectedId===id,records[2].id);
   await page.waitForFunction(x=>Math.abs(document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().carX-x)<.03,records[2].position.x);
   near((await scene()).carX,records[2].position.x,.03);await page.locator('#evidenceFollowVideo').uncheck();
   pass('录像进度联动检测车，病害出现次序相同：'+c.file);
   videoResults.push({file:c.file,sha256:source.playbackSha256||source.sha256,originalFile:source.file,originalSha256:source.sha256,conversion:source.conversion||null,durationSec:duration,observations:records.map(r=>({id:r.id,type:r.type,timeSec:r.anchors[0].timeSec,u:r.anchors[0].u,v:r.anchors[0].v,mileage:r.mileage,angle:r.angle,position:r.position}))});
  }
  assert.equal((await snapshot()).evidenceRecords.length,expectedVideos);assert.deepEqual((await snapshot()).defects,initial.defects);near((await snapshot()).assessment.shi,initial.assessment.shi);
  pass(expectedVideos+'条未分类疑似位置不改写原16条台账和SHI');
  const f=await frame();await f.evaluate(()=>parent.postMessage({channel:'slzj',type:'toggle-task'},location.origin));
  // Adding/editing annotations refreshes the same scene, retaining first-person controls.
  await page.evaluate(()=>document.activeElement.blur());await page.keyboard.press('p');await page.keyboard.press('f');await f.waitForFunction(()=>SLZJScene.snapshot().firstPerson);
  const oldFrame=page.frames().find(x=>x.url().includes('tunnel-scene.html'));
  const last=(await snapshot()).evidenceRecords.at(-1);await page.locator(`#evidenceList [data-evidence-action="edit"][data-id="${last.id}"]`).click();
  assert.equal(await page.locator('#evidenceType').count(),0);await page.locator('[data-evidence-action="update"]').click();
  assert.equal(page.frames().find(x=>x.url().includes('tunnel-scene.html')),oldFrame);assert.equal((await scene()).firstPerson,true);
  assert.equal((await snapshot()).evidenceRecords.find(r=>r.id===last.id).type,null);
  await f.waitForFunction(id=>SLZJScene.snapshot().positions.find(p=>p.id===id).type==='suspected',last.id);
  await page.keyboard.press('Escape');pass('更新病害不重建场景，第一人称与背景组件保留');
  await page.locator('#navigation a[href="#twin"]').click();await page.locator('#evidenceUnfold').waitFor();
  await page.waitForFunction(ids=>ids.every(id=>document.querySelector('#twinFrame')?.contentWindow?.SLZJScene?.snapshot().visibleIds.includes(id)),lastIds);
  const twin=await scene(),snap=await snapshot();for(const id of lastIds){const r=snap.evidenceRecords.find(x=>x.id===id),p=twin.positions.find(x=>x.id===id);assert.equal(p.type,r.type||'suspected');near(p.x,r.position.x);near(p.y,r.position.y);near(p.z,r.position.z);}
  assert.equal(await page.locator('#evidenceForm').isVisible(),false);
  await page.locator(`#evidenceList [data-evidence-action="edit"][data-id="${lastIds[0]}"]`).click();assert.equal(await page.locator('#evidenceForm').isVisible(),true);assert.equal(await page.locator('#evidenceSave').isVisible(),false);
  pass('第三模块孪生、环位横断面、展开图使用同一来源和坐标');
  await page.locator('#evidenceLink').screenshot({path:path.join(out,'现场证据与孪生对应.png')});
  await page.locator('#twinFrame').screenshot({path:path.join(out,'现场病害三维定位.png')});
  await page.locator('#evidenceSourceFilter').selectOption('all');await page.waitForFunction(n=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().visibleIds.length===n,expectedVideos);
  pass('可切换当前源与全部证据，不丢失其他视频标注');
  await page.locator('#evidenceSceneMode').selectOption('demo');await page.locator('#unfold').waitFor();await page.waitForFunction(()=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().visibleIds.length===16);
  pass('切回原有演示台账，原16处病害和历史评估恢复');
  await page.locator('#navigation a[href="#radar"]').click();await page.locator('#csvFile').setInputFiles(path.resolve('examples/radar_stream_sample.json'));await page.locator('[data-action="import-csv"]').click();
  await page.waitForFunction(()=>SLZJ.snapshot().radarData.length===1);await page.locator('[data-evidence-action="capture-radar"]').click();await page.locator('#evidenceFrameWrap').waitFor({state:'visible'});
  await page.locator('#evidenceTrace').fill('1');await page.locator('#evidenceSample').fill('1');await page.locator('[data-evidence-action="pick-radar"]').click();await page.locator('#evidenceType').selectOption('void');
  const before=(await snapshot()).evidenceRecords.length;await page.locator('#evidenceSave').click();await page.waitForFunction(n=>SLZJ.snapshot().evidenceRecords.length===n+1,before);
  const rr=(await snapshot()).evidenceRecords.at(-1);assert.equal(rr.anchors[0].trace,1);assert.equal(rr.anchors[0].sample,1);near(rr.mileage,3144);near(rr.depth,.175);
  assert((await page.locator('#radarSimulation').innerText()).includes('同源病害对应复核'));
  pass('雷达道号/采样点按同一规则进入仿真与孪生，支持相对位置');
  for(const id of ['externalRawRadar','rawRadar']){
   const marked=await page.locator('#'+id).evaluate(c=>{const pixel=c.getContext('2d').getImageData(Math.floor(45+583/3+6),Math.floor(12+201/2-1),3,3).data;for(let i=0;i<pixel.length;i+=4)if(pixel[i]>220&&pixel[i+1]<180&&pixel[i+2]<180)return true;return false;});assert(marked,'同源标注未绘制到 '+id);
  }pass('外部剖面和对应复核剖面的同一道/采样点绘制相同病害标记');
  await page.locator('#radarSimulation').screenshot({path:path.join(out,'同源雷达病害对应.png')});
  await page.locator('#evidenceList [data-evidence-action="edit"]').last().click();await page.locator('#evidenceType').selectOption('water');await page.locator('[data-evidence-action="update"]').click();
  await page.locator('#navigation a[href="#twin"]').click();await page.locator('#evidenceUnfold').waitFor();await page.waitForFunction(id=>document.querySelector('#twinFrame')?.contentWindow?.SLZJScene?.snapshot().visibleIds.includes(id),rr.id);
  const rp=(await scene()).positions.find(p=>p.id===rr.id);assert.equal(rp.type,'water');near(rp.x,16);pass('修改雷达病害类型同时更新对应标记和第三模块');
  await page.locator(`#evidenceList [data-evidence-action="radar"][data-id="${rr.id}"]`).click();await page.locator('#externalRawRadar').waitFor({state:'visible'});assert.equal(await page.locator('#externalTrace').inputValue(),'1');
  pass('三维病害能够返回原雷达快照和正确道号');
  // Associate one video and radar anchor with a single identity, preserving the existing position.
  const videoId=lastIds[0];await page.locator('[data-evidence-action="capture-radar"]').click();await page.locator('#evidenceFrameWrap').waitFor({state:'visible'});
  await page.locator('#evidenceTrace').fill('1');await page.locator('#evidenceSample').fill('1');await page.locator('[data-evidence-action="pick-radar"]').click();await page.locator('#evidenceTarget').selectOption(videoId);
  await page.locator('#evidenceSave').click();await page.waitForFunction(id=>SLZJ.snapshot().evidenceRecords.find(r=>r.id===id).anchors.length===2,videoId);
  pass('同一病害编号同时绑定视频帧与雷达证据，位置一致');
  await page.locator('#navigation a[href="#tasks"]').click();await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready);await page.locator('#evidenceSceneMode').selectOption('evidence');await page.locator('#evidenceSourceFilter').selectOption('all');
  await page.waitForFunction(id=>document.querySelector('#twinFrame')?.contentWindow?.SLZJScene?.snapshot().positions.find(p=>p.id===id)?.type==='suspected',rr.id);
  assert.equal((await snapshot()).evidenceRecords.find(r=>r.id===rr.id).type,'water');
  assert.equal(await page.locator('#evidenceType').count(),0);pass('采集场景只显示位置，旧雷达分类信息仍保留');
  const dl=page.waitForEvent('download');await page.locator('[data-evidence-action="export"]').click();await (await dl).saveAs(path.join(out,'现场病害对应验证.json'));
  const exported=JSON.parse(fs.readFileSync(path.join(out,'现场病害对应验证.json'),'utf8'));assert.equal(exported.records.length,expectedVideos+1);assert.equal(exported.schemaVersion,2);assert.equal(exported.records.filter(r=>r.type===null).length,expectedVideos);
  await page.reload();await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('#evidenceLink').waitFor();assert.equal((await snapshot()).evidenceRecords.length,expectedVideos+1);
  pass('导出完整对应关系和视频帧，刷新后记录及来源仍保留');
  // A separate fresh browser context proves that the actual import control restores evidence.
  const restored=await browser.newPage({viewport:{width:1440,height:1050}});
  restored.on('pageerror',e=>errors.push(e.message));
  await restored.goto('http://127.0.0.1:8765/index.html#tasks');await restored.locator('#taskEvidenceDrawer > summary').click();await restored.locator('#evidenceLink').waitFor();
  await restored.locator('#evidenceImport').setInputFiles(path.join(out,'现场病害对应验证.json'));
  await restored.waitForFunction(n=>SLZJ.snapshot().evidenceRecords.length===n,expectedVideos+1);
  const loaded=await restored.evaluate(()=>SLZJ.snapshot().evidenceRecords);
  for(const r of exported.records){const v=loaded.find(x=>x.id===r.id);assert.equal(v.type,r.type);near(v.mileage,r.mileage);assert.deepEqual(v.anchors,r.anchors);}
  pass('新浏览器通过实际导入控件恢复编号、未分类状态、坐标与真实帧');
  await restored.locator('#evidenceImport').setInputFiles(path.join(out,'现场病害对应验证.json'));
  await restored.waitForFunction(()=>document.querySelector('#evidenceMessage').textContent.includes('编号已存在'));
  assert.equal((await restored.evaluate(()=>SLZJ.snapshot().evidenceRecords)).length,expectedVideos+1);
  pass('重复导入明确拒绝且不改变已有对应记录');
  await restored.locator('#monitorFile').setInputFiles(path.join(videos,cases.at(-1).file));
  await restored.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready&&TunnelVideoMonitor.evidenceIdentity().sourceId?.startsWith('VF-'));
  await restored.locator('#navigation a[href="#twin"]').click();await restored.locator('#evidenceUnfold').waitFor();
  await restored.locator(`#evidenceList [data-evidence-action="video"][data-id="${lastIds[1]}"]`).click();
  await restored.locator('#monitorVideo').waitFor({state:'visible'});
  await restored.waitForFunction(time=>Math.abs(document.querySelector('#monitorVideo').currentTime-time)<.02&&!document.querySelector('#monitorVideo').seeking,cases.at(-1).points[1].time);
  assert.equal((await restored.evaluate(()=>TunnelVideoMonitor.evidenceIdentity())).sourceId,exported.records.find(r=>r.id===lastIds[1]).anchors[0].sourceId);
  pass('刷新后重开原文件，孪生返回正确视频与冻结帧时间');
  await restored.close();

  for(const width of [1024,768,390]){await page.setViewportSize({width,height:1000});await page.locator('#evidenceLink').waitFor();const sizes=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,view:innerWidth}));assert(sizes.scroll<=sizes.view+2);pass('对应界面无整页横向溢出：'+width);}
  await page.locator('#batch').selectOption('B202607');assert.equal((await snapshot()).evidenceRecords.length,0);assert.equal(await page.locator('#evidenceList [data-evidence-row]').count(),0);pass('切换批次不会带入原视频/雷达病害');
  assert.deepEqual(errors,[]);pass('公开视频和雷达对应流程无页面异常或控制台错误');
  fs.writeFileSync(path.join(out,'evidence-correspondence-results.json'),JSON.stringify({checkedAt:new Date().toISOString(),checks,errors,videos:videoResults,boundary:'3段真实隧道检测作业视频（2个作业场景），9个人工待复核位置观测，不判断类型；顺序与相对位置验证，无工程里程真值，也不评估识别准确率。'},null,2));
  console.log('ALL '+checks.length+' EVIDENCE CHECKS PASSED');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
