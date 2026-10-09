/* 检测完成后手动启动建模；独立轮询只通知状态变更，不重绘工作台。 */
(function () {
  "use strict";
  const endpoint = "/api/video-reconstruction";
  const jobs = new Map(), watches = new Map(), signatures = new Map();
  const terminal = status => ["completed", "failed", "cancelled"].includes(status);
  function dispatch(job) {
    jobs.set(job.id, job);
    const signature = JSON.stringify({ ...job, log: undefined });
    if (signature === signatures.get(job.id)) return;
    signatures.set(job.id, signature);
    document.dispatchEvent(new CustomEvent("tunnel-video-reconstruction", { detail: { job: JSON.parse(JSON.stringify(job)) } }));
  }
  async function poll(id, token) {
    const watch = watches.get(id);
    if (!watch || token !== watch.token) return;
    try {
      const response = await fetch(`${endpoint}/status?id=${encodeURIComponent(id)}`, { cache: "no-store" });
      const job = await response.json();
      if (!response.ok) throw Error(job.error || "无法读取三维建模状态。");
      if (watches.get(id)?.token !== token) return;
      window.TunnelDisplayDiagnostics?.reconstructionPoll?.(job);
      dispatch(job);
      watch.errors = 0;
      if (terminal(job.status)) { watches.delete(id); return; }
      watch.timer = setTimeout(() => poll(id, token), 1000);
    } catch (error) {
      if (watches.get(id)?.token !== token) return;
      // 网络中断不冒充后端建模失败；可重连原任务，不重复创建子进程。
      watch.errors++;
      if (watch.errors < 4) { watch.timer = setTimeout(() => poll(id, token), 1800); return; }
      watches.delete(id);
      const job = jobs.get(id);
      if (job) dispatch({ ...job, connectionError: error.message || "连接中断，请点击重新连接任务状态。" });
    }
  }
  function watch(job) {
    const previous = watches.get(job.id);
    if (previous?.timer) clearTimeout(previous.timer);
    jobs.set(job.id, job);
    const entry = { token: (previous?.token || 0) + 1, errors: 0, timer: null };
    watches.set(job.id, entry);
    entry.timer = setTimeout(() => poll(job.id, entry.token), 250);
  }
  async function start(source, options = {}) {
    if (!source?.id || source.status !== "completed") throw Error("先完成本次视频检测，再手动启动三维重建。");
    if (location.protocol !== "http:" || !/^(127\.0\.0\.1|localhost)$/i.test(location.hostname)) throw Error("请通过“启动平台.py”打开本地工作台后再建模，静态网页不能启动 Python。");
    const response = await fetch(`${endpoint}/start`, { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ jobId: source.id, batchId: source.batchId, taskId: source.taskId, maxFrames: Number(options.maxFrames || 24) }) });
    const job = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(job.error || `无法启动建模（HTTP ${response.status}）。`);
    dispatch(job);
    if (!terminal(job.status)) watch(job);
    return job;
  }
  function resume(job) {
    if (!job?.id) return;
    watch(job);
  }
  window.TunnelVideoReconstruction = { start, resume, forget(id) { const watch = watches.get(id); if (watch?.timer) clearTimeout(watch.timer); watches.delete(id); jobs.delete(id); signatures.delete(id); }, busy: () => Array.from(jobs.values()).some(job => ["queued", "running"].includes(job.status)), current: id => jobs.has(id) ? JSON.parse(JSON.stringify(jobs.get(id))) : null };
})();
