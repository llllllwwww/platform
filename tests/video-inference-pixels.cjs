// 比较实际截图像素，覆盖 DOM 不变但持续动画仍改变画面的情况。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const output = path.join(__dirname, 'output');
const baseline = process.argv.includes('--baseline');
const reconstruction = process.argv.includes('--reconstruction');
const prefix = reconstruction ? 'reconstruction-pixels-stable' : baseline ? 'inference-pixels-before' : 'inference-pixels-stable';
fs.mkdirSync(output, { recursive: true });
const compare = (first, second, excluded) => JSON.parse(execFileSync(process.env.PIXEL_TEST_PYTHON || 'python', ['-c', `
from PIL import Image, ImageChops
import sys,json
first,second=Image.open(sys.argv[1]).convert('RGB'),Image.open(sys.argv[2]).convert('RGB')
assert first.size==second.size
channels=ImageChops.difference(first,second).split()
mask=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]).point(lambda n:255 if n else 0)
all_pixels=mask.histogram()[255]
excluded=json.loads(sys.argv[3])
if excluded: mask.paste(0,tuple(excluded))
print(json.dumps({'changedPixels':all_pixels,'outsideInferencePixels':mask.histogram()[255],'bounds':mask.getbbox()}))
`, first, second, JSON.stringify(excluded)], { windowsHide: true, encoding: 'utf8' }));

(async () => {
  const server = http.createServer((req, res) => {
    const requested = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    const file = path.resolve(root, '.' + requested);
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    fs.readFile(file, (error, body) => {
      if (error) { res.writeHead(404).end(); return; }
      const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
      res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' }); res.end(body);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let polls = 0;
    let job = { id: 'pixel-test', batchId: 'B202609', taskId: 'T-001', filename: 'tunnel.mp4', status: 'running', progress: { phase: '运行 crack-seg U-Net', percent: 40, frames: 0, totalFrames: 8, candidates: 0 }, result: null, error: null };
    let reconstructionJob = { id: 'pixel-test', sourceJobId: 'pixel-test', runId: 'r-pixels', batchId: 'B202609', taskId: 'T-001', filename: 'tunnel.mp4', status: 'running', maxFrames: 24, progress: { phase: '恢复相机位姿与稀疏地标', percent: 40, registeredFrames: 3, sparsePoints: 80, localizedCandidates: 0 }, result: null };
    if (reconstruction) job = { ...job, status: 'completed', progress: { ...job.progress, percent: 100 }, result: { frameCount: 8, candidateCount: 0, preview: [], jsonUrl: '/api/video-inference/file/pixel-test/review.json' } };
    await page.route(/\/api\/video-reconstruction\/start$/, route => route.fulfill({ status: 202, json: reconstructionJob }));
    await page.route(/\/api\/video-reconstruction\/status\?/, route => { polls++; return route.fulfill({ json: reconstructionJob }); });
    await page.route(/\/api\/video-inference\/start$/, route => route.fulfill({ json: job }));
    await page.route(/\/api\/video-inference\/status\?/, route => { polls++; return route.fulfill({ json: job }); });
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html#processing`);
    await page.locator('#currentImageAlgorithm').waitFor();
    await page.evaluate(() => window.TunnelVideoInference.start(new File(['pixel test'], 'tunnel.mp4', { type: 'video/mp4' }), { batchId: 'B202609', taskId: 'T-001' }));
    if (reconstruction) await page.locator('[data-reconstruction-field="start"]').click();
    await page.waitForTimeout(reconstruction ? 3600 : 1200);
    const animations = await page.evaluate(() => document.getAnimations().map(animation => ({ name: animation.animationName, state: animation.playState, iterations: String(animation.effect.getTiming().iterations), target: animation.effect.target?.id || animation.effect.target?.className })));
    const rect = await page.locator(reconstruction ? '#videoReconstructionStatus' : '#videoInferenceStatus').boundingBox();
    const excluded = [Math.floor(rect.x), Math.floor(rect.y), Math.ceil(rect.x + rect.width), Math.ceil(rect.y + rect.height)];
    const shots = [];
    for (let n = 0; n < 3; n++) {
      const file = path.join(output, `${prefix}-${n}.png`);
      await page.screenshot({ path: file, fullPage: true, animations: 'allow' }); shots.push(file);
      if (n < 2) await page.waitForTimeout(430);
    }
    const unchanged = [compare(shots[0], shots[1], excluded), compare(shots[1], shots[2], excluded)];
    if (!baseline) {
      assert.equal(await page.locator('#frontendVersion').textContent(), '界面版本 2026.10.08.6');
      assert.equal(animations.filter(animation => animation.state === 'running').length, 0);
      for (const frame of unchanged) assert.equal(frame.changedPixels, 0, JSON.stringify(frame));
    }
    if (reconstruction) reconstructionJob = { ...reconstructionJob, progress: { ...reconstructionJob.progress, percent: 52, registeredFrames: 5, sparsePoints: 120 } };
    else job = { ...job, progress: { phase: '第 2 帧已完成', percent: 52, frames: 2, totalFrames: 8, candidates: 4 } };
    await page.waitForFunction(isReconstruction => document.querySelector(isReconstruction ? '[data-reconstruction-field="percent"]' : '[data-inference-field="percent"]').textContent === '52%', reconstruction);
    await page.waitForTimeout(350);
    const changedFile = path.join(output, `${prefix}-progress.png`);
    await page.screenshot({ path: changedFile, fullPage: true, animations: 'allow' });
    const changed = compare(shots[2], changedFile, excluded);
    if (!baseline) {
      assert(changed.changedPixels > 0);
      assert.equal(changed.outsideInferencePixels, 0, JSON.stringify(changed));
      const downloadPromise = page.waitForEvent('download');
      await page.locator('[data-display-diagnostics="export"]').click();
      const download = await downloadPromise;
      const diagnosticFile = path.join(output, reconstruction ? 'reconstruction-display-diagnostics-test.json' : 'display-diagnostics-test.json');
      await download.saveAs(diagnosticFile);
      const diagnostic = JSON.parse(fs.readFileSync(diagnosticFile, 'utf8'));
      assert.equal(diagnostic.version, '2026.10.08.6');
      assert.equal(diagnostic.route, 'processing');
      assert(reconstruction ? diagnostic.events.filter(e => e.kind === 'reconstruction-poll').length >= 3 : diagnostic.polls >= 3);
      assert.equal(diagnostic.renders.length, 1);
    }
    assert.deepEqual(errors, []);
    const report = { baseline, reconstruction, polls, animations, unchanged, changed, errors };
    fs.writeFileSync(path.join(output, `${prefix}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    console.log(baseline ? 'BASELINE captured' : 'PASS 相同状态全页像素不变；有效进度只改变对应状态区；显示诊断可导出');
  } finally {
    if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
