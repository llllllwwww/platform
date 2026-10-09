// 验证真实 850 ms 状态轮询不会重建整页；API 使用固定响应，避免重复启动 Python 模型。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '..');
const checks = [];
const pass = name => { checks.push(name); console.log('PASS', name); };
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' };

(async () => {
  const server = http.createServer((req, res) => {
    let requested;
    try { requested = decodeURIComponent(new URL(req.url, 'http://localhost').pathname); }
    catch { res.writeHead(400).end(); return; }
    const file = path.resolve(root, '.' + requested);
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    fs.readFile(file, (error, body) => {
      if (error) { res.writeHead(404).end(); return; }
      res.writeHead(200, { 'Content-Type': (mime[path.extname(file)] || 'application/octet-stream') + '; charset=utf-8' });
      res.end(body);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(() => {
      window.__inferenceEvents = 0;
      window.__stateWrites = 0;
      document.addEventListener("tunnel-video-inference", () => { window.__inferenceEvents++; });
      const originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        if (key === "slzj-workbench-v2") window.__stateWrites++;
        return originalSetItem.call(this, key, value);
      };
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // 保留实际 iframe 元素，简化其内容，统计任何由轮询造成的重新加载。
    await page.route(/\/algorithm\/web\/multiscene\/.*\.html/, route => route.fulfill({
      contentType: 'text/html', body: '<!doctype html><title>复核场景测试</title><script>parent.__reviewLoads=(parent.__reviewLoads||0)+1</script>'
    }));
    let polls = 0;
    let job = {
      id: 'ui-poll-1', batchId: 'B202609', taskId: 'T-001', taskName: '首轮衬砌普查',
      filename: 'polling-test.mp4', status: 'running',
      progress: { phase: '模型推理', percent: 12.5, frames: 1, totalFrames: 8, candidates: 2 },
      result: null, error: null
    };
    await page.route(/\/api\/video-inference\/start$/, route => route.fulfill({
      json: { ...job, status: 'queued', progress: { ...job.progress, phase: '等待模型', percent: 0 } }
    }));
    await page.route(/\/api\/video-inference\/status\?/, route => {
      polls++;
      return route.fulfill({ json: job });
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base + '/index.html#processing');
    await page.locator('#gain').waitFor();
    assert.match(await page.locator('script[src*="assets/app.js"]').getAttribute('src'), /\?v=20261008-session-6$/);
    assert.match(await page.locator('script[src*="assets/video-inference.js"]').getAttribute('src'), /\?v=20261008-session-6$/);
    assert.equal(await page.locator('#frontendVersion').textContent(), '界面版本 2026.10.08.6');
    assert.equal(await page.locator('#page').getAttribute('data-route'), 'processing');
    assert.equal(await page.evaluate(() => Array.from(document.querySelector('#page').children).every(element => getComputedStyle(element).animationName === 'none')), true);
    pass('检测页禁用整页入场动画，脚本和可见版本标记确认新版资源');
    await page.locator('#gain').fill('4.3');
    await page.locator('#gain').focus();
    await page.evaluate(() => {
      const selectors = ['#currentImageAlgorithm', '#videoInferenceStatus', '#gain', '#background', '#rawRadar', '#radarSource', '#navigation .nav-item', '#batch option'];
      window.__stableRefs = selectors.map(selector => [selector, document.querySelector(selector)]);
      window.__pageMutations = [];
      window.__outsideInference = [];
      window.__uiObserver = new MutationObserver(records => {
        for (const record of records) {
          const element = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
          window.__pageMutations.push(record.type);
          if (!element.closest('#videoInferenceStatus, #videoInferenceReview')) window.__outsideInference.push(element.id || element.nodeName);
        }
      });
      window.__uiObserver.observe(document.querySelector('#page'), { subtree: true, childList: true, characterData: true, attributes: true });
      window.scrollTo({ top: Math.min(180, document.documentElement.scrollHeight - innerHeight), behavior: "instant" });
      window.__scrollBefore = scrollY;
      window.__rawPixels = document.querySelector('#rawRadar').toDataURL();
    });
    await page.evaluate(() => window.TunnelVideoInference.start(new File(['test'], 'polling-test.mp4', { type: 'video/mp4' }), { batchId: 'B202609', taskId: 'T-001' }));
    await page.waitForFunction(() => document.querySelector('.inference-stats b')?.textContent === '模型推理');
    await page.waitForFunction(() => document.querySelector('.inference-status-head .badge')?.textContent === '13%');
    // 首次从空态显示任务时内容高度会变化；从运行态开始检查每次状态轮询。
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
      window.__scrollBefore = scrollY;
      resolve();
    }))));
    const initialPolls = polls;
    while (polls < initialPolls + 2) await page.waitForTimeout(100);
    let stable = await page.evaluate(() => ({
      identities: window.__stableRefs.every(([selector, element]) => document.querySelector(selector) === element),
      focused: document.activeElement.id, value: document.querySelector('#gain').value,
      scrollStable: scrollY === window.__scrollBefore,
      canvasStable: document.querySelector('#rawRadar').toDataURL() === window.__rawPixels,
      outside: window.__outsideInference
    }));
    assert.equal(stable.identities, true);
    assert.equal(stable.focused, 'gain');
    assert.equal(stable.value, '4.3');
    assert.equal(stable.scrollStable, true);
    assert.equal(stable.canvasStable, true);
    assert.deepEqual(stable.outside, []);
    pass('真实连续轮询保留整页、雷达画布、输入值、焦点和滚动位置');

    const unchangedBaseline = await page.evaluate(() => ({
      events: window.__inferenceEvents, writes: window.__stateWrites,
      footer: document.querySelector('#saveStatus').textContent
    }));
    await page.evaluate(() => { window.__pageMutations.length = 0; });
    const unchangedPolls = polls;
    while (polls < unchangedPolls + 2) await page.waitForTimeout(100);
    assert.deepEqual(await page.evaluate(() => window.__pageMutations), []);
    pass('相同状态连续返回时不产生推理卡片 DOM 变更');
    assert.deepEqual(await page.evaluate(() => ({
      events: window.__inferenceEvents, writes: window.__stateWrites,
      footer: document.querySelector('#saveStatus').textContent
    })), unchangedBaseline);
    pass('相同状态不发重复事件、不写本地存储、不更新页脚');

    job = { ...job, log: ['日志持续追加但进度未变化'] };
    const logOnlyPolls = polls;
    while (polls < logOnlyPolls + 2) await page.waitForTimeout(100);
    assert.deepEqual(await page.evaluate(() => ({
      events: window.__inferenceEvents, writes: window.__stateWrites,
      footer: document.querySelector('#saveStatus').textContent
    })), unchangedBaseline);
    assert.deepEqual(await page.evaluate(() => window.__pageMutations), []);
    assert.deepEqual(await page.evaluate(() => window.TunnelVideoInference.current().log), job.log);
    pass('日志单独变化保留最新快照，界面和存储仍不重复刷新');

    job = { ...job, progress: { phase: '正在推理第 3 帧', percent: 37.5, frames: 3, totalFrames: 8, candidates: 6 } };
    await page.waitForFunction(() => document.querySelector('.inference-status-head .badge')?.textContent === '38%');
    assert.match(await page.locator('#videoInferenceStatus .inference-stats').innerText(), /3 \/ 8/);
    assert.equal(await page.locator('#videoInferenceStatus .inference-stats b').nth(2).textContent(), '6');
    assert.equal(await page.evaluate(() => window.__stableRefs.every(([selector, element]) => document.querySelector(selector) === element)), true);
    assert.deepEqual(await page.evaluate(() => window.__outsideInference), []);
    pass('进度、代表帧和候选计数原位更新');
    assert.equal(await page.locator('#saveStatus').textContent(), unchangedBaseline.footer);
    pass('进度变化只更新固定字段，页脚不随帧刷新');

    await page.evaluate(() => {
      const current = window.TunnelVideoInference.current();
      document.dispatchEvent(new CustomEvent('tunnel-video-inference', { detail: { job: { ...current, id: 'other-task', taskId: 'T-OTHER', filename: 'other.mp4' } } }));
    });
    assert.match(await page.locator('.inference-status-head small').textContent(), /polling-test.mp4/);
    pass('其他任务状态不会覆盖当前任务的卡片');

    await page.evaluate(() => { window.__uiObserver.disconnect(); location.hash = 'defects'; });
    await page.locator('#currentVideoScene').waitFor({ state: 'attached' });
    assert.equal(await page.locator('#multisceneFrame').count(), 0);
    assert.equal(await page.evaluate(() => (window.__reviewLoads || 0)), 0);
    await page.evaluate(() => {
      window.__reviewRef = document.querySelector('#videoInferenceReview');
      window.__frameRef = document.querySelector('#currentVideoScene');
      window.__tableRef = document.querySelector('#page table');
      window.__statusRef = document.querySelector('#videoInferenceStatus');
      window.__reviewOutside = [];
      window.__reviewObserver = new MutationObserver(records => {
        for (const record of records) {
          const element = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
          if (!element.closest('#videoInferenceReview, #videoReconstructionStatus')) window.__reviewOutside.push(element.id || element.nodeName);
        }
      });
      window.__reviewObserver.observe(document.querySelector('#page'), { subtree: true, childList: true, characterData: true, attributes: true });
    });
    job = { ...job, progress: { ...job.progress, phase: '正在推理第 6 帧', percent: 75, frames: 6, candidates: 7 } };
    await page.waitForFunction(() => document.querySelector('.inference-status-head .badge')?.textContent === '75%');
    assert.equal(await page.evaluate(() => document.querySelector('#videoInferenceStatus') === window.__statusRef), true);
    pass('候选复核页的运行进度保持现有节点');

    job = { ...job, status: 'completed', progress: { phase: '完成', percent: 100, frames: 8, totalFrames: 8, candidates: 7 },
      detector: { device: 'cpu', threshold: 0.7 }, result: { frameCount: 8, candidateCount: 7, jsonUrl: '/api/video-inference/file/ui-poll-1/review.json', preview: [
        { id: 'C-001', imageName: 'frame_000003.png', timeSec: 2.5, confidence: 0.91, overlayUrl: '/api/video-inference/file/ui-poll-1/detect/overlay/frame_000003.png' }
      ] } };
    await page.locator('#videoInferenceReview.is-complete .inference-candidate-card').waitFor();
    assert.equal(await page.locator('.inference-candidate-card b').textContent(), 'C-001');
    assert.equal(await page.locator('.inference-review-actions a').getAttribute('href'), '/api/video-inference/file/ui-poll-1/review.json');
    stable = await page.evaluate(() => ({
      review: document.querySelector('#videoInferenceReview') === window.__reviewRef,
      frame: document.querySelector('#currentVideoScene') === window.__frameRef,
      table: document.querySelector('#page table') === window.__tableRef,
      loads: window.__reviewLoads || 0, outside: window.__reviewOutside,
      stored: JSON.parse(localStorage.getItem('slzj-workbench-v2')).videoInferenceJobs['B202609::T-001'].status
    }));
    assert.deepEqual(stable, { review: true, frame: true, table: true, loads: 0, outside: [], stored: 'completed' });
    pass('完成时显示候选图和 JSON 并开放手动建模，本视频容器不重载且不显示演示台账');

    await page.evaluate(() => {
      window.__toastChanges = 0;
      window.__toastObserver = new MutationObserver(records => { window.__toastChanges += records.length; });
      window.__toastObserver.observe(document.querySelector('#toast'), { subtree: true, childList: true, attributes: true, characterData: true });
      const current = window.TunnelVideoInference.current();
      document.dispatchEvent(new CustomEvent('tunnel-video-inference', { detail: { job: current } }));
      document.dispatchEvent(new CustomEvent('tunnel-video-inference', { detail: { job: current } }));
    });
    assert.equal(await page.evaluate(() => window.__toastChanges), 0);
    pass('重复完成状态不会重播完成提示或重建候选图');

    job = { ...job, id: 'ui-poll-2', status: 'running', filename: 'failed-test.mp4', result: null, error: null,
      progress: { phase: '准备模型', percent: 5, frames: 0, totalFrames: 8, candidates: 0 } };
    await page.evaluate(() => window.TunnelVideoInference.start(new File(['test'], 'failed-test.mp4', { type: 'video/mp4' }), { batchId: 'B202609', taskId: 'T-001' }));
    await page.waitForFunction(() => document.querySelector('.inference-status-head small')?.textContent === 'failed-test.mp4');
    job = { ...job, status: 'failed', error: '测试错误：模型不可用', progress: { ...job.progress, phase: '推理失败', percent: 100 } };
    await page.locator('#videoInferenceReview.is-failed .inference-error').waitFor();
    assert.match(await page.locator('.inference-error').textContent(), /模型不可用/);
    assert.equal(await page.evaluate(() => document.querySelector('#currentVideoScene') === window.__frameRef && (window.__reviewLoads || 0) === 0), true);
    pass('换视频和失败提示及时更新，不回退为其他视频模型');

    await page.evaluate(() => { window.__reviewObserver.disconnect(); location.hash = 'processing'; });
    await page.locator('#videoInferenceStatus.is-failed').waitFor();
    assert.match(await page.locator('.inference-error').textContent(), /模型不可用/);
    assert.deepEqual(errors, []);
    pass('页面往返读取最新状态，无未捕获 JavaScript 错误');
    console.log(`Result: ${checks.length} checks passed; ${polls} real status requests; no Python inference was started.`);
  } finally {
    if (browser) await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

