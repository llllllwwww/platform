const base = process.env.TEST_BASE_URL || "http://127.0.0.1:8765";
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs"),
  path = require("path"),
  assert = require("node:assert/strict");
(async () => {
  const b = await chromium.launch({
    headless: true,
    executablePath:
      process.env.BROWSER_EXECUTABLE ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } }),
    errors = [],
    checks = [];
  p.on("pageerror", (e) => errors.push(e.message));
  const pass = (x) => {
    checks.push(x);
    console.log("PASS", x);
  };
  await p.goto(base + "/index.html#twin");
  await p.locator("#videoDatasetSelect").waitFor();
  await p.locator("#videoDatasetSelect").selectOption("__demo");
  await p.waitForFunction(
    () => document.querySelector("#twinFrame")?.contentWindow?.SLZJScene,
  );
  const f = p
      .frames()
      .find((f) => f.url().includes("assets/tunnel-scene.html")),
    sc = () => f.evaluate(() => SLZJScene.snapshot());
  await f.waitForFunction(() => SLZJScene.snapshot().visibleIds.includes("D-001"));
  assert((await sc()).layers.stars);
  assert((await sc()).layers.city);
  pass("星空、城市背景在新版默认保留");
  await p.locator('[data-action="roam"]').click();
  await f.waitForFunction(() => SLZJScene.snapshot().firstPerson);
  assert(await f.locator("#carHud").isVisible());
  pass("第一人称视角及原车载B-scan/HUD保留");
  assert.equal(await p.evaluate(() => document.activeElement.id), "twinFrame");
  await p.keyboard.press("Escape");
  await f.waitForFunction(() => !SLZJScene.snapshot().firstPerson);
  await p.locator('[data-action="roam"]').click();
  await f.waitForFunction(() => SLZJScene.snapshot().firstPerson);
  pass("模式按钮自动聚焦三维场景，Esc立即退出第一人称");
  await p.locator('[data-action="scene-run"]').click();
  await f.waitForFunction(() => SLZJScene.snapshot().playing);
  assert((await sc()).firstPerson);
  await p.waitForTimeout(350);
  await p.locator('[data-action="scene-run"]').click();
  await f.waitForFunction(() => !SLZJScene.snapshot().playing);
  assert((await sc()).firstPerson);
  const x = (await sc()).carX;
  await p.waitForTimeout(350);
  assert.equal((await sc()).carX, x);
  pass("第一人称内启动、暂停不重建场景，暂停后车辆坐标冻结");
  await f.locator("#stage canvas").press("p");
  await f.waitForFunction(() => SLZJScene.snapshot().playing);
  await f.locator("#stage canvas").press("p");
  await f.waitForFunction(() => !SLZJScene.snapshot().playing);
  assert((await sc()).firstPerson);
  pass("P快捷键与任务真实播放状态双向联动");
  await f.locator("#stage canvas").press("f");
  await f.waitForFunction(() => !SLZJScene.snapshot().firstPerson);
  await p.locator('[data-action="fly"]').click();
  await f.waitForFunction(() => SLZJScene.snapshot().freeFly);
  await f.locator("#stage canvas").press("g");
  await f.waitForFunction(() => !SLZJScene.snapshot().freeFly);
  pass("F/G快捷键与自由漫游保留");
  await p.locator('[data-action="roam"]').click();
  await f.waitForFunction(() => SLZJScene.snapshot().firstPerson);
  await p.locator('[data-action="fly"]').click();
  await f.waitForFunction(
    () => SLZJScene.snapshot().freeFly && !SLZJScene.snapshot().firstPerson,
  );
  await p.locator('[data-action="roam"]').click();
  await f.waitForFunction(
    () => SLZJScene.snapshot().firstPerson && !SLZJScene.snapshot().freeFly,
  );
  await p.locator('[data-action="roam"]').click();
  await f.waitForFunction(() => !SLZJScene.snapshot().firstPerson);
  pass("第一人称和自由漫游双向切换互斥且有效");
  for (const layer of [
    "stars",
    "city",
    "ground",
    "grout",
    "rebar",
    "profile",
    "car",
    "ray",
  ]) {
    await p.locator(`[data-layer="${layer}"]`).uncheck();
    await f.waitForFunction((k) => !SLZJScene.snapshot().layers[k], layer);
    await p.locator(`[data-layer="${layer}"]`).check();
    await f.waitForFunction((k) => SLZJScene.snapshot().layers[k], layer);
  }
  pass("8种原有背景/实体图层可独立关闭和恢复");
  for (const layer of ["ground", "grout", "rebar", "profile"])
    await p.locator(`[data-layer="${layer}"]`).uncheck();
  await p.locator('[data-action="rotate"]').click();
  await f.waitForFunction(() => SLZJScene.snapshot().autoRotate);
  await p.locator('[data-action="rotate"]').click();
  await f.waitForFunction(() => !SLZJScene.snapshot().autoRotate);
  const old = (await sc()).selectedId;
  await p.locator('[data-action="step-defect"][data-dir="1"]').click();
  await f.waitForFunction((id) => SLZJScene.snapshot().selectedId !== id, old);
  assert(
    (await p.locator("#historyComparison").innerText()).includes(
      (await sc()).selectedId,
    ),
  );
  pass("自动旋转和上一/下一病害控件保留，历史批次表同步选中对象");
  const ring = await p.locator("#ringSection").evaluate((el) => ({
    id: el.dataset.defectId,
    angle: Number(el.dataset.angle),
    depth: Number(el.dataset.depth),
    y: Number(el.querySelector(".ring-defect-marker").dataset.y),
    z: Number(el.querySelector(".ring-defect-marker").dataset.z),
  }));
  const selected = (await p.evaluate(() => SLZJ.snapshot())).defects.find(
    (d) => d.id === ring.id,
  );
  assert.equal(ring.id, (await sc()).selectedId);
  assert.equal(ring.angle, selected.angle);
  assert.equal(ring.depth, selected.depth);
  assert(Math.abs(ring.y - selected.position.y) < 1e-12);
  assert(Math.abs(ring.z - selected.position.z) < 1e-12);
  assert.notEqual(ring.id, old);
  pass("环位横断面随三维选中病害更新，角度/埋深/YZ与业务坐标一致");
  for (const view of ["in", "cross", "crown", "invert", "iso"])
    await p
      .locator(`[data-action="view"][data-view="${view}"]`)
      .first()
      .click();
  pass("洞内、横断面、拱顶、仰拱、全景视角入口保留");
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    "base64",
  );
  await p.locator("#backgroundImage").setInputFiles({
    name: "background.png",
    mimeType: "image/png",
    buffer: png,
  });
  await f.waitForFunction(() => SLZJScene.snapshot().backgroundImage);
  await p.locator('[data-action="restore-background"]').click();
  await f.waitForFunction(() => !SLZJScene.snapshot().backgroundImage);
  pass("本地背景图片可加载且能恢复原有背景");
  await p.locator('[data-action="fullscreen-scene"]').click();
  await p.waitForFunction(() => !!document.fullscreenElement);
  await p.locator('[data-action="fullscreen-scene"]').click();
  await p.waitForFunction(() => !document.fullscreenElement);
  pass("三维全屏进入与退出有效");
  await p.screenshot({
    path: path.join(__dirname, "output/04-保留原有场景组件.png"),
    fullPage: true,
  });
  const C = require("../assets/core.js"),
    defaultState = C.createState();
  async function checkHistory(config) {
    const values = await p
      .locator("#shiHistory [data-batch]")
      .evaluateAll((els) =>
        els.map((el) => ({
          id: el.dataset.batch,
          shi: Number(el.dataset.shi),
        })),
      );
    assert.equal(values.length, 2);
    for (const v of values)
      assert(
        Math.abs(v.shi - C.assess({ ...config, batch: v.id }).shi) < 1e-10,
      );
    const r = await p.locator("#shiHistoryRate").evaluate((el) => ({
      days: Number(el.dataset.days),
      delta: Number(el.dataset.delta),
      rate: Number(el.dataset.rate),
    }));
    const chronological = config.batches
      .slice()
      .sort((a, b) => a.date.localeCompare(b.date));
    const days =
      (Date.parse(chronological[1].date) - Date.parse(chronological[0].date)) /
      86400000;
    const delta =
      C.assess({ ...config, batch: chronological[1].id }).shi -
      C.assess({ ...config, batch: chronological[0].id }).shi;
    assert.equal(r.days, days);
    assert(Math.abs(r.delta - delta) < 1e-10);
    assert(Math.abs(r.rate - (delta / days) * 365) < 1e-10);
    return values;
  }
  const originalHistory = await checkHistory(defaultState);
  pass("SHI历史曲线复用两批次同口径评估，年化变化率按实际日期间隔计算");
  await p.goto(base + "/index.html#health");
  await p.locator("#videoDatasetSelect").waitFor();
  await p.locator("#videoDatasetSelect").selectOption("__demo");
  await p.locator("#alpha").fill("0");
  await p.locator("#alpha").dispatchEvent("change");
  await p.goto(base + "/index.html#twin");
  await p.locator("#videoDatasetSelect").selectOption("__demo");
  const changedHistory = await checkHistory({ ...defaultState, alpha: 0 });
  assert.notDeepEqual(originalHistory, changedHistory);
  pass("修改组合权重后历史两期SHI及变化率同步重算");
  await p.goto(base + "/index.html#tasks");
  await p.locator("#videoDatasetSelect").waitFor();
  await p.locator("#videoDatasetSelect").selectOption("__demo");
  await p.waitForFunction(
    () => document.querySelector("#twinFrame")?.contentWindow?.SLZJScene,
  );
  const taskFrame = p
    .frames()
    .find((f) => f.url().includes("assets/tunnel-scene.html"));
  await taskFrame.waitForFunction(() => SLZJScene.snapshot().selectedId);
  await p.locator('[data-action="roam"]').click();
  await taskFrame.waitForFunction(() => SLZJScene.snapshot().firstPerson);
  assert(await taskFrame.locator("#carHud").isVisible());
  await p.locator('[data-action="scene-run"]').click();
  await taskFrame.waitForFunction(() => SLZJScene.snapshot().playing);
  await p.locator('[data-action="scene-run"]').click();
  await taskFrame.waitForFunction(
    () => !SLZJScene.snapshot().playing && SLZJScene.snapshot().firstPerson,
  );
  await p.screenshot({
    path: path.join(__dirname, "output/05-检测采集第一人称.png"),
    fullPage: true,
  });
  assert.equal(
    await p.locator("[data-task-progress]").first().innerText(),
    await p.locator("#taskPercent").innerText(),
  );
  assert.equal(
    await p.locator("[data-task-status]").first().innerText(),
    "已暂停",
  );
  assert((await p.locator("#taskLog").innerText()).includes("暂停检测任务"));
  pass("检测采集模块直接使用第一人称、车载面板与启停，无需旧版页面");
  assert.equal(
    await p
      .locator('a[href*="legacy"],a[href*="隧道三维数字孪生平台.html"]')
      .count(),
    0,
  );
  assert(!fs.existsSync(path.join(__dirname, "../隧道三维数字孪生平台.html")));
  assert(!fs.existsSync(path.join(__dirname, "../legacy/原三维平台.html")));
  const direct = await b.newPage();
  await direct.goto(base + "/assets/tunnel-scene.html");
  await direct.waitForURL("**/index.html#twin");
  await direct.locator("#videoDatasetSelect").waitFor();
  await direct.locator("#videoDatasetSelect").selectOption("__demo");
  await direct.waitForFunction(
    () => document.querySelector("#twinFrame")?.contentWindow?.SLZJScene,
  );
  await direct.close();
  pass("仅提供新版工作台入口，内部场景独立访问回到三维模块");
  assert.equal(errors.length, 0, errors.join("\n"));
  pass("保留功能专项验收无页面异常");
  fs.writeFileSync(
    path.join(__dirname, "output/retained-components-results.json"),
    JSON.stringify({ date: new Date().toISOString(), checks, errors }, null, 2),
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
