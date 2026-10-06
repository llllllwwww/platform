/* 本地视频推理任务：把浏览器导入的录像交给启动平台.py 的 Python 桥接服务。 */
(function () {
  "use strict";
  const endpoint = "/api/video-inference";
  let active = null;
  let pollTimer = null;
  let sequence = 0;

  const dispatch = (job, extra = {}) => {
    const detail = { job: job ? JSON.parse(JSON.stringify(job)) : null, ...extra };
    document.dispatchEvent(new CustomEvent("tunnel-video-inference", { detail }));
  };
  const localMessage = () => "视频自动推理需要通过“启动平台.py”访问本地工作台；直接双击 index.html 时只能播放视频，不能启动 Python。";
  const stopPolling = () => { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } };

  async function poll(id, token) {
    if (token !== sequence) return;
    try {
      const response = await fetch(`${endpoint}/status?id=${encodeURIComponent(id)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取视频推理任务状态。");
      const job = await response.json();
      if (token !== sequence) return;
      active = job;
      dispatch(job);
      if (["completed", "failed", "cancelled"].includes(job.status)) { stopPolling(); return; }
      pollTimer = setTimeout(() => poll(id, token), 850);
    } catch (error) {
      if (token !== sequence) return;
      stopPolling();
      active = { ...(active || {}), status: "failed", error: error.message || "推理任务状态读取失败。", progress: { ...(active?.progress || {}), phase: "状态读取失败", percent: 100 } };
      dispatch(active, { transportError: true });
    }
  }

  async function start(file, context, options = {}) {
    if (!file) throw new Error("没有选择视频文件。");
    const token = ++sequence;
    stopPolling();
    active = {
      id: null, status: "uploading", filename: file.name, batchId: context?.batchId || "", taskId: context?.taskId || "",
      taskName: context?.taskName || "", createdAt: new Date().toISOString(), finishedAt: null,
      progress: { phase: "上传到本机推理服务", percent: 1, frames: 0, totalFrames: 0, candidates: 0 }, result: null, error: null,
    };
    dispatch(active);
    if (location.protocol !== "http:" || !/^127\.0\.0\.1$|^localhost$/i.test(location.hostname)) {
      active = { ...active, status: "failed", error: localMessage(), progress: { ...active.progress, phase: "未连接本地推理服务", percent: 100 } };
      dispatch(active, { transportError: true });
      throw new Error(localMessage());
    }
    const form = new FormData();
    form.append("video", file, file.name);
    form.append("batchId", context?.batchId || "");
    form.append("taskId", context?.taskId || "");
    form.append("taskName", context?.taskName || "");
    form.append("maxFrames", String(options.maxFrames || 8));
    form.append("threshold", String(options.threshold || 0.7));
    form.append("device", options.device === "cuda" ? "cuda" : "cpu");
    try {
      const response = await fetch(`${endpoint}/start`, { method: "POST", body: form, cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `本地推理服务返回 HTTP ${response.status}。`);
      if (token !== sequence) return payload;
      active = payload;
      dispatch(active);
      pollTimer = setTimeout(() => poll(active.id, token), 250);
      return payload;
    } catch (error) {
      if (token !== sequence) return null;
      stopPolling();
      active = { ...active, status: "failed", error: error.message || "无法启动 Python 推理。", progress: { ...active.progress, phase: "无法启动 Python 推理", percent: 100 } };
      dispatch(active, { transportError: true });
      throw error;
    }
  }

  function current() { return active ? JSON.parse(JSON.stringify(active)) : null; }
  function reset() { ++sequence; stopPolling(); active = null; dispatch(null, { reset: true }); }
  window.TunnelVideoInference = { start, current, reset, localMessage };
})();
