// 可选的界面验收：从真实隧道原片制作 320×180 临时副本，独立服务跑 8 帧 CPU 任务；不评价原分辨率精度 / 性能。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const root = path.resolve(__dirname, '..');
const video = path.join(root, 'assets/videos/multiscene/rail-tunnel-short.mp4');
const output = path.join(__dirname, 'output');

(async () => {
  assert(fs.existsSync(video), '先准备 assets/videos/multiscene/rail-tunnel-short.mp4');
  fs.mkdirSync(output, { recursive: true });
  const reservation = net.createServer();
  await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  const python = process.env.VIDEO_INFERENCE_PYTHON || 'python';
  const fixtureDirectory = fs.mkdtempSync(path.join(output, 'video-inference-live-fixture-'));
  const fixtureVideo = path.join(fixtureDirectory, 'tunnel-ui-320.webm');
  const prepare = `import cv2, sys
cv2.setNumThreads(2)
cap = cv2.VideoCapture(sys.argv[1])
assert cap.isOpened(), 'Cannot decode source tunnel video'
fps = cap.get(cv2.CAP_PROP_FPS) or 30
writer = cv2.VideoWriter(sys.argv[2], cv2.VideoWriter_fourcc(*'VP80'), fps, (320, 180))
assert writer.isOpened(), 'Cannot encode VP8 UI fixture'
frames = 0
while True:
    ok, frame = cap.read()
    if not ok: break
    writer.write(cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA))
    frames += 1
writer.release()
cap.release()
assert frames > 0, 'No source frames'
print('INFO tunnel UI fixture: 320x180, ' + str(frames) + ' original frames')`;
  try {
    execFileSync(python, ['-c', prepare, video, fixtureVideo], { windowsHide: true, stdio: ['ignore', 'inherit', 'inherit'] });
  } catch (error) {
    const actual = fs.realpathSync(fixtureDirectory);
    if (path.dirname(actual) !== fs.realpathSync(output)) throw new Error('拒绝清理越界临时副本目录');
    fs.rmSync(actual, { recursive: true, force: true });
    throw error;
  }
  const child = spawn(python, ['启动平台.py', '--port', String(port), '--no-browser'], {
    cwd: root, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, stdio: ['ignore', 'pipe', 'pipe']
  });
  let log = '', childError = null, browser = null, jobId = null;
  child.on('error', error => { childError = error; });
  child.stdout.on('data', chunk => { log = (log + chunk.toString()).slice(-12000); });
  child.stderr.on('data', chunk => { log = (log + chunk.toString()).slice(-12000); });
  const exited = new Promise(resolve => child.once('exit', resolve));
  const base = `http://127.0.0.1:${port}`;
  try {
    let healthy = false;
    for (let attempt = 0; attempt < 80; attempt++) {
      if (childError) throw childError;
      if (child.exitCode !== null) throw new Error('启动器提前退出：' + log);
      try {
        healthy = (await fetch(base + '/api/video-inference/health', { signal: AbortSignal.timeout(1000) })).ok;
      } catch {}
      if (healthy) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert(healthy, '本地推理服务未就绪：' + log);
    browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [], statusResponses = [];
    let previousProgress = ''; 
    const pendingResponses = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (response.url().includes('/api/video-inference/status?')) {
        pendingResponses.push(response.json().then(job => {
          statusResponses.push({ status: job.status, ...job.progress });
          const summary = `${job.status}: ${job.progress?.phase} (${job.progress?.frames || 0}/${job.progress?.totalFrames || 0})`;
          if (summary !== previousProgress) { console.log('INFO', summary); previousProgress = summary; }
        }).catch(() => {}));
      }
    });
    await page.goto(base + '/index.html#tasks');
    await page.locator('#monitorFile').waitFor();
    const started = page.waitForResponse(response => response.url().endsWith('/api/video-inference/start'));
    await page.locator('#monitorFile').setInputFiles(fixtureVideo);
    const startResponse = await started;
    assert.equal(startResponse.status(), 202);
    const initial = await startResponse.json();
    jobId = initial.id;
    assert(/^[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$/.test(jobId));
    await page.locator('#navigation a[href="#processing"]').click();
    await page.locator('#videoInferenceStatus.is-running').waitFor();
    await page.locator('#gain').fill('4.3');
    await page.locator('#gain').focus();
    await page.evaluate(() => {
      window.scrollTo({ top: 180, behavior: 'instant' });
      const selectors = ['#currentImageAlgorithm', '#videoInferenceStatus', '#rawRadar', '#gain', '#background', '#radarSource', '#navigation a', '#batch option'];
      window.__liveRefs = selectors.map(selector => [selector, document.querySelector(selector)]);
      window.__liveScroll = scrollY;
      window.__liveStats = { samples: 0, opacityDrops: 0, outsideChanges: [], nodeReplacements: 0, focusLosses: 0, valueLosses: 0, scrollChanges: 0 };
      const observer = new MutationObserver(records => {
        for (const record of records) {
          const element = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
          if (!element.closest('#videoInferenceStatus')) window.__liveStats.outsideChanges.push(element.id || element.nodeName);
        }
      });
      observer.observe(document.querySelector('#page'), { subtree: true, childList: true, characterData: true, attributes: true });
      const sample = () => {
        const stats = window.__liveStats;
        stats.samples++;
        if (!window.__liveRefs.every(([selector, element]) => document.querySelector(selector) === element)) stats.nodeReplacements++;
        if (Array.from(document.querySelector('#page').children).some(element => Number(getComputedStyle(element).opacity) < 0.99)) stats.opacityDrops++;
        if (document.activeElement.id !== 'gain') stats.focusLosses++;
        if (document.querySelector('#gain').value !== '4.3') stats.valueLosses++;
        if (document.querySelector('#videoInferenceStatus').classList.contains('is-running') && scrollY !== window.__liveScroll) stats.scrollChanges++;
        window.__liveFrame = requestAnimationFrame(sample);
      };
      window.__liveFrame = requestAnimationFrame(sample);
    });
    await page.waitForFunction(() => ['completed', 'failed'].includes(window.TunnelVideoInference.current()?.status), null, { timeout: 180000 });
    const job = await page.evaluate(() => window.TunnelVideoInference.current());
    assert.equal(job.status, 'completed', job.error || log);
    assert.equal(job.result.frameCount, 8);
    assert.equal(job.detector.device, 'cpu');
    await page.locator('#videoInferenceStatus.is-complete').waitFor();
    assert.equal(await page.locator('[data-inference-field="percent"]').textContent(), '100%');
    assert.equal(await page.locator('[data-inference-field="json"]').isVisible(), true);
    const stats = await page.evaluate(() => { cancelAnimationFrame(window.__liveFrame); return window.__liveStats; });
    assert(stats.samples > 0);
    for (const field of ['opacityDrops', 'nodeReplacements', 'focusLosses', 'valueLosses', 'scrollChanges']) assert.equal(stats[field], 0, field + ' changed');
    assert.deepEqual(stats.outsideChanges, []);
    assert.deepEqual(errors, []);
    await Promise.all(pendingResponses);
    assert(statusResponses.length > 0);
    await page.screenshot({ path: path.join(output, 'video-inference-live-completed.png'), fullPage: true });
    await page.locator('[data-action="open-video-inference"]').first().click();
    await page.locator('#videoInferenceReview.is-complete').waitFor();
    assert.match(await page.locator('.inference-review-head p').textContent(), /tunnel-ui-320.webm/);
    if (job.result.candidateCount > 0) await page.locator('.inference-candidate-card img').first().waitFor();
    const report = {
      verifiedAt: new Date().toISOString(), video: 'assets/videos/multiscene/rail-tunnel-short.mp4',
      fixture: { width: 320, height: 180, sourceFramesPreserved: true, purpose: 'UI workflow verification only' },
      jobId, status: job.status, frames: job.result.frameCount, candidates: job.result.candidateCount,
      device: job.detector.device, statusRequests: statusResponses.length, browserErrors: errors,
      ui: stats, progress: statusResponses, testJobCleanedAfterVerification: true
    };
    fs.writeFileSync(path.join(output, 'video-inference-live.json'), JSON.stringify(report, null, 2));
    console.log('PASS 真实隧道视频的 320×180 副本 → Python 8 帧 CPU 推理 → 智能处理 → 候选复核');
    console.log('PASS 持续采样：0 整页透明度下降 / 0 节点重建 / 0 输入焦点或滚动丢失');
    console.log(JSON.stringify({ frames: report.frames, candidates: report.candidates, statusRequests: report.statusRequests, visualSamples: stats.samples }));
  } catch (error) {
    console.error(log.split('\n').filter(line => !line.includes('GET /api/video-inference/status')).slice(-8).join('\n'));
    throw error;
  } finally {
    if (browser) await browser.close();
    if (child.exitCode === null && !childError) { child.kill(); await exited; }
    // 仅删除本测试的独立服务刚创建的任务，拒绝递归删除任何其他路径。
    const actualFixture = fs.realpathSync(fixtureDirectory);
    if (path.dirname(actualFixture) !== fs.realpathSync(output)) throw new Error('拒绝清理越界临时副本目录');
    fs.rmSync(actualFixture, { recursive: true, force: true });
    if (jobId) {
      const runtime = path.resolve(root, 'runtime/video_inference');
      const taskDirectory = path.resolve(runtime, jobId);
      if (path.dirname(taskDirectory) !== runtime) throw new Error('拒绝清理越界任务目录');
      if (fs.existsSync(taskDirectory)) {
        const actual = fs.realpathSync(taskDirectory), actualRoot = fs.realpathSync(runtime);
        if (path.dirname(actual) !== actualRoot) throw new Error('拒绝清理越界实际目录');
        fs.rmSync(actual, { recursive: true, force: true });
      }
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
