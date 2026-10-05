/* 疑似位置采集：倒序补录后的浏览顺序、原画幅定位、移动端与架构手册。 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=require('./tunnel-video-cases.json').find(c=>c.uiExample),points=fixture.points;
const out=path.join(__dirname,'output'),file=path.join(out,'public-videos',fixture.playbackFile||fixture.file);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),checks=[],errors=[];
 const pass=name=>{checks.push({name});console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 const scene=()=>page.evaluate(()=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot());
 const seek=async t=>{await page.locator('#monitorVideo').evaluate((v,t)=>{v.pause();v.currentTime=t},t);await page.waitForFunction(t=>{const v=document.querySelector('#monitorVideo');return !v.seeking&&Math.abs(v.currentTime-t)<.01},t)};
 try{
  await page.goto('http://127.0.0.1:8765/index.html#tasks');await page.locator('#taskEvidenceDrawer > summary').click();
  await page.locator('#monitorFile').setInputFiles(file);await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready);
  assert.equal(await page.locator('#evidenceType').count(),0);pass('第一阶段只记录位置，表单没有必选或默认病害类型');
  for(const p of [points[2],points[0],points[1]]){
   await seek(p.time);await page.locator('[data-evidence-action=capture-video]').click();await page.locator('#evidenceFrameWrap').waitFor({state:'visible'});
   const rect=await page.locator('#evidenceFrame').boundingBox();await page.locator('#evidenceFrame').click({position:{x:rect.width*p.u,y:rect.height*p.v}});
   const count=await page.evaluate(()=>SLZJ.snapshot().evidenceRecords.length);await page.locator('#evidenceSave').click();await page.waitForFunction(n=>SLZJ.snapshot().evidenceRecords.length===n+1,count);
  }
  const records=(await page.evaluate(()=>SLZJ.snapshot().evidenceRecords)).sort((a,b)=>a.mileage-b.mileage);
  assert.deepEqual(records.map(r=>r.anchors[0].timeSec),points.map(p=>p.time));assert(records.every(r=>r.type===null&&r.classificationStatus==='pending'));pass('先记后段再补录前段，记录仍按视频时间与相对里程排列');
  await page.locator(`#evidenceList [data-evidence-action=locate][data-id="${records[0].id}"]`).click();
  await page.locator(".scene-settings > summary").click();
  for(const [dir,id]of [[1,records[1].id],[1,records[2].id],[-1,records[1].id],[-1,records[0].id]]){
   await page.locator(`[data-action=step-defect][data-dir="${dir}"]`).click();await page.waitForFunction(id=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().selectedId===id,id);
  }pass('上一 / 下一疑似点按出现顺序导航，不依赖保存先后');
  await seek(points[1].time);await page.waitForFunction(()=>document.querySelector('.monitor-evidence-point')?.textContent==='2');
  const point=await page.locator('.monitor-evidence-point').evaluate((el,a)=>{const v=document.querySelector('#monitorVideo'),r=v.getBoundingClientRect(),e=el.getBoundingClientRect(),s=Math.min(r.width/v.videoWidth,r.height/v.videoHeight);return {x:e.x+e.width/2,y:e.y+e.height/2,expectedX:r.x+(r.width-v.videoWidth*s)/2+v.videoWidth*s*a.u,expectedY:r.y+(r.height-v.videoHeight*s)/2+v.videoHeight*s*a.v,pointer:getComputedStyle(el.parentElement).pointerEvents};},records[1].anchors[0]);
  assert(Math.abs(point.x-point.expectedX)<.1&&Math.abs(point.y-point.expectedY)<.1);assert.equal(point.pointer,'none');pass('原录像定位点按实际画幅显示，且不拦截播放控件');
  await page.locator(`#evidenceList [data-evidence-action=locate][data-id="${records[1].id}"]`).click();
  await page.waitForFunction(x=>Math.abs(document.querySelector("#twinFrame").contentWindow.SLZJScene.snapshot().carX-x)<1e-6,records[1].position.x);
  const visibleProgress=parseFloat(await page.locator('#taskPercent').textContent()),expectedProgress=(records[1].mileage-3128)/48*100;
  assert(Math.abs(visibleProgress-expectedProgress)<.06);assert.match(await page.locator('#taskStatus').textContent(),/暂停/);
  assert.equal(await page.locator(`[data-task-row="${records[1].taskId}"] [data-task-progress]`).textContent(),await page.locator('#taskPercent').textContent());
  const distance=Number((await page.locator('#taskDistance').textContent()).match(/[\d.]+/)[0]);assert(Math.abs(distance-records[1].position.x)<.06);

  await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('[data-action=view][data-view=iso]').first().click();
  await page.locator('#taskComparison').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'疑似位置采集-1440.png')});
  await page.setViewportSize({width:390,height:1000});await page.locator('#monitorScreen').scrollIntoViewIfNeeded();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:path.join(out,'疑似位置采集-390.png')});pass('双画面对照在桌面与手机布局均无整页横向溢出');
  await seek(points[1].time+1);await page.waitForFunction(()=>!document.querySelector('#monitorVideo').seeking);assert.equal(await page.locator('.monitor-evidence-point').count(),0);pass('离开记录时刻后隐藏编号点，不假装连续追踪');
  await page.goto('http://127.0.0.1:8765/平台架构与使用流程.html');
  assert.equal(await page.locator('details.step').count(),19);assert((await page.locator('body').innerText()).includes('只记录疑似位置'));assert((await page.locator('body').innerText()).includes('type=null'));
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));}
  pass('架构说明保留19步和四个模块，并明确位置采集与类型识别分工');
  assert.deepEqual(errors,[]);pass('疑似位置采集、顺序导航和架构说明无页面或控制台异常');
  fs.writeFileSync(path.join(out,'video-position-ui-results.json'),JSON.stringify({checkedAt:new Date().toISOString(),checks,errors,video:fixture.playbackFile||fixture.file,times:points.map(p=>p.time),boundary:'高速公路隧道检测原始拍摄段的3个人工待复核位置点，按相对规则映射；不做自动检测或实测定位精度结论。'},null,2));
  console.log('ALL '+checks.length+' POSITION UI CHECKS PASSED');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
