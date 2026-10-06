/* 录像联动回归：先导入无标注录像，再验证播放、暂停、拖动和控制权。 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.join(__dirname,'output'),fixtures=require('./tunnel-video-cases.json');
const file=c=>{const published=path.join(__dirname,'..','assets','videos',c.playbackFile||c.file);return fs.existsSync(published)?published:path.join(out,'public-videos',c.playbackFile||c.file);};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--use-angle=swiftshader','--enable-unsafe-swiftshader','--use-fake-device-for-media-stream']});
 const page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'reduce'}),checks=[],errors=[],samples=[];
 await page.addInitScript(() => { window.__SLZJ_DISABLE_AUTO_INFERENCE__ = true; });
 const pass=name=>{checks.push({name});console.log('PASS '+name)};
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 const read=()=>page.evaluate(()=>{const v=document.querySelector('#monitorVideo'),s=document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot(),state=SLZJ.snapshot();return {time:v.currentTime,duration:v.duration,paused:v.paused,carX:s.carX,scenePlaying:s.playing,records:state.evidenceRecords.length,source:TunnelVideoMonitor.evidenceIdentity().sourceId,task:state.tasks.find(t=>t.id===state.evidenceContext.taskId)}});
 const seek=async time=>{await page.locator('#monitorVideo').evaluate((v,time)=>{v.pause();v.currentTime=time},time);await page.waitForFunction(t=>{const v=document.querySelector('#monitorVideo');return !v.seeking&&Math.abs(v.currentTime-t)<.02},time)};
 const aligned=async (label,tolerance=.04)=>{await page.waitForFunction(tolerance=>{const v=document.querySelector('#monitorVideo'),s=document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot();return Math.abs(s.carX-48*v.currentTime/v.duration)<tolerance},tolerance,{timeout:2500});const r=await read();samples.push({label,...r,expectedX:48*r.time/r.duration});return r};
 try{
  await page.goto('http://127.0.0.1:8765/index.html#tasks');await page.waitForFunction(()=>document.querySelector('#twinFrame')?.contentWindow?.SLZJScene,null,{timeout:60000});
  await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('#monitorFile').setInputFiles(file(fixtures[0]));await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready&&TunnelVideoMonitor.evidenceIdentity().sourceId);
  assert.equal((await read()).records,0);pass('新录像无需先标注疑似点');
  await seek(8);await page.locator('#evidenceFollowVideo').check();const first=await aligned('开启时对齐');pass('暂停在非零时刻，勾选后立即对齐仿真车');
  assert(Math.abs(first.task.progress-first.time/first.duration)<1e-6);assert(Math.abs(parseFloat(await page.locator('#taskPercent').textContent())-first.time/first.duration*100)<.06);
  const distance=Number((await page.locator('#taskDistance').textContent()).match(/[\d.]+/)[0]);assert(Math.abs(distance-first.carX)<.06);pass('任务百分比、相对距离与录像进度使用同一位置');
  await seek(10);await aligned('无标注拖动');pass('没有任何标注时，拖动录像进度仍更新仿真车');
  const before=await read();await page.locator('#monitorVideo').evaluate(v=>v.play());await page.waitForFunction(t=>document.querySelector('#monitorVideo').currentTime>t+1,before.time,{timeout:12000});
  const moving=await aligned('实际播放',.65);assert(moving.carX>before.carX+1);assert.equal(moving.records,0);pass('真实连续播放推进仿真车，无需人工位置记录');
  await page.locator('#monitorVideo').evaluate(v=>v.pause());await aligned('暂停');const stopped=await read();await page.waitForTimeout(650);const held=await read();assert(Math.abs(held.time-stopped.time)<.01);assert(Math.abs(held.carX-stopped.carX)<.04);pass('录像暂停后车辆保持对应位置');
  await seek(2);await aligned('倒退');assert((await read()).carX<stopped.carX);pass('暂停拖回前段时车辆同步回退');
  await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('#monitorVideo').evaluate(v=>v.play());await page.waitForFunction(()=>document.querySelector('#monitorVideo').currentTime>3,null,{timeout:12000});await aligned('折叠继续',.65);await page.locator('#monitorVideo').evaluate(v=>v.pause());pass('折叠疑似位置面板仍保持录像联动');
  await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('#evidenceFollowVideo').uncheck();const independent=(await read()).carX;await seek(5);await page.waitForTimeout(350);assert(Math.abs((await read()).carX-independent)<.04);pass('取消勾选后录像与仿真恢复独立控制');
  await page.locator('#evidenceFollowVideo').check();await aligned('重新开启');await page.locator('#monitorFile').setInputFiles(file(fixtures[1]));await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready&&TunnelVideoMonitor.evidenceIdentity().sourceId);await seek(2);await aligned('换源');assert.equal((await read()).records,0);pass('更换录像后无标注也能联动，新时长不会沿用旧视频');
  await page.locator('#evidenceFollowVideo').uncheck();await page.locator('[data-action=scene-run]').click();await page.waitForFunction(()=>document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().playing);await page.locator('[data-action=scene-run]').click();await page.waitForFunction(()=>!document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot().playing);pass('原有仿真启动与暂停仍可独立操作');
  await page.locator('#evidenceFollowVideo').check();await aligned('启动仿真后开启联动');await page.screenshot({path:path.join(out,'录像联动-无标注.png')});
  // 只使用浏览器虚拟摄像头，不访问用户的实际设备。
  await page.context().grantPermissions(['camera'],{origin:'http://127.0.0.1:8765'});
  const beforeCamera=(await read()).carX;await page.locator('[data-monitor-mode=camera]').click();await page.locator('[data-monitor-action=camera]').click();
  await page.waitForFunction(()=>document.querySelector('#monitorVideo').srcObject?.getVideoTracks().some(t=>t.readyState==='live')&&TunnelVideoMonitor.evidenceIdentity().ready);
  const cameraIdentity=await page.evaluate(()=>TunnelVideoMonitor.evidenceIdentity());assert.equal(cameraIdentity.timeSec,null);assert.equal(cameraIdentity.durationSec,null);await page.waitForTimeout(700);
  assert(Math.abs((await read()).carX-beforeCamera)<.04);pass('虚拟摄像头没有录像时长，不伪造车的位置进度');
  await page.locator('[data-monitor-action=stop]').click();await page.waitForFunction(()=>!TunnelVideoMonitor.evidenceIdentity().ready);await page.waitForTimeout(300);assert(Math.abs((await read()).carX-beforeCamera)<.04);pass('停止释放视频源后不会回写空进度或跳回起点');
  assert.deepEqual(errors,[]);pass('录像联动流程无页面或控制台异常');
  fs.writeFileSync(path.join(out,'video-follow-results.json'),JSON.stringify({checkedAt:new Date().toISOString(),checks,errors,samples,boundary:'真实隧道录像的连续播放与相对进度联动；不要求疑似位置记录，不做类型识别或实测里程结论；摄像头为浏览器虚拟设备。'},null,2));
  console.log('ALL '+checks.length+' VIDEO FOLLOW CHECKS PASSED');
 }catch(e){console.error('联动失败时状态',await read().catch(()=>null));throw e;}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
