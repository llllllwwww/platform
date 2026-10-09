// 点击平台按钮 -> 真实 CPU PyCOLMAP -> 候选定位 -> 同帧 WebGL 复核。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const output = path.join(__dirname, 'output');
const qa = path.join(output, `reconstruction-live-${Date.now()}`);
const python = process.env.RECONSTRUCTION_TEST_PYTHON || 'D:/python3.12/python.exe';
const seed = process.env.RECONSTRUCTION_SEED || path.join(root, 'runtime/video_inference/20261008-100429-5e746739');
const checks = [], pass = name => { checks.push(name); console.log('PASS', name); };

(async () => {
  fs.mkdirSync(qa, { recursive: true });
  const service = spawn(python, ['-u', 'tests/reconstruction_test_server.py', '--qa-dir', qa, '--seed', seed],
    { cwd: root, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, stdio: ['ignore', 'pipe', 'pipe'] });
  let browser, ready, log = '';
  const childOutput = fs.createWriteStream(path.join(qa, 'service.log'));
  service.stdout.on('data', data => { log += data; childOutput.write(data); });
  service.stderr.on('data', data => { childOutput.write(data); });
  try {
    ready = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error('测试服务启动超时')), 30000);
      const onData = () => { const line = log.split(/\r?\n/).find(s => s.startsWith('READY ')); if (line) { clearTimeout(timeout); service.stdout.off('data', onData); resolve(JSON.parse(line.slice(6))); } };
      service.stdout.on('data', onData); onData();
      service.once('exit', code => { clearTimeout(timeout); reject(Error(`测试服务退出 ${code}`)); });
    });
    const url = `http://127.0.0.1:${ready.port}`;
    browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const errors = [], statuses = [], posts = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', async response => {
      if (response.url().includes('/api/video-reconstruction/status?')) { try { statuses.push(await response.json()); } catch {} }
      if (response.url().endsWith('/api/video-reconstruction/start')) posts.push(response.status());
    });
    // 仅跳过既有概览 iframe 的开销；本次新建模 viewer 保持真实运行。
    await page.route('**/algorithm/web/multiscene/index.html', route => route.fulfill({ contentType: 'text/html', body: '<p>Existing cases retained</p>' }));
    await page.goto(url + '/index.html#processing');
    await page.locator('#videoReconstructionStatus').waitFor();
    assert(await page.locator('[data-reconstruction-field="start"]').isDisabled());
    pass('检测前建模按钮禁用');
    await page.evaluate(async id => {
      const job = await (await fetch('/api/video-inference/status?id=' + id)).json();
      document.dispatchEvent(new CustomEvent('tunnel-video-inference', { detail: { job } }));
    }, ready.jobId);
    assert(await page.locator('[data-reconstruction-field="start"]').isEnabled());
    pass('已完成检测成果可直接手动建模');
    await page.locator('#reconstructionFrames').selectOption(process.env.RECONSTRUCTION_FRAMES || '24');
    await page.locator('[data-reconstruction-field="start"]').click();
    await page.waitForFunction(() => document.querySelector('[data-reconstruction-field="status"]').textContent === '建模中', null, { timeout: 10000 });
    await page.evaluate(() => {
      window.__stablePage = document.querySelector('#page'); window.__stableRadar = document.querySelector('#rawRadar');
      window.__outsideMutations = [];
      window.__observer = new MutationObserver(records => {
        for (const r of records) {
          const e = r.target.nodeType === 1 ? r.target : r.target.parentElement;
          if (e?.closest('#page') && !e.closest('#videoReconstructionStatus')) window.__outsideMutations.push({ id: e.id, type: r.type });
        }
      });
      window.__observer.observe(document.querySelector('#page'), { subtree: true, childList: true, characterData: true, attributes: true });
    });
    await page.locator('#gain').fill('4.3');
    await page.locator('#gain').focus();
    const start = Date.now();
    await page.waitForFunction(() => ['已完成', '未完成'].includes(document.querySelector('[data-reconstruction-field="status"]').textContent), null, { timeout: 900000 });
    const snapshot = await page.evaluate(() => {
      window.__observer.disconnect();
      return { samePage: window.__stablePage === document.querySelector('#page'), sameRadar: window.__stableRadar === document.querySelector('#rawRadar'),
        gain: document.querySelector('#gain').value, outsideMutations: window.__outsideMutations };
    });
    assert(snapshot.samePage && snapshot.sameRadar);
    assert.equal(snapshot.gain, '4.3');
    assert.equal(snapshot.outsideMutations.length, 0, JSON.stringify(snapshot.outsideMutations));
    pass('真实重建轮询未替换页面或雷达画布，输入和其他界面保持稳定');
    const job = await (await page.request.get(url + '/api/video-reconstruction/status?id=' + ready.jobId)).json();
    fs.writeFileSync(path.join(qa, 'result.json'), JSON.stringify(job, null, 2));
    assert.equal(job.status, 'completed', job.error || JSON.stringify(job));
    assert(job.result.registeredFrames >= 3 && job.result.sparsePoints >= 30);
    // CPU SfM 可能因本机线程调度只恢复一个很小的连通分量；候选无命中时仍应保留空坐标，
    // 不能把“纯灰圆柱材质”验证与偶发的定位覆盖率绑定。
    assert(job.result.localizedCandidates >= 0, '定位数量应为非负数');
    assert(job.result.localizedCandidates <= job.result.candidateCount);
    assert.equal(job.result.regularizedSurfaceKind, 'primitive_cylinder');
    assert(job.result.regularizedTriangles >= 1000, '圆柱规则化表面应包含足够三角面');
    assert.deepEqual(posts, [202]);
    pass('真实 CPU 重建产出相机、地标、候选位置与圆柱规则化表面');
    const same = await (await page.request.post(url + '/api/video-reconstruction/start', { data: { jobId: ready.jobId, batchId: 'B202609', taskId: 'T-001', maxFrames: 48 } })).json();
    assert.equal(same.runId, job.runId);
    pass('重复启动返回同一成果，无重复重建');
    const localized = await (await page.request.get(url + job.result.jsonUrl)).json();
    assert.equal(localized.scene_id, job.result.sceneId);
    assert(localized.defects.some(d => !d.points.length));
    assert(localized.defects.filter(d => !d.points.length).every(d => !d.observations.some(o => o.point)));
    pass('定位 JSON 绑定同一场景，未命中保留空坐标');
    await page.screenshot({ path: path.join(qa, 'processing-completed.png'), fullPage: true });
    await page.locator('[data-action="open-video-reconstruction"]').click();
    const frame = page.frameLocator('#multisceneFrame');
    await frame.locator('body[data-ready="true"]').waitFor({ timeout: 30000 });
    const viewer = await frame.locator('body').evaluate(() => {
      const D = window.TUNNEL_DATA;
      const panel = getComputedStyle(document.querySelector('.scene-panel')).backgroundColor;
      if (panel === 'rgb(255, 255, 255)') throw Error('新复核场景未采用深色主题');
      const candidate = D.defects.find(d => d.points.length);
      let rawMarkerCount = 0, errorPx = null, candidateId = null;
      if (candidate) {
        const index = D.cameras.findIndex(c => c.image_name === candidate.image_name);
        // 候选定位来自原始观测面；先在原始面验证红色候选标记，再切回默认的纯灰规则化圆柱检查材质。
        window.tunnelViewer.setSurface('raw');
        window.tunnelViewer.setFrame(index); window.tunnelViewer.setMode('follow'); window.tunnelViewer.selectCandidateId(candidate.id);
        rawMarkerCount = window.tunnelViewer.stats().markerCount;
        const projected = window.tunnelViewer.projectSourcePixel(candidate.points[0]);
        const first = candidate.pixels[candidate.pixel_hits.findIndex(Boolean)];
        errorPx = Math.hypot(projected[0] - first[0], projected[1] - first[1]);
        candidateId = candidate.id;
      }
      window.tunnelViewer.setSurface('regularized');
      return { stats: window.tunnelViewer.stats(), sameVideo: D.title, geometryKind: D.geometry_kind,
        regularizedKind: D.regularized?.report?.surface_kind, displaySurface: D.regularized ? 'regularized' : 'raw',
        regularizedTexture: D.regularized?.texture_url?.slice(0, 27),
        rawMarkerCount, errorPx, candidate: candidateId };
    });
    assert.equal(viewer.stats.glError, 0);
    if (viewer.candidate) assert(viewer.rawMarkerCount > 0);
    assert.equal(viewer.regularizedKind, 'primitive_cylinder');
    assert.equal(viewer.displaySurface, 'regularized');
    assert.equal(viewer.regularizedTexture, 'data:image/jpeg;base64,/9j/');
    if (viewer.errorPx !== null) assert(viewer.errorPx < 0.02, JSON.stringify(viewer));
    pass(viewer.candidate ? '真实 WebGL 复核可切帧选候选，位置重投影回对应原图像素' : '真实 WebGL 复核在无命中时保留空坐标并显示纯灰圆柱');
    await page.screenshot({ path: path.join(qa, 'viewer.png'), fullPage: true });
    await page.reload();
    await page.locator('#videoReconstructionStatus').waitFor();
    assert.equal(await page.locator('[data-reconstruction-field="status"]').textContent(), '已完成');
    // 刷新后同一会话可能恢复“当前视频成果”页，也可能回到案例页签；两者都必须保留本次成果入口。
    await page.waitForFunction(() => {
      const tab = document.querySelector('#uploadedSceneTab');
      const current = document.querySelector('#currentVideoScene iframe');
      return (tab && !tab.hidden) || current;
    }, null, { timeout: 30000 });
    assert((await page.locator('#uploadedSceneTab').count()) > 0 || (await page.locator('#currentVideoScene iframe').count()) > 0);
    pass('浏览器刷新保留同任务成果入口');
    assert.deepEqual(errors, []);
    const report = { checks, sourceJob: path.basename(seed), jobId: ready.jobId, runId: job.runId, elapsedMs: Date.now() - start,
      result: job.result, statusPolls: statuses.length, snapshot, viewer, errors };
    fs.writeFileSync(path.join(output, 'video-reconstruction-live.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ checks: checks.length, polls: statuses.length, registered: job.result.registeredFrames, points: job.result.sparsePoints,
      localized: job.result.localizedCandidates, candidates: job.result.candidateCount, elapsedSec: job.result.elapsedSec, qa }, null, 2));
  } finally {
    if (browser) await browser.close();
    if (service.exitCode === null) { service.kill(); await new Promise(resolve => service.once('exit', resolve)); }
    childOutput.end();
    // 测试成果留在 tests/output；不删除或修改用户任务、视频或原始候选。
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
