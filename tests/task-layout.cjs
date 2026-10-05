/* 同屏工作区验收：真实录像标注、折叠状态与媒体生命周期、原有场景控制和响应式。 */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const out = path.join(__dirname, 'output');
const videoFile = path.join(out, 'public-videos', 'cracks.mp4');
if (!fs.existsSync(videoFile)) throw new Error('请先运行 python tests/download_public_videos.py 准备公开视频。');
fs.mkdirSync(out, {recursive:true});
const near = (a,b,eps=1) => assert(Math.abs(a-b)<=eps,`${a} 与 ${b} 相差超过 ${eps}`);
(async()=>{
  const browser = await chromium.launch({headless:true,
    executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    args:['--use-angle=swiftshader','--enable-unsafe-swiftshader','--use-fake-device-for-media-stream']});
  const checks=[], errors=[];
  const pass = (name,data) => {checks.push({name,...data});console.log('PASS',name,data?JSON.stringify(data):'');};
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
    await context.grantPermissions(['camera'],{origin:'http://127.0.0.1:8765'});
    const page = await context.newPage();
    page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR',e.message);});
    await page.goto('http://127.0.0.1:8765/index.html#tasks');
    await page.waitForFunction(()=>document.querySelector('#twinFrame')?.contentWindow?.SLZJScene,null,{timeout:60000});
    const frame=page.frames().find(f=>f.url().includes('tunnel-scene.html'));
    await frame.locator('#loading').waitFor({state:'hidden'});
    const scene=()=>frame.evaluate(()=>SLZJScene.snapshot());
    const state=()=>page.evaluate(()=>SLZJ.snapshot());
    const initial=await state();
    assert.equal(await page.locator('#taskEquipmentDrawer').getAttribute('open'),null);
    assert.equal(await page.locator('#taskEvidenceDrawer').getAttribute('open'),null);
    assert.equal(await page.locator('#taskCaptureVideo').isDisabled(),true);
    assert.equal(await page.locator('.equipment-card').count(),6);
    pass('初始工作区聚焦双画面，六类设备和标注控件完整保留');
    const geometry=()=>page.evaluate(()=>({
      video:document.querySelector('#monitorScreen').getBoundingClientRect().toJSON(),
      twin:document.querySelector('#twinFrame').getBoundingClientRect().toJSON(),
      scroll:document.documentElement.scrollWidth,width:innerWidth,
      columns:getComputedStyle(document.querySelector('#taskForm .form-grid')).gridTemplateColumns.split(' ').length
    }));
    for(const width of [1440,1280,1024]) {
      await page.setViewportSize({width,height:1000});await page.evaluate(()=>window.scrollTo(0,0));
      const g=await geometry();near(g.video.y,g.twin.y);near(g.video.height,g.twin.height);
      assert(g.twin.x>=g.video.right+10);assert(g.scroll<=g.width+2);assert.equal(g.columns,4);
      pass('桌面左右等高对齐且参数完整：'+width,{height:g.video.height});
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#monitorFile').setInputFiles(videoFile);
    await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready&&TunnelVideoMonitor.evidenceIdentity().sourceId?.startsWith('VF-'));
    await page.locator('#monitorVideo').evaluate(v=>{v.pause();v.currentTime=7;});
    await page.waitForFunction(()=>{const v=document.querySelector('#monitorVideo');return !v.seeking&&Math.abs(v.currentTime-7)<.02;});
    await page.evaluate(()=>{window.layoutMedia=document.querySelector('#monitorVideo');window.layoutFrame=document.querySelector('#twinFrame');});
    assert.equal(await page.locator('#taskCaptureVideo').isEnabled(),true);
    pass('公开真实录像解码后启用顶部冻结标注入口');
    await page.locator('[data-action=roam]').click();await frame.waitForFunction(()=>SLZJScene.snapshot().firstPerson);
    await page.locator('#taskEquipmentDrawer > summary').click();
    await page.locator('[data-action=equipment-select][data-equipment=radar]').click();
    assert.equal(await page.locator('[data-equipment=radar]').getAttribute('aria-pressed'),'true');
    await page.locator('#taskEquipmentDrawer > summary').click();
    await page.locator('.monitor-details > summary').click();
    assert.equal(await page.locator('#monitorResolution').textContent(),'1080 × 1920');
    await page.locator('.monitor-details > summary').click();
    await page.locator('[data-action=task-evidence]').click();
    await page.locator('#taskEvidenceDrawer > summary').click();
    assert(await page.evaluate(()=>document.querySelector('#monitorVideo')===layoutMedia&&document.querySelector('#twinFrame')===layoutFrame));
    near(await page.locator('#monitorVideo').evaluate(v=>v.currentTime),7,.02);
    assert.equal((await scene()).firstPerson,true);
    pass('设备、来源说明及标注面板折叠不重建视频和场景，第一人称及录像时间保留');
    await page.locator('#taskCaptureVideo').click();
    await page.waitForFunction(()=>document.querySelector('#evidenceFrame').dataset.frameReady==='true');
    assert.equal(await page.locator('#taskEvidenceDrawer').getAttribute('open'),'');
    const frozen=await page.locator('#evidenceFrame').evaluate(el=>({width:el.width,height:el.height}));
    assert(frozen.width>0&&frozen.height>0);
    pass('顶部按钮冻结实际视频帧并展开左侧标注区',{frame:frozen});
    await page.locator('#evidenceSceneMode').selectOption('evidence');
    const rect=await page.locator('#evidenceFrame').boundingBox();
    await page.locator('#evidenceFrame').click({position:{x:rect.width*.48,y:rect.height*.69}});
    await page.locator('#evidenceType').selectOption('crack');await page.locator('#evidenceSave').click();
    await page.waitForFunction(()=>SLZJ.snapshot().evidenceRecords.length===1);
    const record=(await state()).evidenceRecords[0];
    await frame.waitForFunction(id=>SLZJScene.snapshot().visibleIds.includes(id),record.id);
    const p=(await scene()).positions.find(p=>p.id===record.id);
    near(p.x,record.position.x,1e-8);near(p.y,record.position.y,1e-8);near(p.z,record.position.z,1e-8);
    assert.equal(p.type,'crack');assert.equal(await page.locator('#taskEvidenceCount').textContent(),'1 条记录');
    assert.equal(page.frames().find(f=>f.url().includes('tunnel-scene.html')),frame);
    assert.equal((await scene()).firstPerson,true);assert.deepEqual((await state()).defects,initial.defects);
    pass('左侧标注即时映射至右侧同一类型和XYZ，不重建场景或改写原台账');
    await page.locator('#evidenceSave').scrollIntoViewIfNeeded();
    const sticky=await page.locator('.task-simulation-pane').boundingBox();
    assert(sticky.y>=70&&sticky.y<100);
    const twinBox=await page.locator('#twinFrame').boundingBox();assert(twinBox.y+twinBox.height<1000);
    pass('滚动左侧标注时右侧仿真持续留在可视区',{top:sticky.y});
    await page.screenshot({path:path.join(out,'任务模块-标注与仿真同屏.png')});
    await page.locator('#evidenceFollowVideo').check();
    await page.locator('#monitorVideo').evaluate(v=>{v.currentTime=10;});
    await page.waitForFunction(()=>{const v=document.querySelector('#monitorVideo');const s=document.querySelector('#twinFrame').contentWindow.SLZJScene.snapshot();return !v.seeking&&Math.abs(s.carX-48*v.currentTime/v.duration)<.04;});
    await page.locator('#evidenceFollowVideo').uncheck();
    pass('新布局中录像进度仍驱动车辆相对位置');
    await page.locator('[data-action=scene-run]').click();await frame.waitForFunction(()=>SLZJScene.snapshot().playing);
    await page.locator('[data-action=scene-run]').click();await frame.waitForFunction(()=>!SLZJScene.snapshot().playing);
    const pauseX=(await scene()).carX;await page.waitForTimeout(200);near((await scene()).carX,pauseX,1e-8);
    await frame.locator('#stage canvas').press('f');await frame.waitForFunction(()=>!SLZJScene.snapshot().firstPerson);
    await page.locator('[data-action=fly]').click();await frame.waitForFunction(()=>SLZJScene.snapshot().freeFly);
    await frame.locator('#stage canvas').press('g');await frame.waitForFunction(()=>!SLZJScene.snapshot().freeFly);
    pass('左右布局保留作业启动暂停、第一人称及F/G场景快捷键');
    await page.locator('.scene-settings > summary').click();
    await page.locator('[data-layer=city]').uncheck();await frame.waitForFunction(()=>!SLZJScene.snapshot().layers.city);
    await page.locator('[data-layer=city]').check();await frame.waitForFunction(()=>SLZJScene.snapshot().layers.city);
    const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
    await page.locator('#backgroundImage').setInputFiles({name:'布局背景验收.png',mimeType:'image/png',buffer:png});
    await frame.waitForFunction(()=>SLZJScene.snapshot().backgroundImage);
    await page.locator('[data-action=restore-background]').click();await frame.waitForFunction(()=>!SLZJScene.snapshot().backgroundImage);
    await page.locator('.scene-settings > summary').click();
    pass('右侧展开场景工具仍可操作图层和本地背景图片');
    await page.locator('[data-monitor-action=fullscreen]').click();await page.waitForFunction(()=>document.fullscreenElement?.id==='monitorScreen');
    const full=await page.locator('#monitorScreen').boundingBox();near(full.width,1440);near(full.height,1000);
    await page.evaluate(()=>document.exitFullscreen());await page.waitForFunction(()=>!document.fullscreenElement);
    await page.locator('[data-action=fullscreen-scene]').click();await page.waitForFunction(()=>!!document.fullscreenElement);
    await page.locator('[data-action=fullscreen-scene]').click();await page.waitForFunction(()=>!document.fullscreenElement);
    pass('视频与仿真可独立进入和退出全屏');
    await page.locator('#taskEvidenceDrawer > summary').click();await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(out,'任务模块-桌面左右对照.png'),fullPage:true});
    await page.locator('[data-monitor-mode=camera]').click();await page.locator('[data-monitor-action=camera]').click();
    await page.waitForFunction(()=>document.querySelector('#monitorVideo').srcObject?.getVideoTracks()[0]?.readyState==='live');
    const track=await page.locator('#monitorVideo').evaluate(v=>v.srcObject.getVideoTracks()[0].id);
    await page.locator('#taskEquipmentDrawer > summary').click();await page.locator('#taskEquipmentDrawer > summary').click();
    await page.locator('#taskEvidenceDrawer > summary').click();await page.locator('#taskEvidenceDrawer > summary').click();
    assert.equal(await page.locator('#monitorVideo').evaluate(v=>v.srcObject.getVideoTracks()[0].id),track);
    assert.equal(await page.locator('#monitorVideo').evaluate(v=>v.srcObject.getVideoTracks()[0].readyState),'live');
    await page.locator('[data-monitor-action=stop]').click();
    pass('折叠面板不打断摄像头会话（自动化虚拟设备）');
    await page.locator('#monitorFile').setInputFiles(videoFile);
    await page.waitForFunction(()=>TunnelVideoMonitor.evidenceIdentity().ready&&TunnelVideoMonitor.evidenceIdentity().sourceId?.startsWith('VF-'));
    await page.locator('#monitorVideo').evaluate(v=>{v.pause();v.currentTime=7;});
    await page.waitForFunction(()=>!document.querySelector('#monitorVideo').seeking);
    for(const width of [768,390]) {
      await page.setViewportSize({width,height:1000});await page.evaluate(()=>window.scrollTo(0,0));
      const g=await geometry();near(g.video.x,g.twin.x);assert(g.twin.y>g.video.bottom);assert(g.scroll<=g.width+2);
      assert.equal(g.columns,width<768?1:2);
      pass('窄屏恢复单列且无整页横向溢出：'+width);
    }
    await page.screenshot({path:path.join(out,'任务模块-手机布局.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('[data-action=task-parameters]').click();
    await page.locator('#taskSpeed').fill('0.75');await page.locator('#taskObstacle').check();
    await page.locator('#taskForm button[type=submit]').click();
    const applied=await state();assert.equal(applied.tasks.find(t=>t.id===applied.evidenceContext.taskId).speed,.75);
    assert.equal(await page.locator('#taskObstacle').isChecked(),true);
    assert.equal(await page.locator('#taskForm .field').count(),8);
    assert.equal(await page.locator('.task-parameter-metrics .mini-stat').count(),5);
    pass('下方全宽作业参数表单仍可应用速度与障碍设置');
    assert.deepEqual(errors,[]);pass('完整同屏操作与响应式流程无页面异常');
    fs.writeFileSync(path.join(out,'task-layout-results.json'),JSON.stringify({checkedAt:new Date().toISOString(),checks,errors,
      boundary:'仅布局和交互验收；公开视频病害位置采用人工相对映射，不评估工程识别精度，摄像头为浏览器虚拟设备。'},null,2));
    console.log('ALL '+checks.length+' TASK LAYOUT CHECKS PASSED');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
