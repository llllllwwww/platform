/* 平台重启：界面初始化，已有业务档案保留；同一服务会话刷新可继续操作。 */
(function (root) {
  "use strict";
  let backend = false, sessionId = "", canPersist = true;
  const LOCAL_INDEX = "slzj-workbench-history-v1", LOCAL_PREFIX = "slzj-workbench-history-";
  async function api(action, fields) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), fields ? 8000 : 2500);
    try {
      const response = await fetch(`/api/workbench/${action}`, { cache: "no-store", signal: controller.signal,
        ...(fields ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields) } : {}) });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || "历史工作状态暂不可读取。");
      return value;
    } finally { clearTimeout(timeout); }
  }
  function localList() {
    try { return JSON.parse(localStorage.getItem(LOCAL_INDEX) || "[]"); } catch (_) { return []; }
  }
  async function archive(state, reason = "startup") {
    const fields = { state, reason, previousSessionId: state.workbenchSessionId || "" };
    if (backend) {
      try { return await api("archive", fields); } catch (_) { /* 本机写入失败时保留浏览器副本。 */ }
    }
    const id = "local-" + Date.now() + "-" + Math.random().toString(16).slice(2, 8);
    const record = { id, savedAt: new Date().toISOString(), batchId: state.batch || "", taskId: state.activeTask || "", reason,
      radarCount: (state.radars || []).length, videoCount: Object.keys(state.videoJobsById || {}).length };
    localStorage.setItem(LOCAL_PREFIX + id, JSON.stringify({ ...record, state }));
    localStorage.setItem(LOCAL_INDEX, JSON.stringify([record, ...localList()]));
    return record;
  }
  async function initialize(saved, createState) {
    try {
      const info = await api("session");
      if (typeof info.sessionId !== "string" || !/^S-[0-9a-f]{32}$/.test(info.sessionId)) throw Error("旧服务没有启动会话接口。");
      sessionId = info.sessionId; backend = true;
    } catch (_) {
      try {
        sessionId = sessionStorage.getItem("slzj-static-session") || "static-" + Date.now() + "-" + Math.random().toString(16).slice(2);
        sessionStorage.setItem("slzj-static-session", sessionId);
      } catch (_) { sessionId = "static-" + Date.now(); }
    }
    let tabSessionId = "", tabState = null;
    try {
      tabSessionId = sessionStorage.getItem("slzj-active-workbench-session") || "";
      tabState = JSON.parse(sessionStorage.getItem("slzj-active-workbench-state") || "null");
      sessionStorage.setItem("slzj-active-workbench-session", sessionId);
    } catch (_) { /* 禁用会话存储时仍以初始状态打开。 */ }
    const resumable = tabState?.schemaVersion === 2 ? tabState : saved;
    if (tabSessionId === sessionId && resumable?.schemaVersion === 2 && resumable.workbenchSessionId === sessionId) return { state: resumable, reset: false };
    let warning = "";
    if (saved?.schemaVersion === 2) {
      try { await archive(saved); }
      catch (_) { canPersist = false; warning = "历史状态未能归档，原浏览器记录已保留；当前初始界面暂不覆盖它。"; }
    }
    const state = createState();
    // 项目、批次、任务定义和视频身份是档案元数据；当前选择、计算和视图参数使用初始值。
    if (saved?.schemaVersion === 2) {
      if (saved.project) state.project = saved.project;
      if (saved.batches?.length) state.batches = saved.batches;
      if (!state.batches.some(row => row.id === state.batch)) state.batch = state.batches[0].id;
      if (saved.tasks?.length) state.tasks = saved.tasks.map(row => ({ ...row, speed: .6, spacing: .1, scanStart: -90, scanEnd: 90, obstacle: false, status: "ready", progress: 0 }));
      state.videoJobsById = { ...(saved.videoJobsById || {}) };
      for (const job of Object.values(saved.videoInferenceJobs || {})) if (job?.id) state.videoJobsById[job.id] = job;
    }
    state.workbenchSessionId = sessionId;
    state.videoSelectionByContext = {};
    state.videoInferenceJobs = {};
    state.videoReconstructionJobs = {};
    state.retiredVideoIds = [];
    state.selectedId = null;
    state.activeTask = state.tasks.find(row => row.batch === state.batch)?.id || state.tasks[0]?.id;
    return { state, reset: true, warning };
  }
  async function list() {
    const local = localList();
    if (!backend) return local;
    try { return [...(await api("archives")).archives, ...local]; } catch (_) { return local; }
  }
  async function load(id) {
    const record = id.startsWith("local-") ? JSON.parse(localStorage.getItem(LOCAL_PREFIX + id) || "null") : await api(`archive?id=${encodeURIComponent(id)}`);
    if (record?.state?.schemaVersion !== 2) throw Error("历史工作状态格式无效。");
    return { ...record.state, workbenchSessionId: sessionId, retiredVideoIds: [] };
  }
  function saveTab(state) {
    try { sessionStorage.setItem("slzj-active-workbench-state", JSON.stringify(state)); } catch (_) { /* 本机与浏览器持久档案仍保留。 */ }
  }
  root.TunnelWorkbenchSession = { initialize, list, load, archive, saveTab, canPersist: () => canPersist };
})(typeof window === "undefined" ? globalThis : window);
