/* 用真实 HTTP / WebSocket 测试数据接入；不模拟现场雷达设备。 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const out=path.resolve(__dirname,'output');fs.mkdirSync(out,{recursive:true});
(async()=>{
  let seq=0,invalidPoll=false,requestCount=0,openSockets=0,wsMode='valid';
  const sockets=new Set();
  const fixture=(n=1)=>({schemaVersion:1,name:'自动化接口测试帧（非实测）',source:'本机生成测试数据，不代表现场雷达',frameId:'FRAME-'+n,
    batchId:'B202609',taskId:'T-001',lineId:'TEST-L01',capturedAt:'2026-10-04T00:00:00.000Z',
    matrix:[[1,n,0,-1],[2,-n,3,4],[0,1,n,-3]],metadata:{traceSpacingM:.02,sampleIntervalNs:.1,timeWindowNs:.2,epsilon:6,amplitudeUnit:'接口测试振幅'}});
  const server=http.createServer((req,res)=>{
    requestCount++;
    if(req.url!='/no-cors')res.setHeader('Access-Control-Allow-Origin','http://127.0.0.1:8765');
    res.setHeader('Cache-Control','no-store');res.setHeader('Content-Type','application/json');
    if(req.url==='/large'){res.setHeader('Content-Length',5*1024*1024+1);res.end('{}');return;}
    if(req.url==='/mismatch'){res.end(JSON.stringify({...fixture(),batchId:'B202607'}));return;}
    if(req.url==='/http-error'){res.writeHead(503);res.end('{}');return;}
    if(req.url==='/slow'){setTimeout(()=>{if(!res.destroyed)res.end(JSON.stringify(fixture(99)));},800);return;}
    if(req.url==='/fixed' || req.url==='/no-cors'){const data=fixture(700);if(requestCount%2)data.metadata=Object.fromEntries(Object.entries(data.metadata).reverse());res.end(JSON.stringify(data));return;}
    res.end(JSON.stringify(invalidPoll?{schemaVersion:1,matrix:[[1,2],[3,'invalid']]}:fixture(++seq)));
  });
  const packet=(text,opcode=1)=>{
    const body=Buffer.from(text),header=body.length<126?Buffer.from([0x80|opcode,body.length]):Buffer.from([0x80|opcode,126,body.length>>8,body.length&255]);
    return Buffer.concat([header,body]);
  };
  server.on('upgrade',(req,socket)=>{
    const key=req.headers['sec-websocket-key'];
    const accept=crypto.createHash('sha1').update(key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n');
    sockets.add(socket);openSockets++;
    const interval=setInterval(()=>{if(!socket.destroyed)socket.write(packet(JSON.stringify(fixture(++seq)),wsMode==='binary'?2:1));},260);
    socket.on('data',data=>{if((data[0]&15)===8)socket.end(Buffer.from([0x88,0]));});
    socket.on('error',()=>{});
    socket.on('close',()=>{sockets.delete(socket);openSockets--;clearInterval(interval);});
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const endpoint='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const checks=[],errors=[];
  const pass=(name,data)=>{checks.push({name,...data});console.log('PASS',name,data?JSON.stringify(data):'');};
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true});
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:8765/index.html#radar');await page.waitForSelector('#radarAcquisition');
    const snapshot=()=>page.evaluate(()=>SLZJ.snapshot());
    const importedCount=async()=> (await snapshot()).radarData.length;
    const selectNetwork=async()=>{await page.locator('[data-radar-mode=network]').click();};
    const connect=async(url,transport='http')=>{
      await selectNetwork();await page.locator('#radarTransport').selectOption(transport);await page.locator('#radarEndpoint').fill(url);await page.locator('#radarInterval').fill('1').catch(()=>{});
      await page.locator('#radarLiveForm button[type=submit]').click();
    };
    const waitFrame=()=>page.waitForFunction(()=>!document.querySelector('[data-radar-action=save]').disabled);
    const downloadJSON=async()=>{
      const pending=page.waitForEvent('download');await page.locator('[data-radar-action=json]').click();const d=await pending;
      return JSON.parse(fs.readFileSync(await d.path(),'utf8'));
    };
    assert.deepEqual(await page.locator('.panel-head h2').allTextContents(),['雷达采集 · 外部数据','雷达信号 · 仿真演示']);
    assert.equal(await page.locator('#radarInputEmpty').isVisible(),true);
    assert.equal(await page.locator('#rawRadar').isVisible(),true);assert.equal(await importedCount(),0);
    pass('默认外部区为空，仿真示例独立保留，不伪造接入成功');
    const initial=await snapshot();
    await page.locator('#trace').fill('0');await page.locator('#trace').dispatchEvent('change');
    const demoImage=await page.locator('#rawRadar').evaluate(c=>c.toDataURL());
    await page.locator('#csvFile').setInputFiles({name:'device.dzt',mimeType:'application/octet-stream',buffer:Buffer.from('not a csv')});
    await page.locator('[data-action=import-csv]').click();await page.waitForFunction(()=>document.querySelector('#importMessage').textContent.includes('暂不支持'));
    assert.equal(await importedCount(),0);pass('专有设备文件不按CSV伪解析');
    await page.locator('#csvFile').setInputFiles({name:'invalid.csv',mimeType:'text/csv',buffer:Buffer.from('1,2\n3,no')});
    await page.locator('[data-action=import-csv]').click();await page.waitForFunction(()=>document.querySelector('#importMessage').textContent.includes('不是有效数值'));
    assert.equal(await importedCount(),0);pass('非法文件被拒绝，未写入目录');
    await page.locator('#csvFile').setInputFiles(path.resolve('examples/radar_sample.csv'));
    await page.locator('#metadataFile').setInputFiles(path.resolve('examples/radar_metadata.json'));
    await page.waitForFunction(()=>document.querySelector('#metadata').value.includes('traceSpacingM'));
    await page.locator('[data-action=import-csv]').click();await page.waitForFunction(()=>SLZJ.snapshot().radarData.length===1);
    assert.equal(await page.locator('#externalRawRadar').isVisible(),true);
    assert.equal((await snapshot()).radarData[0].rows,32);assert.match(await page.locator('#radarInputMetadata').textContent(),/非实测/);
    assert.equal(await page.locator('#rawRadar').evaluate(c=>c.toDataURL()),demoImage);
    pass('原CSV与元数据导入可用，外部来源如实标记，仿真图保持独立');
    const extOriginal=await page.locator('#externalRawRadar').evaluate(c=>c.toDataURL());
    await page.locator('#externalPalette').selectOption('2');
    assert.notEqual(await page.locator('#externalRawRadar').evaluate(c=>c.toDataURL()),extOriginal);
    assert.equal(await page.locator('#rawRadar').evaluate(c=>c.toDataURL()),demoImage);
    await page.locator('#externalZoom').selectOption('2');assert.equal(await page.locator('#externalRawRadar').evaluate(c=>c.style.width),'200%');
    await page.locator('#externalTrace').fill('20');await page.locator('#externalTrace').dispatchEvent('change');assert.match(await page.locator('#externalTraceLabel').textContent(),/#20/);
    pass('外部图增益、缩放与单道切换有效且不影响示意控件');
    await page.locator('#csvFile').setInputFiles({name:'frame.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixture(8)))});
    await page.locator('[data-action=import-csv]').click();await page.waitForFunction(()=>SLZJ.snapshot().radarData.length===2);
    let exported=await downloadJSON();assert.deepEqual(exported.matrix,fixture(8).matrix);assert.equal(exported.taskId,'T-001');assert.equal(exported.provenance.transport,'file-json');
    pass('JSON帧文件导入与完整矩阵、来源、任务和时间导出正确');
    await page.locator('#csvFile').setInputFiles({name:'bad-calibration.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...fixture(),metadata:{sampleIntervalNs:.1,timeWindowNs:99}}))});
    await page.locator('[data-action=import-csv]').click();await page.waitForFunction(()=>document.querySelector('#importMessage').textContent.includes('时间窗与采样间隔不一致'));
    assert.equal(await importedCount(),2);pass('JSON标定冲突不会覆盖已选数据');
    await connect(endpoint+'/poll');await waitFrame();
    assert.equal(await importedCount(),2);exported=await downloadJSON();assert.equal(exported.provenance.transport,'http');assert.equal(exported.source,fixture().source);
    const firstFrame=exported.frameId;
    await page.waitForTimeout(1200);exported=await downloadJSON();assert.notEqual(exported.frameId,firstFrame);
    assert.equal(await importedCount(),2);assert.deepEqual((await snapshot()).defects,initial.defects);assert.deepEqual((await snapshot()).assessment,initial.assessment);
    pass('HTTP网关帧实际连续接收，预览不自动入库或改变病害与SHI');
    await page.locator('[data-radar-action=save]').click();await page.waitForFunction(()=>SLZJ.snapshot().radarData.length===3);
    const saved=(await snapshot()).radarData[2];assert.equal(saved.provenance.transport,'http');assert.equal(saved.batchId,'B202609');assert.equal(saved.taskId,'T-001');
    assert.equal(await page.locator('#radarSource').inputValue(),'2');
    await page.locator('[data-action=go-processing]').first().click();await page.waitForURL(/#processing/);
    const beforeRequests=requestCount;await page.waitForTimeout(1200);assert.equal(requestCount,beforeRequests);
    await page.locator('[data-action=run-preprocess]').first().click();await page.waitForFunction(()=>SLZJ.snapshot().processing.status==='completed');
    assert.equal((await snapshot()).processing.inputId,saved.id);assert.equal(await page.locator('#processedRadar').isVisible(),true);
    pass('手动快照写入目录并送入实际预处理，离开雷达页停止轮询');
    await page.goto('http://127.0.0.1:8765/index.html#radar');await page.reload();await page.waitForSelector('#radarAcquisition');
    assert.equal((await snapshot()).radarData.length,3);assert.equal(await page.locator('#radarReceiveStatus').textContent(),'未连接');
    pass('刷新只恢复已保存数据目录，不自动连接外部设备');
    await connect(endpoint+'/fixed');await waitFrame();const fixed=await downloadJSON();
    await page.waitForFunction(()=>document.querySelector('#radarReceiveStatus').textContent==='等待新帧');
    exported=await downloadJSON();assert.equal(exported.receivedAt,fixed.receivedAt);assert.equal(exported.connection.acceptedFrames,1);assert(exported.connection.repeatedFrames>=1);
    pass('重复轮询帧不伪装成新采集，保留原接收时间');
    await page.locator('#radarEndpoint').fill('rtsp://device/radar');await page.locator('#radarLiveForm button[type=submit]').click();
    assert.match(await page.locator('#radarFeedback').textContent(),/HTTP/);assert.equal((await downloadJSON()).frameId,fixed.frameId);
    pass('无效地址在切换前拒绝，已有合法预览不会丢失');
    await page.locator('[data-radar-action=stop]').click();const stoppedRequests=requestCount;await page.waitForTimeout(1200);assert.equal(requestCount,stoppedRequests);
    assert.equal(await page.locator('#externalRawRadar').isVisible(),true);pass('手动停止实际关闭轮询，最后帧保留供取证');
    invalidPoll=false;await connect(endpoint+'/poll');await waitFrame();const validFrame=(await downloadJSON()).frameId;invalidPoll=true;
    await page.waitForFunction(()=>document.querySelector('#radarReceiveStatus').textContent==='接收失败');
    exported=await downloadJSON();assert.equal(exported.frameId,validFrame);assert.equal(await importedCount(),3);
    pass('后续非法帧明确失败，最后有效帧及目录不被覆盖');invalidPoll=false;
    for(const [route,message] of [['/mismatch','关联不一致'],['/large','5 MB'],['/http-error','503'],['/no-cors','跨域']]){
      await connect(endpoint+route);await page.waitForFunction(()=>document.querySelector('#radarReceiveStatus').textContent==='接收失败');
      assert.match(await page.locator('#radarFeedback').textContent(),new RegExp(message));assert.equal(await importedCount(),3);
      pass('网络异常明确拒绝且无自动归档',{route});
    }
    await connect(endpoint+'/slow');await page.locator('#batch').selectOption('B202607');await page.waitForTimeout(1100);
    assert.equal(await importedCount(),0);assert.equal(await page.locator('#radarInputEmpty').isVisible(),true);assert.equal(await page.locator('[data-radar-action=reconnect]').isDisabled(),true);
    pass('请求未完成时切换批次会取消旧来源，延迟响应不串用');
    await page.locator('[data-radar-mode=file]').click();
    await page.locator('#csvFile').setInputFiles({name:'late-file.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...fixture(98),batchId:'B202607',taskId:'T-B202607'}))});
    await page.evaluate(()=>{
      window.originalFileText=File.prototype.text;
      File.prototype.text=async function(){window.fileReadStarted=true;const text=await window.originalFileText.call(this);await new Promise(r=>setTimeout(r,800));return text;};
    });
    await page.locator('[data-action=import-csv]').click();await page.waitForFunction(()=>window.fileReadStarted);
    await page.locator('#batch').selectOption('B202609');
    await page.waitForFunction(()=>document.querySelector('#importMessage').textContent.includes('读取期间变更'));
    assert.equal(await importedCount(),3);
    await page.evaluate(()=>{File.prototype.text=window.originalFileText;});
    pass('文件读取期间切换批次也不会把旧文件关联到新任务');
    await connect(endpoint.replace('http:','ws:')+'/socket','websocket');await waitFrame();
    exported=await downloadJSON();assert.equal(exported.provenance.transport,'websocket');assert.equal(await importedCount(),3);assert.equal(openSockets,1);
    pass('浏览器真实WebSocket文本帧接入与JSON校验可用');
    await page.locator('[data-radar-action=save]').click();await page.waitForFunction(()=>SLZJ.snapshot().radarData.length===4);
    const archived=await downloadJSON();await page.waitForTimeout(400);const laterArchived=await downloadJSON();
    assert.equal(laterArchived.connection.acceptedFrames,archived.connection.acceptedFrames);assert.deepEqual(laterArchived.events,archived.events);
    assert.equal(await importedCount(),4);pass('WebSocket保存快照和接入记录独立，后续帧不改写目录');
    await page.locator('#radarPreview').selectOption('live');
    await page.evaluate(()=>{location.hash='tasks';});await page.waitForFunction(()=>document.querySelector('#videoMonitor'));
    await page.waitForTimeout(400);assert.equal(openSockets,0);
    await page.evaluate(()=>{location.hash='radar';});await page.waitForSelector('#radarAcquisition');
    assert.equal(await page.locator('#radarReceiveStatus').textContent(),'已停止');
    pass('离开雷达页关闭WebSocket，返回需要手动重连');
    wsMode='binary';await connect(endpoint.replace('http:','ws:')+'/socket','websocket');
    await page.waitForFunction(()=>document.querySelector('#radarReceiveStatus').textContent==='接收失败');assert.match(await page.locator('#radarFeedback').textContent(),/二进制/);assert.equal(await importedCount(),4);wsMode='valid';
    pass('二进制专有流不误解释成合法雷达矩阵');
    const contract=await page.evaluate(()=>{
      const rejected=[];const base={matrix:[[1,2],[3,4]]};
      for(const value of [ {...base,matrix:[[1,null],[3,4]]},{...base,matrix:[[1,2],[3]]},{...base,metadata:[]},{...base,capturedAt:'2026-10-04'},{...base,capturedAt:'2026-02-30T00:00:00Z'},{...base,frameId:{}},{...base,schemaVersion:2},{...base,batchId:'B202609',metadata:{batchId:'B202607'}} ]){
        try{TunnelRadarAcquisition.parseFrame(JSON.stringify(value),{batchId:'B202609',taskId:'T-001'});rejected.push(false);}catch{rejected.push(true);}
      }
      return rejected;
    });assert(contract.every(Boolean));pass('空值、非矩形、非法元数据、时间、帧编号和版本均拒绝');
    for(const width of [1024,768,390]){
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>document.documentElement.clientWidth),false);
      pass('雷达新页面无整页横向溢出',{width});
    }
    await page.setViewportSize({width:1440,height:1100});
    await page.locator('[data-radar-mode=file]').click();await page.locator('#radarSource').selectOption('3');
    await page.locator('#radarAcquisition').screenshot({path:path.join(out,'雷达外部数据区.png')});
    assert.deepEqual(errors,[]);pass('外部雷达流程无页面异常');
    fs.writeFileSync(path.join(out,'radar-acquisition-results.json'),JSON.stringify({checks,errors,boundary:'测试为本机生成的CSV/JSON与HTTP/WebSocket网关；未连接现场GPR设备，不验证工程检测精度。'},null,2));
    console.log(`ALL ${checks.length} RADAR CHECKS PASSED`);
  }finally{await browser.close();for(const socket of sockets)socket.destroy();server.closeAllConnections();await new Promise(r=>server.close(r));}
})().catch(error=>{console.error(error.stack || error);process.exit(1);});
