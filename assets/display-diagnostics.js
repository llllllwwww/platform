/* 本地显示诊断：仅保留内存中的最近事件，点击按钮才导出；不上传、不保存视频内容。 */
(function () {
  "use strict";
  const version = "2026.10.08.6";
  const started = performance.now();
  const renders = [], events = [];
  let polls = 0, latestPoll = null;
  const route = () => document.querySelector("#page")?.dataset.route || location.hash.slice(1).split("?")[0] || "overview";
  const relevant = () => ["processing", "defects"].includes(route());
  const targetName = target => {
    const element = target?.nodeType === Node.ELEMENT_NODE ? target : target?.parentElement;
    return element ? element.id ? "#" + element.id : element.tagName.toLowerCase() + "." + String(element.className || "").split(/\s+/).slice(0, 3).join(".") : "unknown";
  };
  const record = (kind, data = {}) => {
    events.push({ atMs: Math.round(performance.now() - started), kind, ...data });
    if (events.length > 120) events.shift();
  };
  const observer = new MutationObserver(records => {
    if (!relevant()) return;
    for (const change of records) {
      const element = change.target.nodeType === Node.ELEMENT_NODE ? change.target : change.target.parentElement;
      if (!element?.closest("#page, #navigation, #saveStatus, .topbar")) continue;
      record(element.closest("#videoInferenceStatus, #videoInferenceReview, #videoReconstructionStatus") ? "inference-update" : "outside-update", {
        type: change.type, target: targetName(element), attribute: change.attributeName || undefined,
        added: change.type === "childList" ? change.addedNodes.length : undefined,
        removed: change.type === "childList" ? change.removedNodes.length : undefined,
      });
    }
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["class", "style"] });
  document.addEventListener("animationstart", event => {
    if (relevant()) record("animation-start", { name: event.animationName, target: targetName(event.target), pseudo: event.pseudoElement });
  }, true);
  window.addEventListener("error", event => {
    if (relevant() && event.message) record("script-error", { message: event.message, source: event.filename, line: event.lineno });
  });
  const read = () => ({
    version, exportedAt: new Date().toISOString(), url: location.href, route: route(),
    visibleVersion: document.querySelector("#frontendVersion")?.textContent || "missing",
    scripts: Array.from(document.scripts).map(script => script.getAttribute("src")).filter(Boolean),
    polls, latestPoll, renders: renders.slice(), events: events.slice(),
    animations: document.getAnimations().map(animation => ({ name: animation.animationName || "transition", state: animation.playState, target: targetName(animation.effect?.target) })),
    display: {
      sidebarBlur: getComputedStyle(document.querySelector(".sidebar")).backdropFilter,
      topbarBlur: getComputedStyle(document.querySelector(".topbar")).backdropFilter,
      pageOpacity: getComputedStyle(document.querySelector("#page")).opacity,
    },
  });
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(read(), null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = "平台显示诊断-" + new Date().toISOString().replace(/[:.]/g, "-") + ".json";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  document.addEventListener("click", event => {
    if (event.target.closest('[data-display-diagnostics="export"]')) download();
  });
  window.TunnelDisplayDiagnostics = {
    read, download,
    render(nextRoute, stack) {
      if (!["processing", "defects"].includes(nextRoute)) return;
      renders.push({ atMs: Math.round(performance.now() - started), route: nextRoute, caller: String(stack || "").split("\n").slice(2, 5).join("\n") });
      if (renders.length > 30) renders.shift();
    },
    reconstructionPoll(job) {
      record("reconstruction-poll", { id: job.id, status: job.status, phase: job.progress?.phase, percent: job.progress?.percent });
    },
    poll(job) {
      polls++;
      latestPoll = { atMs: Math.round(performance.now() - started), id: job.id, status: job.status, phase: job.progress?.phase, percent: job.progress?.percent };
    },
  };
})();
