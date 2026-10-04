/* 外部雷达数据：文件与网关帧都经过统一校验；接入预览不覆盖演示台账。 */
(function () {
  "use strict";
  const C = window.TunnelCore;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const now = () => new Date().toISOString();
  const bytes = text => new TextEncoder().encode(text).length;
  const sameContext = (a, b) => a?.batchId === b?.batchId && a?.taskId === b?.taskId;
  const localTime = value => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "未提供";
  function textField(value, label, limit = 256) {
    if (value == null) return "";
    if (typeof value !== "string" || value.length > limit) throw Error(`${label}须为不超过 ${limit} 字的文本。`);
    return value;
  }
  function normalizeTime(value) {
    if (value == null) return null;
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw Error("capturedAt 应为带时区的 ISO 8601 时间。");
    const [year, month, day] = value.slice(0,10).split("-").map(Number);
    const monthEnd = new Date(0); monthEnd.setUTCFullYear(year, month, 0);
    if(month<1 || month>12 || day<1 || day>monthEnd.getUTCDate()) throw Error("capturedAt 包含不存在的日历日期。");
    return new Date(value).toISOString();
  }
  function parseFrame(text, association = {}) {
    if (typeof text !== "string" || bytes(text) > C.LIMITS.bytes) throw Error("JSON 雷达帧超过 5 MB 或不是文本。");
    let payload;
    try { payload = JSON.parse(text); } catch { throw Error("JSON 雷达帧解析失败。"); }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw Error("雷达帧须为包含 matrix 的 JSON 对象。");
    if (payload.schemaVersion != null && payload.schemaVersion !== 1) throw Error("只支持 schemaVersion: 1 的雷达接入帧。");
    const matrix = payload.matrix;
    if (!Array.isArray(matrix) || matrix.length < 2 || matrix.length > C.LIMITS.rows) throw Error("matrix 采样点数须为 2～512。");
    const cols = Array.isArray(matrix[0]) ? matrix[0].length : 0;
    if (cols < 2 || cols > C.LIMITS.cols) throw Error("matrix 道数须为 2～2048。");
    matrix.forEach((row, i) => {
      if (!Array.isArray(row) || row.length !== cols) throw Error(`matrix 第 ${i + 1} 行不是矩形矩阵。`);
      row.forEach(value => { if (typeof value !== "number" || !Number.isFinite(value)) throw Error("matrix 只接受有限数值，不接受字符串、空值或文本。"); });
    });
    if (payload.metadata?.amplitudeUnit != null) textField(payload.metadata.amplitudeUnit, "amplitudeUnit", 128);
    const parsed = C.parseCSV(matrix.map(row => row.join(",")).join("\n"), payload.metadata ?? {});
    for (const key of ["batchId", "taskId"]) {
      for (const declared of [payload[key], parsed.metadata[key]]) {
        if (declared != null && declared !== association[key]) throw Error(`源端 ${key} 与当前任务关联不一致，请确认批次和任务后重新接入。`);
      }
    }
    const capturedAt = normalizeTime(payload.capturedAt ?? parsed.metadata.capturedAt);
    const frameId = payload.frameId;
    if (frameId != null && !((typeof frameId === "string" && frameId.length > 0 && frameId.length <= 128) || (typeof frameId === "number" && Number.isSafeInteger(frameId)))) throw Error("frameId 应为短文本或安全整数。");
    parsed.name = textField(payload.name, "name") || "外部雷达数据帧";
    parsed.lineId = textField(payload.lineId ?? parsed.metadata.lineId, "lineId", 128) || association.lineId || "未提供";
    parsed.capturedAt = capturedAt;
    parsed.frameId = frameId ?? null;
    parsed.declaredSource = textField(payload.source ?? parsed.metadata.source, "source") || "源端未声明；真实性未核验";
    if (!capturedAt) parsed.warnings.push("未提供源端采集时间；本机接收时间不代表现场采集时间。");
    return parsed;
  }

  let root = null, api = null, context = null, latest = null, selectedId = null;
  let mode = "file", preview = "archive", status = "未连接", detail = "导入外部文件，或填写设备 / 网关地址后开始接收。";
  let config = null, active = false, token = 0, socket = null, controller = null;
  let pollTimer = null, deadline = null, idleTimer = null, count = 0, repeated = 0, dropped = 0;
  let fingerprint = null, savedFingerprint = null, lastWS = -Infinity, events = [];
  let viewTrace = 0, gain = 1, zoom = 1;
  const $ = selector => root?.querySelector(selector);
  const recordEvent = action => { events.push({time:now(),action,batchId:context?.batchId,taskId:context?.taskId}); if (events.length > 50) events.shift(); };
  function setStatus(value, message) { status = value; detail = message; refresh(); }
  function feedback(message) { $("#radarFeedback").textContent = message; $("#radarFeedback").hidden = !message; }
  function dataset() { return preview === "live" ? latest : api?.selected(); }
  function markup() {
    return `<section class="panel radar-acquisition" id="radarAcquisition" aria-label="外部雷达数据接入">
      <div class="panel-head"><h2>雷达采集 · 外部数据</h2><span class="badge blue">01 / 外部数据接入</span></div>
      <div class="panel-body">
        <p class="radar-input-intro">先查看外部原始矩阵与接入帧，再与下方程序示意信号对照。接入只解析和展示数据，不生成病害诊断。</p>
        <div class="radar-input-tabs" role="tablist" aria-label="雷达接入方式">
          <button type="button" role="tab" aria-selected="true" id="radarFileTab" aria-controls="radarFilePane" data-radar-mode="file" class="active">外部文件导入</button>
          <button type="button" role="tab" aria-selected="false" id="radarNetworkTab" aria-controls="radarNetworkPane" data-radar-mode="network">设备 / 网关数据源</button>
        </div>
        <div id="radarFilePane" class="radar-source-pane" role="tabpanel" aria-labelledby="radarFileTab">
          <div class="radar-file-grid"><div><label class="field">CSV 矩阵 / JSON 数据帧<input type="file" id="csvFile" accept=".csv,.json" aria-label="选择CSV或JSON雷达文件"></label><p class="radar-input-help">行 = 采样点，列 = 道。CSV 为无表头纯数值矩阵；JSON 格式见接入样例。文件只在本机解析。</p></div><div><label class="field">CSV 标定元数据（可选）<input type="file" id="metadataFile" accept=".json" aria-label="选择JSON元数据文件"></label><textarea id="metadata" rows="4" aria-label="JSON元数据内容" placeholder='{"traceSpacingM":0.02,"sampleIntervalNs":0.1,"epsilon":6}'></textarea></div></div>
          <div class="actions"><button type="button" class="primary" data-action="import-csv">解析并导入</button><button type="button" class="subtle small" data-action="sample-csv">下载 CSV 样例</button><a class="radar-sample-link" href="examples/radar_stream_sample.json" download>下载 JSON 接入样例</a><button type="button" class="small subtle" data-action="invalid-demo">检查示例错误</button></div>
          <div id="importMessage" role="alert"></div>
        </div>
        <div id="radarNetworkPane" class="radar-source-pane" role="tabpanel" aria-labelledby="radarNetworkTab" hidden>
          <form id="radarLiveForm" class="radar-network-form"><label class="field radar-endpoint">设备 / 网关地址<input id="radarEndpoint" required type="text" placeholder="http(s)://网关/雷达帧 或 ws(s)://网关/数据流" autocomplete="off" spellcheck="false"></label><label class="field">接入方式<select id="radarTransport"><option value="http">HTTP(S) JSON 帧轮询</option><option value="websocket">WebSocket JSON 帧</option></select></label><label class="field">轮询间隔 / 秒<input id="radarInterval" type="number" min="1" max="30" step="1" value="2"></label><button type="submit" class="primary">连接数据源</button><button type="button" class="subtle" data-radar-action="stop">停止接入</button><button type="button" class="subtle" data-radar-action="reconnect">重新连接</button></form>
          <p class="radar-input-help">源端每次提供一个完整矩阵快照，格式为 JSON 接入样例；不支持直接读取 DZT / DT1、串口或专有雷达协议。HTTP 跨域接入需网关允许浏览器读取；HTTPS 页面使用 HTTPS / WSS 源。WebSocket 最多预览每秒 5 帧，超出帧不展示或归档。</p>
        </div>
        <div class="radar-input-status" role="status" aria-live="polite"><i id="radarReceiveDot"></i><b id="radarReceiveStatus"></b><span id="radarReceiveMessage"></span></div>
        <div class="radar-source-controls"><div id="radarArchiveSelector"></div><label>当前预览<select id="radarPreview"><option value="archive">已导入 / 已保存的数据</option><option value="live">设备 / 网关接入帧</option></select></label><label>外部图色标<select id="externalPalette"><option value="1">标准 ×1</option><option value="2">增强 ×2</option><option value="0.5">柔和 ×0.5</option></select></label><label>外部图横向缩放<select id="externalZoom"><option value="1">1 倍</option><option value="2">2 倍</option><option value="4">4 倍</option></select></label><label>外部单道序号<input id="externalTrace" type="number" min="0" value="0" step="1"></label></div>
        <div class="radar-external-view"><div id="radarInputEmpty" class="empty"><b>尚未载入外部雷达数据</b>选择文件并解析，或接收到合法的网关数据帧后显示；下方仿真示例始终独立保留。</div><div id="radarInputCanvases" hidden><div class="radar-wrap"><canvas id="externalRawRadar" aria-label="外部雷达B-scan"></canvas></div><div class="radar-caption"><span id="externalDataName"></span><span id="externalMatrixSize"></span></div><div class="radar-wrap wave"><canvas id="externalWave" aria-label="外部雷达单道波形"></canvas></div><div class="radar-caption"><span id="externalTraceLabel"></span><span>点击剖面切换单道 · 波形按本道最大绝对值归一化</span></div></div></div>
        <div class="radar-input-meta" id="radarInputMetadata"></div><div class="note info" id="radarInputWarnings" hidden></div>
        <div class="actions"><button type="button" class="primary" data-radar-action="save">保存本帧到数据目录</button><button type="button" class="subtle" data-radar-action="csv">下载当前矩阵 CSV</button><button type="button" class="subtle" data-radar-action="json">下载数据与接入记录 JSON</button><button type="button" class="subtle" data-action="go-processing">进入智能处理</button></div>
        <p class="radar-input-boundary">智能处理使用“当前数据”选中的目录快照。网络预览不会自动写入目录、替换已处理输入、生成病害或改变 SHI；先保存本帧，再进入处理。来源真实性、采集时间和物理标定仍需源端核验。</p>
        <div class="radar-directory"><h3>外部数据目录 · 当前批次</h3><div id="radarExternalDirectory"></div></div>
        <div id="radarFeedback" class="radar-feedback" role="alert" hidden></div>
      </div></section>`;
  }
  function refresh() {
    if (!root || !api) return;
    $("#radarReceiveStatus").textContent = status;
    $("#radarReceiveMessage").textContent = detail;
    $("#radarReceiveDot").dataset.state = active && status === "接收中" ? "active" : /失败|异常/.test(status) ? "error" : "idle";
    $("[data-radar-action=stop]").disabled = !active;
    $("[data-radar-action=reconnect]").disabled = !config;
    const data = dataset();
    $("#radarInputEmpty").hidden = !!data;
    $("#radarInputCanvases").hidden = !data;
    $("#radarPreview").value = preview;
    $("[data-radar-action=save]").disabled = !latest || preview !== "live" || savedFingerprint === fingerprint;
    for (const action of ["csv", "json"]) $(`[data-radar-action=${action}]`).disabled = !data;
    $("#externalTrace").disabled = !data;
    if (!data) { $("#radarInputMetadata").replaceChildren(); $("#radarInputWarnings").hidden = true; return; }
    viewTrace = Math.min(Math.max(0,viewTrace), data.cols-1);
    $("#externalTrace").value = viewTrace; $("#externalTrace").max = data.cols-1;
    $("#externalDataName").textContent = data.name;
    $("#externalMatrixSize").textContent = `${data.rows} 点 × ${data.cols} 道 · ${preview === "live" ? (active ? "接入帧（未自动归档）" : "停止后的最后有效帧") : "外部数据快照"}`;
    $("#externalTraceLabel").textContent = `单道 #${viewTrace} · ${data.units?.amplitude || "设备振幅（未标定）"}`;
    const metadata=data.metadata || {}, provenance=data.provenance || {};
    const stats=preview === "live" ? connectionRecord().connection : data.connectionRecord?.connection;
    const cells = [
      ["关联任务 / 批次", `${data.taskId || context.taskId} / ${data.batchId || context.batchId}`], ["测线 / 来源声明", `${data.lineId || metadata.lineId || "未提供"} / ${provenance.declaredSource || data.declaredSource || metadata.source || "未声明，真实性未核验"}`],
      ["源端采集时间（待核验）",localTime(data.capturedAt)], ["本机接收 / 导入时间",localTime(data.receivedAt || data.importedAt)],
      ["帧编号 / 对应接收统计",`${data.frameId ?? "未提供"}${stats ? ` · 有效 ${stats.acceptedFrames} / 重复 ${stats.repeatedFrames} / 跳过 ${stats.skippedDisplayFrames}` : " · 文件快照"}`], ["道间距 / 时间标定",`${metadata.traceSpacingM ? metadata.traceSpacingM+" m" : "未标定（道号）"} / ${metadata.sampleIntervalNs ? metadata.sampleIntervalNs+" ns" : metadata.timeWindowNs ? "时间窗 "+metadata.timeWindowNs+" ns" : "未标定（采样点）"}`],
      ["时间窗 / 振幅单位", `${metadata.timeWindowNs ? metadata.timeWindowNs+" ns" : "未提供"} / ${metadata.amplitudeUnit || "未标定"}`],
      ["介电参数 / 矩阵完整性",`${metadata.epsilon || "未提供"} / 矩形 · 有限数值`], ["当前预览来源",preview === "live" ? `${active ? "网络接入" : "已断开，保留最后帧"} · ${provenance.transport || "网关"}` : `${provenance.transport || "外部文件"} · 已载入目录`],
    ];
    $("#radarInputMetadata").innerHTML=cells.map(([label,value])=>`<div><small>${esc(label)}</small><b>${esc(value)}</b></div>`).join("");
    $("#radarInputWarnings").textContent=(data.warnings || []).join(" ");
    $("#radarInputWarnings").hidden=!data.warnings?.length;
    api.paint(data, {trace:viewTrace,gain,zoom,raw:$("#externalRawRadar"),wave:$("#externalWave")});
  }
  function stop(reason = "已停止接入；最后有效帧保留，可下载或手动保存。") {
    token++; active=false;
    clearTimeout(pollTimer);clearTimeout(deadline);clearTimeout(idleTimer);
    if(controller){controller.abort();controller=null;}
    if(socket){const old=socket;socket=null;old.close();}
    status="已停止";detail=reason;refresh();
  }
  function fail(error, expected) {
    if(expected!==token)return;
    recordEvent("接收失败："+error.message);
    stop();setStatus("接收失败",error.message+" 最后有效帧不会被替换。请检查源端后重新连接。");
    feedback(error.message);
  }
  function safeURL(value, transport) {
    let url;try{url=new URL(value.trim());}catch{throw Error("请填写完整的设备 / 网关地址。");}
    const protocols=transport==="websocket"?["ws:","wss:"]:["http:","https:"];
    if(!protocols.includes(url.protocol))throw Error(transport==="websocket"?"WebSocket 需要 ws:// 或 wss:// JSON 数据源。":"HTTP 轮询需要 http:// 或 https:// JSON 数据源。");
    if(url.username || url.password)throw Error("接入地址中不要填写账号密码；请使用网关授权的接入地址。");
    if(location.protocol==="https:" && ["http:","ws:"].includes(url.protocol))throw Error("HTTPS 页面请使用 HTTPS / WSS 数据源，局域网明文源请在本地启动器中接入。");
    return url.href;
  }
  function canonicalMetadata(value) {
    if(Array.isArray(value)) return value.map(canonicalMetadata);
    if(value && typeof value==="object") return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonicalMetadata(value[key])]));
    return value;
  }
  function connectionRecord() {
    return {connection:{status,acceptedFrames:count,repeatedFrames:repeated,skippedDisplayFrames:dropped},events:events.map(event=>({...event}))};
  }
  function accept(text, expected, settings) {
    if(expected!==token || !active)return;
    const data=parseFrame(text, context);
    // 标识不包含接收时间：同一帧重复轮询不能伪装成新采集数据。
    const key=JSON.stringify([data.frameId,data.capturedAt,data.matrix,canonicalMetadata(data.metadata),data.lineId,data.declaredSource,data.name]);
    if(key===fingerprint){repeated++;setStatus("等待新帧","收到重复帧，保留原采集 / 接收时间，等待源端更新。");return;}
    data.receivedAt=now();data.batchId=context.batchId;data.taskId=context.taskId;
    const url=new URL(settings.url);
    data.provenance={transport:settings.transport,endpoint:url.origin+url.pathname,declaredSource:data.declaredSource,adapterVersion:"radar-frame-1.0"};
    latest=data;fingerprint=key;count++;clearTimeout(deadline);clearTimeout(idleTimer);
    recordEvent("接收到合法数据帧 "+(data.frameId ?? count));
    setStatus("接收中","已收到并校验外部矩阵；当前仅预览，保存本帧后才能进入目录处理。");
    if(settings.transport==="websocket")idleTimer=setTimeout(()=>{if(expected===token && active)setStatus("等待新帧","15 秒未收到新帧，当前显示最后有效帧；源端采集与延迟需核验。");},15000);
  }
  async function boundedText(response) {
    if(Number(response.headers.get("content-length"))>C.LIMITS.bytes)throw Error("源端雷达帧超过 5 MB 上限。");
    const reader=response.body?.getReader();
    if(!reader){const text=await response.text();if(bytes(text)>C.LIMITS.bytes)throw Error("雷达帧超过 5 MB 上限。");return text;}
    const chunks=[];let length=0;
    try{
      while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>C.LIMITS.bytes){await reader.cancel();throw Error("源端雷达帧超过 5 MB 上限。");}chunks.push(value);}
    }finally{reader.releaseLock();}
    const body=new Uint8Array(length);let offset=0;chunks.forEach(chunk=>{body.set(chunk,offset);offset+=chunk.length;});
    return new TextDecoder("utf-8",{fatal:true}).decode(body);
  }
  async function connect(settings) {
    const url=safeURL(settings.url,settings.transport);
    const interval=Number(settings.interval);
    if(!Number.isInteger(interval) || interval<1 || interval>30)throw Error("轮询间隔须为 1～30 的整数秒。");
    stop();latest=null;fingerprint=null;savedFingerprint=null;count=0;repeated=0;dropped=0;lastWS=-Infinity;events=[];
    config={...settings,url,interval};active=true;preview="live";viewTrace=0;feedback("");recordEvent("开始接入 "+settings.transport);
    const expected=token;setStatus("连接中","等待源端提供合法数据帧；连接建立不等于采集成功。");
    if(settings.transport==="websocket"){
      deadline=setTimeout(()=>fail(Error("等待首帧超时，请检查数据源和帧格式。"),expected),15000);
      try{socket=new WebSocket(url);}catch(error){fail(error,expected);return;}
      socket.addEventListener("message",event=>{
        if(expected!==token)return;
        if(performance.now()-lastWS<200){dropped++;return;}
        lastWS=performance.now();
        try{if(typeof event.data!=="string")throw Error("只支持 WebSocket 文本 JSON 帧，不支持二进制雷达流。");accept(event.data,expected,config);}catch(error){fail(error,expected);}
      });
      socket.addEventListener("error",()=>fail(Error("WebSocket 连接失败，请检查地址、网关服务与源端授权。"),expected));
      socket.addEventListener("close",()=>{if(expected===token){recordEvent("源端关闭连接");stop("源端连接已关闭，保留最后有效帧；返回后可手动重连。");}});
      return;
    }
    const poll=async()=>{
      if(expected!==token || !active)return;
      const current=new AbortController();controller=current;
      let timedOut=false;
      const requestDeadline=setTimeout(()=>{timedOut=true;current.abort();},8000);
      deadline=requestDeadline;
      try{
        const response=await fetch(url,{signal:current.signal,cache:"no-store",credentials:"omit",mode:"cors",redirect:"error"});
        if(!response.ok)throw Error(`网关返回 HTTP ${response.status}，未读取为雷达数据。`);
        const text=await boundedText(response);
        clearTimeout(requestDeadline);if(expected!==token)return;
        accept(text,expected,config);
        if(expected===token && active)pollTimer=setTimeout(poll,interval*1000);
      }catch(error){if(expected!==token)return;fail(Error(timedOut?"读取雷达帧超时（8 秒），请检查源端。":error.name==="TypeError"?"无法读取网络雷达源，请检查地址、网络和网关跨域许可。":error.message),expected);}
      finally{clearTimeout(requestDeadline);if(controller===current)controller=null;}
    };
    await poll();
  }
  function download(name, content, type) {
    const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);
  }
  function exportData(format) {
    const data=dataset();if(!data)throw Error("请先导入或接收合法数据。");
    const history=preview === "live" ? connectionRecord() : data.connectionRecord || {connection:{status:"文件快照"},events:[{time:data.importedAt,action:"导入文件快照",batchId:data.batchId,taskId:data.taskId}]};
    const filename=`雷达数据_${data.batchId}_${data.taskId}_${data.frameId ?? data.id ?? "snapshot"}`.replace(/[<>:"/\\|?*]/g,"_");
    if(format==="csv")download(filename+".csv",data.matrix.map(row=>row.join(",")).join("\r\n"),"text/csv;charset=utf-8");
    else download(filename+".json",JSON.stringify({schemaVersion:1,matrix:data.matrix,metadata:data.metadata,name:data.name,source:data.provenance?.declaredSource || data.declaredSource || data.metadata?.source || "外部数据，真实性未核验",batchId:data.batchId,taskId:data.taskId,lineId:data.lineId,frameId:data.frameId ?? null,capturedAt:data.capturedAt || undefined,receivedAt:data.receivedAt || data.importedAt,importedAt:data.importedAt || null,provenance:data.provenance,connection:history.connection,events:history.events,exportedAt:now(),scope:"原始矩阵与接入追溯记录；不含病害识别和真实设备控制。"},null,2),"application/json;charset=utf-8");
    feedback("已下载当前矩阵；请同时保存 JSON 标定与来源记录。项目报告不会附带原始矩阵。");
  }
  function build() {
    const holder=document.createElement("div");holder.innerHTML=markup();root=holder.firstElementChild;
    root.addEventListener("submit",async event=>{
      if(event.target.id!=="radarLiveForm")return;event.preventDefault();event.stopPropagation();
      try{await connect({url:$("#radarEndpoint").value,transport:$("#radarTransport").value,interval:$("#radarInterval").value});}catch(error){feedback(error.message);}
    });
    root.addEventListener("click",async event=>{
      const tab=event.target.closest("[data-radar-mode]");
      if(tab){mode=tab.dataset.radarMode;preview=mode==="file"?"archive":"live";root.querySelectorAll("[data-radar-mode]").forEach(item=>{item.classList.toggle("active",item===tab);item.setAttribute("aria-selected",String(item===tab));});$("#radarFilePane").hidden=mode!=="file";$("#radarNetworkPane").hidden=mode!=="network";refresh();return;}
      const action=event.target.closest("[data-radar-action]")?.dataset.radarAction;if(!action)return;
      try{
        feedback("");
        if(action==="stop"){recordEvent("手动停止接入");stop();}
        if(action==="reconnect" && config)await connect(config);
        if(action==="save" && latest){const copy=JSON.parse(JSON.stringify(latest));copy.connectionRecord=connectionRecord();api.saveFrame(copy,{...context});savedFingerprint=fingerprint;feedback("本帧已保存到当前任务的数据目录，并选为智能处理输入。网络预览保持独立。");refresh();}
        if(action==="csv" || action==="json")exportData(action);
      }catch(error){feedback(error.message);}
    });
    root.addEventListener("change",event=>{
      const el=event.target;
      if(el.id==="radarPreview"){preview=el.value;viewTrace=0;refresh();}
      if(el.id==="externalPalette"){gain=Number(el.value);refresh();}
      if(el.id==="externalZoom"){zoom=Number(el.value);refresh();}
      if(el.id==="externalTrace"){
        const value=Number(el.value), data=dataset();
        if(!data || !Number.isInteger(value) || value<0 || value>=data.cols){feedback("外部单道序号超出有效范围。");el.value=viewTrace;return;}
        viewTrace=value;refresh();
      }
      if(el.id==="radarTransport")$("#radarInterval").disabled=el.value==="websocket";
    });
    root.addEventListener("keydown",event=>{
      const tab=event.target.closest("[data-radar-mode]");if(!tab || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
      event.preventDefault();const tabs=[...root.querySelectorAll("[data-radar-mode]")];const next=event.key==="Home"?0:event.key==="End"?tabs.length-1:(tabs.indexOf(tab)+1)%tabs.length;tabs[next].click();tabs[next].focus();
    });
    $("#externalRawRadar").addEventListener("click",event=>{
      const data=dataset();if(!data)return;const box=event.currentTarget.getBoundingClientRect();
      viewTrace=Math.round(Math.max(0,Math.min(1,(((event.clientX-box.left)/box.width)*640-45)/583))*(data.cols-1));refresh();
    });
  }
  function updateContext(next) {
    if(context && !sameContext(context,next)){
      stop("任务或批次已切换，已断开旧来源；请重新接入，避免证据串用。");
      latest=null;config=null;fingerprint=null;savedFingerprint=null;events=[];count=0;repeated=0;dropped=0;preview="archive";selectedId=null;
    }
    context={...next};
  }
  function mount(host, callbacks) {
    api=callbacks;if(!root)build();updateContext(api.context);
    const selected=api.selected();
    if(selected && selected.id!==selectedId){selectedId=selected.id;preview="archive";viewTrace=0;}
    $("#radarArchiveSelector").innerHTML=api.options();
    $("#radarExternalDirectory").innerHTML=api.directory();
    if(!$("#metadata").value && api.metadataText)$("#metadata").value=api.metadataText;
    $("#importMessage").textContent=api.importMessage || "";
    host.replaceChildren(root);refresh();
  }
  function beforeRender(nextRoute,nextContext) {
    if(!root)return;updateContext(nextContext);
    if(nextRoute!=="radar" && active){recordEvent("离开雷达页，停止网络接入");stop("已离开雷达页并停止接入，返回后手动重连；最后有效帧仍可下载。");}
  }
  window.addEventListener("pagehide",()=>{if(root)stop("页面已关闭，接入已停止。");});
  window.TunnelRadarAcquisition={mount,beforeRender,parseFrame,normalizeTime};
})();
