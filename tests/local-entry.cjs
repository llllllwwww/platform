const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright"),
  path = require("path"),
  fs = require("fs"),
  assert = require("node:assert/strict");
(async () => {
  const b = await chromium.launch({
    headless: true,
    executablePath:
      process.env.BROWSER_EXECUTABLE ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  const p = await b.newPage(),
    errors = [];
  p.on("pageerror", (e) => errors.push(e.message));
  const root = path.resolve(__dirname, "..");
  await p.goto("file:///" + root.replace(/\\/g, "/") + "/index.html#twin");
  await p.waitForFunction(() => window.SLZJ);
  const f = p
    .frames()
    .find((f) => f.url().includes("assets/tunnel-scene.html"));
  await f.waitForFunction(
    () => window.SLZJScene && SLZJScene.snapshot().selectedId === "D-001",
  );
  assert.equal(
    (await f.evaluate(() => SLZJScene.snapshot())).visibleIds.length,
    16,
  );
  console.log("PASS 文件模式直接打开和双向消息同步");
  await p.goto("http://127.0.0.1:8765/index.html#radar");
  await p
    .locator("#csvFile")
    .setInputFiles(path.join(root, "examples/radar_sample.csv"));
  await p
    .locator("#metadataFile")
    .setInputFiles(path.join(root, "examples/radar_metadata.json"));
  await p.waitForFunction(() =>
    document.querySelector("#metadata").value.includes("traceSpacingM"),
  );
  await p.locator('[data-action="import-csv"]').click();
  await p.waitForFunction(() => SLZJ.snapshot().radarData.length === 1);
  assert.equal((await p.evaluate(() => SLZJ.snapshot())).radarData[0].rows, 32);
  console.log("PASS 交付CSV+JSON样例可导入");
  await p.goto("http://127.0.0.1:8765/index.html#simulation");
  await p.locator('[data-tab="structure"]').click();
  const beforeFE = await p.evaluate(() => SLZJ.snapshot().assessment);
  await p
    .locator("#feFile")
    .setInputFiles(path.join(root, "examples/external_fe_example.json"));
  await p.locator('[data-action="import-fe"]').click();
  await p.waitForFunction(() => SLZJ.snapshot().simulation.external);
  assert((await p.locator("#feStatus").textContent()).includes("FE-DEMO-001"));
  assert.deepEqual(
    await p.evaluate(() => SLZJ.snapshot().assessment),
    beforeFE,
  );
  console.log("PASS 外部有限元格式样例可导入但不写入SHI");
  // 以组件公开消息定位到 983 环，验证实际筛选后的车载提示来源。
  await p.goto("http://127.0.0.1:8765/index.html#twin");
  for (const type of ["all", "crack"]) {
    await p.locator('[data-filter="type"]').selectOption(type);
    await p.waitForFunction(
      () => document.querySelector("#twinFrame")?.contentWindow?.SLZJScene,
    );
    const currentFrame = p
      .frames()
      .find((f) => f.url().includes("assets/tunnel-scene.html"));
    await currentFrame.waitForFunction(
      (n) => SLZJScene.snapshot().visibleIds.length === n,
      type === "all" ? 16 : 1,
    );
    await p.locator('[data-action="roam"]').click();
    await currentFrame.waitForFunction(() => SLZJScene.snapshot().firstPerson);
    await p.evaluate(() =>
      document
        .querySelector("#twinFrame")
        .contentWindow.postMessage(
          {
            channel: "slzj",
            type: "task",
            progress: 4.2 / 48,
            playing: false,
            speed: 0.6,
          },
          location.origin,
        ),
    );
    await currentFrame.waitForFunction(
      () => Math.abs(SLZJScene.snapshot().carX - 4.2) < 1e-8,
    );
    if (type === "all")
      await currentFrame.waitForFunction(() =>
        document.querySelector("#chAlert").textContent.includes("983"),
      );
    else
      await currentFrame.waitForFunction(
        () =>
          document.querySelector("#chAlert").textContent ===
          "本段未发现异常回波",
      );
  }
  console.log("PASS 车载病害提示遵循新版筛选，隐藏对象不再产生提示");
  assert.equal(errors.length, 0, errors.join("\n"));
  fs.mkdirSync(path.join(root, "tests/output"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "tests/output/local-entry-results.json"),
    JSON.stringify(
      { checks: 4, errors, date: new Date().toISOString() },
      null,
      2,
    ),
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
