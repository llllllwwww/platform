/* 现场视频接入：原生媒体播放，不把外部画面转换为仿真台账或识别结论。 */
(function () {
  "use strict";
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const stamp = () => new Date().toISOString();
  const timeText = (seconds) => Number.isFinite(seconds) ? `${Math.floor(seconds / 60).toString().padStart(2,"0")}:${Math.floor(seconds % 60).toString().padStart(2,"0")}` : "直播 / 无固定时长";
  let root = null, context = null, selectedMode = "file", source = null;
  let objectURL = null, mediaStream = null, token = 0, timeout = null, frameCallback = null;
  let status = "未接入", message = "选择现场录像、网络视频源或摄像头，接入后在这里观察真实画面。";
  let lastFrame = null, frames = 0, ready = false, sessionStart = null;
  let reconnectSource = null, events = [], captures = [], inactive = false, lastUIUpdate = 0;
  const $ = (selector) => root?.querySelector(selector);
  const button = (label, action, cls = "subtle") => `<button type="button" class="${cls}" data-monitor-action="${action}">${label}</button>`;

  function markup() {
    return `<section class="panel video-monitor" id="videoMonitor" aria-label="检测车现场视频监测">
      <div class="panel-head"><h2>检测车作业场景 · 现场视频</h2><span class="badge blue">01 / 外部视频接入</span></div>
      <div class="panel-body">
        <p class="monitor-intro">在同屏工作区左侧接入原始画面，与右侧仿真场景对照。录像回放和实时输入分别标明来源。</p>
        <div class="monitor-tabs" role="tablist" aria-label="视频接入方式">
          <button type="button" role="tab" id="monitorTabFile" aria-controls="monitorFilePane" aria-selected="true" data-monitor-mode="file" class="active">本地录像回放</button>
          <button type="button" role="tab" id="monitorTabNetwork" aria-controls="monitorNetworkPane" aria-selected="false" data-monitor-mode="network">网络监测源</button>
          <button type="button" role="tab" id="monitorTabCamera" aria-controls="monitorCameraPane" aria-selected="false" data-monitor-mode="camera">摄像头 / 采集卡</button>
        </div>
        <div id="monitorFilePane" class="monitor-source-pane" role="tabpanel" aria-labelledby="monitorTabFile">
          <label class="monitor-file"><span><b>导入现场视频文件</b><small>MP4、WebM 等浏览器可解码的视频；只在本机读取，不上传。</small></span><input id="monitorFile" type="file" accept="video/*,.mp4,.webm,.mov,.m4v" aria-label="选择现场视频文件"></label>
          <p class="monitor-help">导入录像会显示“录像回放”，不作为实时采集。支持播放、暂停、拖动进度、音量和全屏。</p>
        </div>
        <div id="monitorNetworkPane" class="monitor-source-pane" role="tabpanel" aria-labelledby="monitorTabNetwork" hidden>
          <form id="monitorNetworkForm" class="monitor-network-form">
            <label class="field monitor-url-field">视频源地址<input id="monitorURL" type="url" required placeholder="http(s)://设备或网关/视频源" autocomplete="off" spellcheck="false"></label>
            <label class="field">画面格式<select id="monitorFormat"><option value="video">视频直链 / 原生 HLS</option><option value="mjpeg">MJPEG 实时图像流</option></select></label>
            <label class="field">源端用途<select id="monitorNature"><option value="live">实时监测源（由源端确认）</option><option value="recording">现场录像回放</option></select></label>
            <button type="submit" class="primary">连接视频源</button>
          </form>
          <p class="monitor-help">使用设备或视频网关提供的 HTTP(S) 地址；视频直链需可被当前浏览器解码。MJPEG 可用于实时图像监测，HLS 仅在浏览器原生支持时播放。RTSP 请先经视频网关转换。网络源的采集时间与直播延迟需由源端核实。</p>
        </div>
        <div id="monitorCameraPane" class="monitor-source-pane" role="tabpanel" aria-labelledby="monitorTabCamera" hidden>
          <div class="monitor-camera-form"><label class="field">摄像头 / USB 视频采集卡<select id="monitorCamera"><option value="">系统默认视频设备</option></select></label>${button("刷新设备", "devices")}${button("启动摄像头", "camera", "primary")}</div>
          <p class="monitor-help">接入后需要允许浏览器使用摄像头；只请求视频，不启用麦克风。停止接入或离开本页会释放摄像头。</p>
        </div>
        <div class="monitor-screen" id="monitorScreen">
          <video id="monitorVideo" controls muted playsinline preload="metadata" aria-label="外部监测视频" hidden></video>
          <img id="monitorImage" alt="网络 MJPEG 监测画面" hidden>
          <div class="monitor-empty" id="monitorEmpty"><svg viewBox="0 0 72 54" aria-hidden="true"><rect x="4" y="8" width="46" height="37" rx="7"/><path d="M50 19 68 12v30l-18-7M19 18l19 9-19 9z"/></svg><b>尚未接入现场视频</b><span>导入录像或连接摄像头，右侧显示仿真作业与对应疑似位置。</span></div>
          <div class="monitor-overlay"><span class="badge" id="monitorSourceBadge">未接入</span><span id="monitorSourceName">无视频源</span></div>
        </div>
        <div class="monitor-status" role="status" aria-live="polite"><span class="monitor-status-dot" id="monitorStatusDot"></span><b id="monitorStatus">未接入</b><span id="monitorMessage"></span></div>
        <div class="monitor-actions">${button("截图取证", "capture")}${button("全屏画面", "fullscreen")}${button("重新连接", "reconnect")}${button("停止 / 释放视频源", "stop")}${button("下载接入记录", "record", "small subtle")}</div>
        <div class="monitor-feedback" id="monitorFeedback" role="alert" hidden></div>
        <details class="monitor-details"><summary>来源、时间与接入说明</summary>
        <div class="monitor-metadata"><div><small>任务 / 批次关联</small><b id="monitorContext"></b></div><div><small>画面尺寸</small><b id="monitorResolution">—</b></div><div><small>播放位置 / 时长</small><b id="monitorTime">—</b></div><div><small id="monitorFrameLabel">最近呈现帧 · 本机时间</small><b id="monitorLastFrame">—</b></div></div>
        <p class="monitor-boundary">现场视频用于采集、观察和记录疑似位置，本阶段不判断类型。冻结画面点击疑似点后，仿真按同一位置与先后顺序显示。可勾选“录像播放联动仿真车”按相对进度对照；机械臂、车载雷达仍为演示，类型识别在第二阶段独立运行。</p>
        </details>
      </div>
    </section>`;
  }
  function log(action) {
    events.push({time: stamp(), action, source: source?.name || null, kind: source?.kind || null, batch: context?.batchId, task: context?.taskId});
    if (events.length > 50) events.shift();
  }
  function alertMessage(text) {
    if (!root) return;
    $("#monitorFeedback").textContent = text;
    $("#monitorFeedback").hidden = !text;
  }
  function setStatus(next, detail = "") {
    status = next;
    message = detail;
    refresh();
  }
  function refresh() {
    if (!root) return;
    const video = $("#monitorVideo"), image = $("#monitorImage");
    $("#monitorStatus").textContent = status;
    $("#monitorMessage").textContent = message;
    $("#monitorStatusDot").dataset.state = status === "播放中" || status === "监测中" || status === "回放中" ? "active" : status === "连接失败" || status === "画面中断" ? "error" : "idle";
    const label = !source ? "未接入" : source.kind === "file" ? "现场录像 · 回放" : source.kind === "camera" ? "摄像头 · 实时输入" : source.nature === "recording" ? "网络录像 · 回放" : "网络监测源 · 源端确认实时性";
    $("#monitorSourceBadge").textContent = label;
    $("#monitorSourceBadge").className = `badge ${source?.kind === "camera" ? "mint" : "blue"}`;
    $("#monitorSourceName").textContent = source?.name || "无视频源";
    $("#monitorContext").textContent = context ? `${context.taskName} / ${context.batchName}` : "—";
    const width = source?.format === "mjpeg" ? image.naturalWidth : video.videoWidth;
    const height = source?.format === "mjpeg" ? image.naturalHeight : video.videoHeight;
    $("#monitorResolution").textContent = ready && width ? `${width} × ${height}` : "—";
    $("#monitorTime").textContent = !source ? "—" : source.kind === "camera" ? "实时输入 / 无录像进度" : source.format === "mjpeg" ? "图像流 / 无录像进度" : `${timeText(video.currentTime)} / ${timeText(video.duration)}`;
    $("#monitorFrameLabel").textContent = source?.format === "mjpeg" ? "资源最近加载 · 本机时间" : "最近呈现帧 · 本机时间";
    $("#monitorLastFrame").textContent = lastFrame ? new Date(lastFrame).toLocaleTimeString("zh-CN",{hour12:false}) : "—";
    const empty = $("#monitorEmpty");
    empty.hidden = ready;
    empty.querySelector("b").textContent = source ? (status === "连接失败" || status === "画面中断" ? "视频尚未就绪" : "等待视频画面") : "尚未接入现场视频";
    empty.querySelector("span").textContent = source ? message || "接收到有效画面后才会显示已连接。" : "导入录像或连接摄像头，右侧显示仿真作业与对应疑似位置。";
    empty.classList.toggle("loading", !!source && status === "连接中");
    $("[data-monitor-action=capture]").disabled = !ready;
    $("[data-monitor-action=stop]").disabled = !source && status !== "连接中";
    $("[data-monitor-action=reconnect]").disabled = !reconnectSource;
    document.dispatchEvent(new CustomEvent("tunnel-monitor-state", {detail: {active: !!source && ready, kind: source?.kind || null, status, sourceId: source?.evidenceId || null, timeSec: source?.kind === "file" || source?.nature === "recording" ? video.currentTime : null, durationSec: Number.isFinite(video.duration) ? video.duration : null}}));
  }
  function cancelFrameWatch() {
    const video = $("#monitorVideo");
    if (frameCallback !== null && video?.cancelVideoFrameCallback) video.cancelVideoFrameCallback(frameCallback);
    frameCallback = null;
  }
  function watchFrames(expected) {
    const video = $("#monitorVideo");
    if (!video?.requestVideoFrameCallback) return;
    cancelFrameWatch();
    const tick = () => {
      if (expected !== token || !source || inactive) return;
      lastFrame = stamp();
      frames++;
      if (performance.now() - lastUIUpdate > 250) {
        lastUIUpdate = performance.now();
        refresh();
      }
      frameCallback = video.requestVideoFrameCallback(tick);
    };
    frameCallback = video.requestVideoFrameCallback(tick);
  }
  function release({keepFile = false} = {}) {
    token++;
    clearTimeout(timeout);
    timeout = null;
    cancelFrameWatch();
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
    const video = $("#monitorVideo"), image = $("#monitorImage");
    if (video) video.pause();
    if (keepFile && source?.kind === "file") {
      setStatus("已暂停", "已离开检测页，录像暂停；返回后可继续播放。");
      return;
    }
    if (video) { video.srcObject = null; video.removeAttribute("src"); video.load(); video.hidden = true; }
    if (image) { image.removeAttribute("src"); image.hidden = true; }
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null;
    source = null;
    ready = false;
    lastFrame = null;
    frames = 0;
    lastUIUpdate = 0;
    refresh();
  }
  function errorText(error) {
    if (error.name === "NotAllowedError") return "摄像头权限被拒绝。请在浏览器中允许本平台使用摄像头，再重新连接。";
    if (error.name === "NotFoundError") return "没有找到可用摄像头或采集卡，请检查设备连接。";
    if (error.name === "NotReadableError") return "视频设备被占用或无法读取，请关闭其他占用程序后重试。";
    if (error.name === "OverconstrainedError") return "所选设备不可用，请刷新设备列表并重新选择。";
    return error.message || "视频连接失败，请检查来源与格式。";
  }
  function fail(text, expected = token) {
    if (expected !== token) return;
    log("连接失败");
    release();
    setStatus("连接失败", text);
    alertMessage(text);
  }
  function networkURL(value) {
    let url;
    try { url = new URL(value.trim()); } catch { throw Error("请输入完整的 HTTP(S) 视频源地址。"); }
    if (!["http:", "https:"].includes(url.protocol)) throw Error("该地址不能直接在浏览器中播放。RTSP/RTMP 请先通过设备或视频网关转为 HTTP(S) 视频直链或 MJPEG。");
    if (url.username || url.password) throw Error("请使用设备或网关提供的播放地址，地址中不要填写账号密码。");
    if (location.protocol === "https:" && url.protocol === "http:") throw Error("当前页面使用 HTTPS，请使用 HTTPS 视频源；局域网 HTTP 视频可在本地启动器中接入。");
    return url.href;
  }
  async function start(input) {
    // 先校验新来源，校验失败不打断正在播放的旧来源。
    if (input.kind === "file" && (!input.file || (!input.file.type.startsWith("video/") && !/\.(mp4|webm|mov|m4v|ogv)$/i.test(input.file.name)))) throw Error("请选择浏览器可播放的视频文件，例如 MP4 或 WebM。");
    if (input.kind === "network") input = {...input, url: networkURL(input.url)};
    if (input.kind === "camera" && (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)) throw Error("摄像头需要在本地启动器或 HTTPS 页面中使用，并允许摄像头权限。");
    if (input.kind === "network" && input.format === "video" && /\.m3u8(?:\?|$)/i.test(input.url)) {
      const video = $("#monitorVideo");
      if (!video.canPlayType("application/vnd.apple.mpegurl") && !video.canPlayType("application/x-mpegURL")) throw Error("当前浏览器不支持原生 HLS。请使用浏览器可播放的视频直链、MJPEG，或在支持原生 HLS 的浏览器中连接。");
    }
    release();
    inactive = false;
    reconnectSource = input;
    const expected = token;
    source = {...input, name: input.kind === "file" ? input.file.name : input.kind === "camera" ? "摄像头 / 采集卡" : new URL(input.url).origin + new URL(input.url).pathname};
    source.evidenceId = input.kind === "file" ? null : "VS-" + stamp() + "-" + Math.random().toString(36).slice(2,9);
    source.identityBasis = input.kind === "file" ? "SHA-256 整文件校验" : "本次接入会话（不能跨会话冒用）";
    if(input.kind === "file") {
      const original = source;
      (async()=>{
        const partial=input.file.size>128*1024*1024;
        const bytes=partial?await new Blob([String(input.file.size),input.file.slice(0,1048576),input.file.slice(-1048576)]).arrayBuffer():await input.file.arrayBuffer();
        if(!crypto.subtle)throw Error("当前浏览器不支持来源校验，请使用本地启动器");
        const hash=await crypto.subtle.digest("SHA-256",bytes);
        if(expected!==token||source!==original)return;
        source.evidenceId="VF-"+[...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,"0")).join("");
        source.identityBasis=partial?"首尾采样 SHA-256 与文件大小（非整文件校验）":"SHA-256 整文件校验";refresh();
      })().catch(()=>{if(source===original)source.identityBasis="来源校验失败，不能建立现场对应证据";});
    }
    sessionStart = stamp();
    alertMessage("");
    log("开始接入");
    setStatus("连接中", input.kind === "camera" ? "等待摄像头权限和现场画面。" : "正在加载视频源，等待有效画面。");
    timeout = setTimeout(() => fail("等待画面超时。请检查地址、权限、网络与视频格式，再点击重新连接。", expected), 25000);
    const video = $("#monitorVideo"), image = $("#monitorImage");
    if (input.kind === "network" && input.format === "mjpeg") {
      image.hidden = false;
      image.onload = () => {
        if (expected !== token || !source) return;
        clearTimeout(timeout);
        ready = true;
        lastFrame = stamp();
        setStatus(source.nature === "recording" ? "回放中" : "监测中", source.nature === "recording" ? "MJPEG 录像画面已加载，按源端声明作为回放显示。" : "MJPEG 画面已加载；是否为现场直播及延迟请由源端确认。");
      };
      image.onerror = () => fail("MJPEG 画面不可用。请检查源端、播放地址和网络。", expected);
      image.src = input.url;
      return;
    }
    video.hidden = false;
    video.muted = true;
    if (input.kind === "camera") {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({audio: false, video: input.deviceId ? {deviceId: {exact: input.deviceId}} : {width:{ideal:1280},height:{ideal:720}}});
        if (expected !== token || inactive || !root.isConnected) { stream.getTracks().forEach((t) => t.stop()); return; }
        mediaStream = stream;
        source.name = stream.getVideoTracks()[0]?.label || "现场摄像头 / 采集卡";
        stream.getVideoTracks().forEach((track) => {
          track.addEventListener("ended", () => {
            if (expected === token) fail("摄像头画面中断，设备可能已断开或权限被收回。", expected);
          }, {once: true});
          track.addEventListener("mute", () => { if (expected === token) setStatus("画面中断", "设备暂未提供画面，等待视频设备恢复。"); });
          track.addEventListener("unmute", () => { if (expected === token && ready) setStatus("监测中", "摄像头画面已恢复。"); });
        });
        video.srcObject = stream;
        refreshDevices().catch(() => {});
      } catch (error) { fail(errorText(error), expected); return; }
    } else if (input.kind === "file") {
      objectURL = URL.createObjectURL(input.file);
      video.src = objectURL;
    } else video.src = input.url;
    watchFrames(expected);
    try { await video.play(); }
    catch (error) { if (expected === token && error.name === "NotAllowedError") setStatus("等待播放", "浏览器未允许自动播放，请点击画面中的播放按钮。"); }
  }
  async function refreshDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) throw Error("当前浏览器无法枚举视频设备，请通过本地启动器或 HTTPS 使用。");
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    const select = $("#monitorCamera"), old = select.value;
    select.replaceChildren(new Option("系统默认视频设备", ""), ...devices.map((d, i) => new Option(d.label || `视频设备 ${i + 1}`, d.deviceId)));
    if ([...select.options].some((opt) => opt.value === old)) select.value = old;
    if (!devices.length) alertMessage("没有检测到可列出的摄像头，请检查设备或允许摄像头权限。");
    else alertMessage("");
  }
  function download(name, blob) {
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  }
  async function capture() {
    if (!source || !ready) throw Error("请先接入并等待有效画面。");
    const expected = token;
    const image = source.format === "mjpeg" ? $("#monitorImage") : $("#monitorVideo");
    const width = source.format === "mjpeg" ? image.naturalWidth : image.videoWidth;
    const height = source.format === "mjpeg" ? image.naturalHeight : image.videoHeight;
    if (!width || !height) throw Error("当前没有可取证的画面。");
    const capturedAt = stamp();
    const positionSeconds = source.kind === "camera" || source.format === "mjpeg" ? null : image.currentTime;
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    try {
      canvas.getContext("2d").drawImage(image, 0, 0, width, height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve,"image/png"));
      if (!blob) throw Error("无法生成截图，请重新加载视频源。");
      if (token !== expected) throw Error("视频源已切换，请重新截图以避免证据关联错误。");
      const name = `现场画面_${context.batchId}_${context.taskId}_${capturedAt.replace(/[:.]/g,"-")}.png`.replace(/[<>:"/\\|?*]/g,"_");
      captures.push({file:name, capturedAt, source:source.name, kind:source.kind, batch:context.batchId, task:context.taskId, positionSeconds});
      if (captures.length > 30) captures.shift();
      log("截图取证");
      download(name,blob);
      alertMessage("已下载当前画面。可同时下载接入记录，核对截图时间、来源与任务关联。");
    } catch (error) {
      if (error.name === "SecurityError") throw Error("网络源不允许跨域取帧，浏览器禁止导出截图。请使用源端录像/截图或浏览器截图工具。");
      throw error;
    }
  }
  function record() {
    const current = {kind: source?.kind || null, name: source?.name || null, nature: source?.nature || (source?.kind === "file" ? "recording" : source?.kind === "camera" ? "live" : null), status, sessionStart, lastRenderedFrame: lastFrame, renderedFrames: frames};
    // 不导出媒体内容、设备标识、播放地址的查询参数或浏览器权限。
    download(`视频接入记录_${context.batchId}_${context.taskId}.json`, new Blob([JSON.stringify({schemaVersion:1, exportedAt:stamp(), association:context, current, captures, events, scope:"仅记录视频接入与截图信息；未生成病害识别、真实车辆控制或工程评价。"},null,2)],{type:"application/json;charset=utf-8"}));
  }
  function build() {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = markup();
    root = wrapper.firstElementChild;
    root.addEventListener("click", async (event) => {
      const mode = event.target.closest("[data-monitor-mode]");
      if (mode) {
        selectedMode = mode.dataset.monitorMode;
        root.querySelectorAll("[data-monitor-mode]").forEach((tab) => { const selected = tab === mode; tab.classList.toggle("active",selected); tab.setAttribute("aria-selected",String(selected)); });
        ["file","network","camera"].forEach((key) => { $("#monitor" + key[0].toUpperCase() + key.slice(1) + "Pane").hidden = key !== selectedMode; });
        return;
      }
      const action = event.target.closest("[data-monitor-action]")?.dataset.monitorAction;
      if (!action) return;
      try {
        alertMessage("");
        if (action === "devices") await refreshDevices();
        if (action === "camera") await start({kind:"camera", deviceId:$("#monitorCamera").value});
        if (action === "reconnect" && reconnectSource) await start(reconnectSource);
        if (action === "stop") { log("手动断开"); release(); setStatus("已断开","已停止视频并释放设备；可以重新连接。"); }
        if (action === "capture") await capture();
        if (action === "record") record();
        if (action === "fullscreen") {
          if (document.fullscreenElement) await document.exitFullscreen();
          else await $("#monitorScreen").requestFullscreen();
        }
      } catch (error) { alertMessage(errorText(error)); }
    });
    root.addEventListener("keydown", (event) => {
      const tab = event.target.closest("[data-monitor-mode]");
      if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...root.querySelectorAll("[data-monitor-mode]")];
      const index = tabs.indexOf(tab);
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click(); tabs[next].focus();
    });
    root.addEventListener("change", async (event) => {
      if (event.target.id !== "monitorFile") return;
      const file = event.target.files[0];
      if (!file) return;
      try { await start({kind:"file",file}); }
      catch (error) { alertMessage(errorText(error)); }
      event.target.value = "";
    });
    root.addEventListener("submit", async (event) => {
      if (event.target.id !== "monitorNetworkForm") return;
      event.preventDefault(); event.stopPropagation();
      try { await start({kind:"network", url:$("#monitorURL").value, format:$("#monitorFormat").value, nature:$("#monitorNature").value}); }
      catch (error) { alertMessage(errorText(error)); }
    });
    const video = $("#monitorVideo");
    video.addEventListener("loadeddata", () => {
      if (!source || source.format === "mjpeg") return;
      clearTimeout(timeout);
      ready = true;
      refresh();
    });
    video.addEventListener("playing", () => {
      if (!source || source.format === "mjpeg") return;
      clearTimeout(timeout);
      ready = true;
      log("接收到视频画面");
      setStatus(source.kind === "camera" ? "监测中" : "播放中", source.kind === "file" ? "正在播放真实录像文件；这是录像回放，不是实时采集。" : source.kind === "camera" ? "正在显示摄像头实时画面，仿真任务仍保持独立。" : source.nature === "live" ? "网络画面已播放；实时性和延迟仍需源端核实。" : "正在播放网络录像，未作为实时采集。");
      watchFrames(token);
    });
    video.addEventListener("pause", () => { if (source && ready && !inactive && !video.ended) setStatus("已暂停",source.kind === "camera" ? "仅暂停显示，摄像头仍占用；点击停止可释放设备。" : "视频已暂停；开启录像联动时，仿真车保持在对应进度。"); });
    video.addEventListener("waiting", () => { if (source && ready && !inactive) setStatus("缓冲中","等待后续画面；该状态不代表监测数据已连续采集。"); });
    video.addEventListener("stalled", () => { if (source?.kind === "network") setStatus("缓冲中","网络源暂未继续提供数据，请检查连接或重新接入。"); });
    video.addEventListener("ended", () => { if (source) { log("视频结束"); setStatus("播放结束","录像或视频流已结束，可重新播放或接入其他来源。"); } });
    video.addEventListener("seeked", refresh);
    video.addEventListener("timeupdate", () => {
      if (source && !video.requestVideoFrameCallback && !video.paused) lastFrame = stamp();
      refresh();
    });
    video.addEventListener("error", () => {
      if (!source || source.format === "mjpeg" || !video.error) return;
      const message = video.error.code === 4 ? "当前视频无法解码或格式不被浏览器支持。请使用 MP4（H.264）或 WebM；网络源请检查播放地址。" : video.error.code === 2 ? "网络视频读取失败，请检查地址、源端服务与网络。" : "视频读取或解码失败，请更换可播放的文件或来源。";
      fail(message);
    });
  }
  function mount(host, nextContext) {
    if (!root) build();
    const changed = context && (context.taskId !== nextContext.taskId || context.batchId !== nextContext.batchId);
    if (changed) {
      log("任务或批次变更，断开原视频关联");
      release(); reconnectSource = null; events = []; captures = []; sessionStart = null;
      setStatus("未接入","任务或批次已切换，请重新接入本任务的视频，避免来源混用。");
    }
    context = {...nextContext};
    inactive = false;
    host.replaceChildren(root);
    if (source && ready) watchFrames(token);
    refresh();
  }
  function beforeRender(nextRoute) {
    if (!root || !context) return;
    if (nextRoute !== "tasks" && !inactive) {
      inactive = true;
      const local = source?.kind === "file";
      const active = !!source;
      if (active) log(local ? "离开检测页，暂停录像" : "离开检测页，释放视频源");
      release({keepFile:local});
      if (active && !local) setStatus("已断开","已离开检测页并释放视频源；返回后需要手动重新连接。");
    }
  }
  window.addEventListener("pagehide", () => release());
  function evidenceSnapshot() {
    if(!source||!ready)throw Error("先接入并解码现场视频，再冻结证据帧");
    if(!source.evidenceId)throw Error("视频来源正在校验，请稍后再冻结证据帧");
    const video=$("#monitorVideo"), image=$("#monitorImage"), picture=source.format==="mjpeg"?image:video;
    const width=source.format==="mjpeg"?image.naturalWidth:video.videoWidth,height=source.format==="mjpeg"?image.naturalHeight:video.videoHeight;
    const cv=document.createElement("canvas");cv.width=Math.min(640,width);cv.height=Math.round(cv.width*height/width);
    cv.getContext("2d").drawImage(picture,0,0,cv.width,cv.height);
    let thumbnail;try{thumbnail=cv.toDataURL("image/jpeg",.72);}catch{throw Error("此视频跨域取帧受限，不能生成对应证据；请使用允许取帧的源或本地录像");}
    return {kind:"video",sourceId:source.evidenceId,sourceName:source.name,batchId:context.batchId,taskId:context.taskId,width,height,timeSec:source.kind==="file"||source.nature==="recording"?video.currentTime:null,durationSec:Number.isFinite(video.duration)?video.duration:null,frameAt:stamp(),thumbnail,identityBasis:source.identityBasis,nature:source.kind==="file"||source.nature==="recording"?"recording":"live"};
  }
  function evidenceIdentity(){const video=$("#monitorVideo");return {timeSec:source?.kind==="file"||source?.nature==="recording"?video?.currentTime??null:null,durationSec:Number.isFinite(video?.duration)?video.duration:null,sourceId:source?.evidenceId||null,batchId:context?.batchId,taskId:context?.taskId,ready};}
  function seekEvidence(anchor){
    if(!ready||anchor.sourceId!==source?.evidenceId||anchor.taskId!==context.taskId||anchor.batchId!==context.batchId)throw Error("请重新导入该证据的原视频；当前视频来源或任务不匹配");
    if(anchor.timeSec===null)throw Error("实时证据没有可拖动的录像时间，请查看保存的证据帧");
    const video=$("#monitorVideo");video.pause();video.currentTime=anchor.timeSec;refresh();
  }
  window.TunnelVideoMonitor = {mount, beforeRender, evidenceSnapshot, evidenceIdentity, seekEvidence};
})();
