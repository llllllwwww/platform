/* 同一视频的数据视图。目录与工程分值由本机服务提供，缺失阶段保持空状态。 */
(function (root) {
  "use strict";
  const labels = { acquisition: "采集疑似位置", detection: "检测候选", reconstruction: "三维复核", review: "人工复核", twin: "数字孪生", health: "健康评估", operations: "预警与治理", simulation: "仿真验证", report: "检测报告" };
  const status = value => ({ completed: "已完成", ready: "可查看", reviewed: "已记录复核", pending: "待完成", queued: "排队中", running: "处理中", failed: "失败", invalid: "来源校验未通过", awaiting_review: "待人工复核", not_started: "尚未运行", awaiting_reconstruction: "待本视频三维重建", awaiting_measurements: "待标定与实测尺寸", awaiting_assessment: "待有效健康评估", processing_record: "本视频处理记录" })[value] || "待完成";
  const idOK = id => typeof id === "string" && /^[A-Za-z0-9_-]{1,100}$/.test(id);
  const ownedUrl = (url, id) => !url || (typeof url === "string" && url.startsWith(`/api/video-inference/file/${id}/`) && !url.includes(".."));
  function validateInference(job) {
    if (!job?.id || !idOK(job.id)) return job;
    const result = job.result;
    if (result && (!ownedUrl(result.jsonUrl, job.id) || !ownedUrl(result.reviewUrl, job.id) || (result.preview || []).some(row => [row.imageUrl, row.frameUrl, row.overlayUrl, row.maskUrl].some(url => !ownedUrl(url, job.id))))) {
      return { ...job, status: "failed", result: null, error: "检测结果引用了其他视频地址，已隔离；请刷新本视频档案。" };
    }
    return job;
  }
  function validateReconstruction(job) {
    if (!job?.id || job.sourceJobId !== job.id) return null;
    if (job.result && [job.result.viewerUrl, job.result.jsonUrl, job.result.sceneUrl, job.result.qualityUrl].some(url => !ownedUrl(url, job.id))) return null;
    return job;
  }
  async function request(action, fields) {
    const response = await fetch(`/api/video-workspace/${action}`, { cache: "no-store", ...(fields ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields) } : {}) });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(value.error || "本机视频档案暂不可读取，请使用最新版本地启动器。");
    return value;
  }
  function validateBundle(bundle, id) {
    if (bundle?.manifest?.videoId !== id || bundle?.inference?.id !== id) throw Error("返回档案不属于当前选择的视频。");
    for (const key of ["acquisition", "review", "twin", "health", "operations", "simulation", "report"]) {
      if (bundle[key] && bundle[key].videoId !== id) throw Error("下游文件的视频来源不一致。");
    }
    bundle.inference = validateInference(bundle.inference);
    if (bundle.reconstruction && !validateReconstruction(bundle.reconstruction)) throw Error("三维成果引用了其他视频。");
    return bundle;
  }
  function pages(route, data, ui) {
    const { heading, panel, moduleTabs, btn, escape: e, fmt } = ui;
    const { job, rebuilt, bundle, error, pending, unselected } = data;
    if (unselected) {
      const titles = { overview: "项目总览", defects: "病害清单与复核", twin: "三维数字孪生", health: "结构健康评估", alerts: "预警与治理", reports: "检测报告", simulation: "仿真验证工作台" };
      return heading(titles[route] || "工作台", "当前处于初始状态，尚未选择视频或加载历史工作数据。", "初始工作台") + moduleTabs() +
        panel("未选择视频", `<div class="video-data-empty" id="initialWorkspace"><b>选择数据后开始工作</b><p>已有视频、检测结果、三维模型和复核记录均保存在原档案中。可在页面上方手动选择视频，也可导入新录像或加载历史工作状态。</p><p>当前不加载历史候选、模型、健康评分、预警和报告；独立演示功能仍可手动打开。</p><div class="actions">${btn("前往录像采集", "go-video-acquisition", "primary")}${btn("打开独立演示", "video-use-demo", "subtle")}</div></div>`) +
        panel("四阶段初始状态", `<div class="video-stage-grid">${["01 检测采集", "02 智能解析", "03 孪生评估与运维", "04 仿真推演"].map(name => `<div><b>${name}</b><span>等待选择数据</span><code>未加载历史成果</code></div>`).join("")}</div>`);
    }
    const name = job?.filename || "等待选择视频", id = job?.id || "尚未建立视频档案";
    const result = job?.status === "completed" ? job.result : null;
    const geometry = rebuilt?.status === "completed" ? rebuilt.result : null;
    const m = bundle?.manifest, prefix = job?.id ? `/api/video-inference/file/${job.id}/` : "";
    const json = (path, label) => prefix ? `<a class="btn small subtle" href="${e(prefix + path)}" target="_blank" rel="noopener">${label} ↗</a>` : "";
    const source = `<div class="video-source-card"><b>${e(name)}</b><code>${e(id)}</code><span>${m ? e(m.directory) : pending ? "上传中的视频尚无服务器目录；旧视频结果已从当前视图移开。" : "本视频档案正在核验，尚未核验的下游成果不出具评估结论。"}</span>${error ? `<p class="video-data-warning">${e(error)}</p>` : ""}${m?.errors?.length ? `<p class="video-data-warning">${m.errors.map(e).join("；")}</p>` : ""}</div>`;
    const chain = `<div class="video-stage-grid">${Object.entries(labels).map(([key, title]) => `<div data-video-stage="${key}"><b>${title}</b><span>${status(m?.stages?.[key]?.status)}</span><code>${e(m?.stages?.[key]?.path || "等待本视频成果")}</code></div>`).join("")}</div>`;
    const nav = `<div class="actions">${btn("检测与三维复核", "open-video-inference", "subtle")}${btn("当前视频孪生", "go-video-twin", "subtle")}${btn("当前视频评估", "go-video-health", "subtle")}${btn("当前视频报告", "go-video-report", "subtle")}</div>`;
    const identity = panel("当前视频档案", source + (job?.id ? `<div class="actions"><a class="btn small subtle" href="/api/video-workspace/media?id=${e(job.id)}" target="_blank" rel="noopener">本视频原始录像 ↗</a>${json("manifest.json", "本视频文件索引")}</div>` : "") + nav, '<span class="badge mint">同源数据</span>');
    if (route === "overview") return heading("项目总览 · 当前视频", "检测、三维复核与后续模块共用这个视频编号，各阶段成果独立保存。", "当前视频数据") + moduleTabs() + identity + panel("本视频处理链与文件目录", chain) + panel("当前成果", `<div class="video-data-kpis"><div><b>${result?.frameCount ?? "—"}</b><small>已检测采样帧</small></div><div><b>${result?.candidateCount ?? "—"}</b><small>裂缝候选观测</small></div><div><b>${geometry?.localizedCandidates ?? "—"}</b><small>获得局部三维位置</small></div><div><b>待评估</b><small>物理尺度与尺寸未标定</small></div></div>`);
    if (route === "twin") return heading("三维数字孪生 · 当前视频", "只加载本视频的模型与定位成果，保持局部坐标和原始候选的对应关系。", "03 / 孪生评估与运维", btn("返回本视频复核", "open-video-inference", "subtle")) + moduleTabs() + identity + panel("本视频三维空间", geometry ? `<iframe id="videoTwinFrame" class="multiscene-frame" src="${e(geometry.viewerUrl)}" title="${e(name)} · 同源三维模型" loading="eager"></iframe><p class="muted">${geometry.localizedCandidates || 0} / ${result?.candidateCount || geometry.candidateCount || 0} 个候选具有局部三维位置。坐标为任意尺度 SfM 单位；尚未完成隧道里程和工程坐标标定。</p>${json(`reconstruction/${rebuilt.runId}/localized_defects.json`, "本视频定位数据")}${json("twin/state.json", "孪生状态")}` : `<div class="video-data-empty"><b>本视频尚无可加载模型</b><p>先完成检测，再到第二模块手动运行三维重建与定位。已存在的其他视频模型保存在各自档案中。</p>${btn("前往手动三维重建", "open-video-inference", "primary")}</div>`, '<span class="badge mint">局部三维复核</span>');
    if (route === "health") return heading("结构健康评估 · 当前视频", "评估仅使用本视频确认并标定的数据；候选数量和像素面积不足以计算工程健康分。", "03 / 孪生评估与运维") + moduleTabs() + identity + panel("本视频评估状态", `<div class="video-data-empty"><b>${status(bundle?.health?.status || (geometry ? "awaiting_measurements" : "awaiting_reconstruction"))}</b><p>SHI：未计算 · 风险等级：未判定</p><ol>${(bundle?.health?.missing || ["本视频三维重建与定位", "经校核的物理尺度与隧道坐标", "已确认病害类型、实测尺寸和评估参数"]).map(item => `<li>${e(item)}</li>`).join("")}</ol><p>本视频的待评估记录保存在 <code>health/assessment.json</code>；不会借用其他视频或演示台账的分值。</p>${json("health/assessment.json", "当前视频评估文件")}</div>`);
    if (route === "alerts") return heading("预警与治理 · 当前视频", "本视频尚未形成有依据的健康评估，暂不自动生成预警或治理结论。", "03 / 孪生评估与运维") + moduleTabs() + identity + panel("本视频处置记录", `<div class="video-data-empty"><b>等待有效评估</b><p>当前预警 0 条，治理任务 0 条。演示预警在独立演示模式中保留。</p>${json("operations/state.json", "本视频运维状态")}</div>`);
    if (route === "simulation") return heading("仿真验证工作台 · 当前视频", "视频对应的仿真输入和结果只保存在这个视频的档案中。", "04 / 仿真推演") + moduleTabs() + identity + panel("本视频验证状态", `<div class="video-data-empty"><b>尚无本视频工程仿真结果</b><p>需补齐经校核的几何、材料、荷载和边界条件，再导入同源求解成果。原有作业几何、雷达示意、结构敏感性与方案比较可在独立演示模式使用。</p>${json("simulation/state.json", "本视频仿真状态")}${btn("打开独立演示工作台", "video-demo-simulation", "subtle")}</div>`);
    if (route === "reports") return heading("检测报告 · 当前视频", "报告记录本视频已完成和待完成的阶段；检测、模型和评估文件具有相同视频编号。", "03 / 孪生评估与运维", btn("下载本视频 JSON", "export-json", "subtle") + btn("下载本视频 HTML", "export-html", "primary") + btn("候选 CSV", "export-csv", "subtle") + btn("打印 / PDF", "print-report", "subtle")) + moduleTabs() + `<article id="report" class="video-report">${identity}${panel("视频身份与来源", `<p>视频编号：<code>${e(id)}</code></p><p>SHA-256：<code class="video-hash">${e(m?.source?.sha256 || "正在核验")}</code></p><p>批次 / 任务：${e(job?.batchId)} / ${e(job?.taskId)}</p>`)}${panel("处理成果与文件", chain)}${panel("结论范围", `<p>检测采样帧：${result?.frameCount ?? "未完成"}；候选观测：${result?.candidateCount ?? "未完成"}；取得局部位置：${geometry?.localizedCandidates ?? "未完成"}。</p><p>健康分未计算，风险等级未判定。尚缺物理标定和确认尺寸，不将裂缝候选当作工程诊断。</p>${json("reports/report.json", "已归档 JSON")}${json("reports/report.html", "已归档 HTML")}`)}</article>`;
    return "";
  }
  root.TunnelVideoData = { labels, status, ownedUrl, validateInference, validateReconstruction, validateBundle, request, pages };
})(typeof window === "undefined" ? globalThis : window);
