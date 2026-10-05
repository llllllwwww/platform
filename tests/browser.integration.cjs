const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs"),
  path = require("path"),
  assert = require("node:assert/strict");
const root = path.resolve(__dirname, ".."),
  out = path.join(__dirname, "output");
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.BROWSER_EXECUTABLE ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    acceptDownloads: true,
  });
  let errors = [],
    checks = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  const pass = (name, extra) => {
    checks.push({ name, ...extra });
    console.log("PASS", name, extra ? JSON.stringify(extra) : "");
  };
  const go = async (r) => {
    await page.goto("http://127.0.0.1:8765/index.html#" + r);
    await page.waitForFunction(
      () =>
        document.querySelector("h1") &&
        !document.querySelector("h1").textContent.includes("无法计算"),
    );
  };
  const snap = () => page.evaluate(() => SLZJ.snapshot());
  const frame = async () => {
    await page.waitForFunction(
      () => document.querySelector("#twinFrame")?.contentWindow?.SLZJScene,
    );
    const f = page.frames().find((f) => f.url().includes("assets/tunnel-scene.html"));
    assert(f, "iframe missing");
    await f.waitForFunction(() => window.SLZJScene);
    await f.waitForFunction(
      () =>
        getComputedStyle(document.querySelector("#loading")).display === "none",
    );
    return f;
  };
  await go("overview");
  let f = await frame();
  await page.screenshot({
    path: path.join(out, "01-总览.png"),
    fullPage: true,
  });
  pass("首页和原有三维场景实际渲染");
  await go("tasks");
  await page.locator("#taskSpeed").fill("5");
  await page.locator("#taskObstacle").check();
  await page.locator("#obstacleX").fill("3");
  await page.locator("#taskForm button[type=submit]").click();
  f = await frame();
  await page.locator('[data-action="toggle-task"]').click();
  await page.waitForFunction(
    () => SLZJ.snapshot().tasks[0].status === "blocked",
  );
  f = await frame();
  const vehicle = await f.evaluate(() => SLZJScene.snapshot());
  assert(Math.abs(vehicle.carX - 1) < 0.01);
  assert(vehicle.obstacleVisible);
  assert(!vehicle.playing);
  pass("检测车按同尺寸停障计算停止，三维实际 X=1m", { carX: vehicle.carX });
  await page.locator('[data-action="reset-task"]').click();
  await page.locator("#taskObstacle").uncheck();
  await page.locator("#taskSpeed").fill("0.6");
  await page.locator("#taskForm button[type=submit]").click();
  await go("radar");
  await page
    .locator("#csvFile")
    .setInputFiles({
      name: "invalid.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("1,2\n3,no"),
    });
  await page.locator('[data-action="import-csv"]').click();
  await page.waitForFunction(() =>
    document
      .querySelector("#importMessage")
      .textContent.includes("不是有效数值"),
  );
  assert.equal((await snap()).radarData.length, 0);
  pass("非法文件被拒绝且未写入目录");
  await page
    .locator("#csvFile")
    .setInputFiles({
      name: "valid.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("1,2,3,4\n2,4,8,16\n3,4,3,2"),
    });
  await page
    .locator("#metadata")
    .fill(
      '{"traceSpacingM":0.02,"sampleIntervalNs":0.1,"timeWindowNs":0.2,"epsilon":6}',
    );
  await page.locator('[data-action="import-csv"]').click();
  await page.waitForFunction(() => SLZJ.snapshot().radarData.length === 1);
  assert.equal((await snap()).radarData[0].rows, 3);
  pass("CSV与JSON元数据真实解析");
  await go("processing");
  await page.locator("#radarSource").selectOption("0");
  await page.locator('[data-action="run-preprocess"]').first().click();
  await page.waitForFunction(
    () => SLZJ.snapshot().processing.status === "completed",
  );
  assert(await page.locator("#processedRadar").count());
  pass("数值预处理运行并显示对比矩阵", {
    version: (await snap()).processing.version,
  });
  assert.equal(await page.locator("#modelChoice").count(), 0);
  assert.match(await page.locator("#currentImageAlgorithm").textContent(), /crack-seg U-Net/);
  await page.locator('[data-action="try-model"]').click();
  assert.equal((await snap()).processing.status, "failed");
  assert.match((await snap()).processing.message, /RTM.*未接入/);
  pass("旧识别选择已移除，当前影像算法明确，RTM 未连接不会伪造结果");
  await go("defects");
  await page.locator('[data-action="select"][data-id="D-003"]').click();
  await page.waitForURL(/#twin/);
  f = await frame();
  await f.waitForFunction(() => SLZJScene.snapshot().selectedId === "D-003");
  let sc = await f.evaluate(() => SLZJScene.snapshot()),
    data = await snap(),
    d = data.defects.find((d) => d.id === "D-003"),
    p = sc.positions.find((x) => x.id === d.id);
  assert.equal(p.x, d.position.x);
  assert.equal(p.y, d.position.y);
  assert.equal(p.z, d.position.z);
  assert((await page.locator(".detail-id").textContent()).includes("D-003"));
  pass("列表→三维→详情→证据按编号与坐标联动");
  await page.locator('[data-filter="type"]').selectOption("water");
  f = await frame();
  sc = await f.evaluate(() => SLZJScene.snapshot());
  assert.deepEqual(
    sc.visibleIds.slice().sort(),
    (await snap()).defects.map((d) => d.id).sort(),
  );
  pass("筛选后3D可见对象与列表一致");
  await page.locator('[data-action="view"][data-view="side"]').click();
  await page.waitForTimeout(120);
  sc = await f.evaluate(() => SLZJScene.snapshot());
  const target = sc.projected.find((x) => x.id === "D-012");
  assert(target && target.z < 1);
  await f
    .locator("#stage canvas")
    .click({ position: { x: target.x, y: target.y } });
  await page.waitForFunction(() =>
    document.querySelector(".detail-id").textContent.includes("D-012"),
  );
  pass("点击实际三维对象更新父页面病害详情与雷达证据");

  await page.locator('[data-action="reset-filters"]').click();
  f = await frame();
  await page.locator('[data-action="review"][data-review="confirmed"]').click();
  await page.locator("#batch").selectOption("B202607");
  f = await frame();
  assert((await snap()).tasks.every((t) => t.batch === "B202607"));
  assert.equal((await snap()).radarData.length, 0);
  assert.equal((await snap()).processing.status, "idle");
  pass("历史批次隔离任务、导入和处理记录");
  await page.locator("#batch").selectOption("B202609");
  await go("health");
  let before = await snap();
  await page.locator("#alpha").fill("0");
  await page.locator("#alpha").dispatchEvent("change");
  let after = await snap();
  assert.notEqual(before.assessment.shi, after.assessment.shi);
  assert.notEqual(before.assessment.version, after.assessment.version);
  pass("评估权重改变SHI和版本", {
    before: before.assessment.shi,
    after: after.assessment.shi,
  });
  await page.locator('.ahp-cell[data-i="0"][data-j="1"]').fill("2");
  await page.locator('[data-action="apply-ahp"]').click();
  assert.equal((await snap()).evaluationConfig.ahp[1][0], 0.5);
  pass("AHP互反矩阵编辑与一致性检查");
  await go("alerts");
  let alert = (await snap()).alerts.find((a) => a.status === "new");
  assert(alert);
  for (const status of ["confirmed", "processing", "recheck"]) {
    await page
      .locator(`[data-action="advance-alert"][data-id="${alert.id}"]`)
      .click();
    assert.equal(
      (await snap()).alerts.find((x) => x.id === alert.id).status,
      status,
    );
  }
  await page
    .locator(`[data-action="advance-alert"][data-id="${alert.id}"]`)
    .click();
  await page
    .locator("#recheckNote")
    .fill("演示复检 RE-001：已完成专项复核，继续监测。");
  await page.locator("#recheckForm button").click();
  await page.reload();
  assert.equal(
    (await snap()).alerts.find((x) => x.id === alert.id).status,
    "closed",
  );
  pass("预警确认→处理→复检→关闭，刷新后保留记录");
  await go("simulation");
  await page.locator("#simulationForm button[type=submit]").click();
  let sim1 = (await snap()).simulation.sample.result.matrix;
  await page.locator("#noise").fill("0.7");
  await page.locator("#simulationForm button[type=submit]").click();
  let sim2 = (await snap()).simulation.sample.result.matrix;
  assert.notDeepEqual(sim1, sim2);
  pass("样本参数实际改变可复现信号");
  await page.locator('[data-tab="structure"]').click();
  await page.locator("#simulationForm button[type=submit]").click();
  let struct = (await snap()).simulation.structure.result;
  await page.locator("#load").fill("200");
  await page.locator("#simulationForm button[type=submit]").click();
  assert(
    Math.abs(
      (await snap()).simulation.structure.result.displacement -
        struct.displacement * 2,
    ) < 0.000002,
  );
  pass("结构响应随荷载成比例改变");
  await page.locator('[data-tab="plans"]').click();
  await page.locator("#budget").fill("5");
  await page.locator("#simulationForm button[type=submit]").click();
  assert(!(await snap()).simulation.plans.result.some((p) => p.recommended));
  await page.locator("#budget").fill("60");
  await page.locator("#simulationForm button[type=submit]").click();
  assert((await snap()).simulation.plans.result.some((p) => p.recommended));
  pass("预算约束改变方案推荐");
  await go("reports");
  await page.locator('[data-filter="type"]').selectOption("void");
  data = await snap();
  for (const [action, ext] of [
    ["export-csv", "csv"],
    ["export-json", "json"],
    ["export-html", "html"],
  ]) {
    const pending = page.waitForEvent("download");
    await page.locator(`[data-action="${action}"]`).click();
    const dl = await pending;
    const dest = path.join(out, dl.suggestedFilename());
    await dl.saveAs(dest);
    const content = fs.readFileSync(dest, "utf8");
    if (ext === "json") {
      const exp = JSON.parse(content);
      assert.equal(exp.defects.length, data.defects.length);
      assert.equal(exp.assessment.version, data.assessment.version);
    }
    if (ext === "csv")
      assert.equal(
        content.trim().split(/\r?\n/).length,
        data.defects.length + 1,
      );
    if (ext === "html") {
      assert(content.includes(data.assessment.version));
      assert(content.includes("data:image/png;base64"));
      assert(content.replace(/data:image\/png;base64,[A-Za-z0-9+/=]+/g, "[image]").length < 50000, "报告不应内嵌完整样本矩阵文本");
      const report = await browser.newPage();
      await report.goto("file:///" + dest.replace(/\\/g, "/"));
      assert(await report.locator("img").count());
      assert(
        await report
          .locator("img")
          .first()
          .evaluate((x) => x.complete && x.naturalWidth > 0),
      );
      await report.pdf({
        path: path.join(out, "报告打印验证.pdf"),
        format: "A4",
        printBackground: true,
      });
      await report.close();
    }
    pass("导出 " + ext.toUpperCase() + " 内容与当前筛选/评估版本一致");
  }
  await page.locator('[data-action="reset-filters"]').click();
  await go("twin");
  f = await frame();
  await page.screenshot({
    path: path.join(out, "02-三维孪生.png"),
    fullPage: true,
  });
  await go("health");
  await page.screenshot({
    path: path.join(out, "03-健康评估.png"),
    fullPage: true,
  });
  for (const width of [1024, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await go("overview");
    f = await frame();
    const over = await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    );
    assert(!over, "页面出现横向溢出 " + width);
    await page.screenshot({
      path: path.join(out, "布局-" + width + ".png"),
      fullPage: true,
    });
    pass("响应布局无整页横向溢出 " + width);
  }
  // 验证升级旧缓存时只清理已移除的识别选择，保留真实输入和已有业务状态。
  const legacyCache = await page.evaluate(() => {
    const key = Object.keys(localStorage).find((name) => {
      try { return JSON.parse(localStorage.getItem(name)).schemaVersion === 2; }
      catch { return false; }
    });
    if (!key) throw Error("缺少可升级的工作台缓存");
    const saved = JSON.parse(localStorage.getItem(key)),
      preserved = Object.fromEntries(["defects", "tasks", "radars", "evidenceRecords", "scenePrefs", "reviewsByBatch"]
        .filter((name) => Object.prototype.hasOwnProperty.call(saved, name))
        .map((name) => [name, saved[name]]));
    saved.processing.model = "retired-selection";
    saved.processing.status = "failed";
    saved.processing.message = "算法 retired-selection 未接入";
    saved.processingByBatch = saved.processingByBatch || {};
    saved.processingByBatch.B202607 = { status: "failed", model: "retired-selection", message: "算法 retired-selection 未接入", version: "keep-version", gain: 3.5 };
    localStorage.setItem(key, JSON.stringify(saved));
    return { key, preserved };
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await go("processing");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("#currentImageAlgorithm");
  const upgradedCache = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), legacyCache.key);
  assert.equal(Object.hasOwn(upgradedCache.processing, "model"), false);
  assert.equal(Object.hasOwn(upgradedCache.processingByBatch.B202607, "model"), false);
  assert.equal(upgradedCache.processingByBatch.B202607.version, "keep-version");
  assert.equal(upgradedCache.processingByBatch.B202607.gain, 3.5);
  assert.equal((await page.locator("body").textContent()).includes("retired-selection"), false);
  for (const [name, value] of Object.entries(legacyCache.preserved)) assert.deepEqual(upgradedCache[name], value, "缓存升级改变了 " + name);
  pass("旧识别选择跨批次清理，病害、任务、矩阵、对应记录和场景设置保持完整");
  assert.equal(errors.length, 0, errors.join("\n"));
  pass("全流程无页面异常或控制台错误");
  fs.writeFileSync(
    path.join(out, "integration-results.json"),
    JSON.stringify(
      {
        time: new Date().toISOString(),
        browser: "Microsoft Edge / isolated headless test",
        checks,
        errors,
      },
      null,
      2,
    ),
  );
  await browser.close();
  console.log("ALL", checks.length, "PASSED");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
