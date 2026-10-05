/* 疑似位置常驻复核：仅采集位置；人工示例以完整视频 SHA-256 匹配，不冒充识别结果。 */
(function () {
  "use strict";
  const E = window.TunnelEvidenceCore;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  // 这些时间/像素点经过本地人工查看，含接缝与表观变化，均不是确诊病害或真值。
  // 只发布位置索引；原视频仍由操作者在本机导入，不随网页分发。
  const EXAMPLES = [
    {sha:"63d65b435539fb430a44e99a93a019a880ce8ab72946bea61158bd09c7f8474b",name:"轨道隧道扫描 · 设备近景",points:[[5,.61,.66],[12,.68,.85],[20,.62,.55]]},
    {sha:"767d22e5a763c89724b308f1ad3c8d584276e676c67f6f671b2abc38f8152a3a",name:"轨道隧道扫描 · 人员跟拍",points:[[1,.59,.53],[3,.62,.58],[5,.69,.56]]},
    {sha:"70a85ed32a28a79efdd07dfe4e20dc1aa001576062fe5a05d7d7dd1b8c30fa8b",name:"公路隧道车载雷达 · H.264 回放",points:[[10,.72,.58],[14,.17,.72],[18,.73,.40]]}
  ];
  let host = null, root = null, api = null, dialog = null, busy = false, serial = 0;
  let sourceId = null, listKey = "", previous = null, stopAtPoints = false, stopping = false;
  const $ = selector => root?.querySelector(selector);
  const identity = () => window.TunnelVideoMonitor.evidenceIdentity();
  const live = () => !!root?.isConnected && api?.route === "tasks";
  const isRecording = d => d.ready && d.sourceId && Number.isFinite(d.timeSec) && Number.isFinite(d.durationSec) && d.durationSec > 0;
  function message(text, error = false) {
    const el = $("#taskPositionMessage"); if (!el) return;
    el.textContent = text; el.classList.toggle("error", error);
  }
  function items(d) {
    const sorted = E.visible(api.state, api.context).slice().sort((a,b) => a.mileage-b.mileage);
    return sorted.map((record,index) => ({record,sequence:index+1,anchor:record.anchors.find(a => a.kind === "video" && a.sourceId === d.sourceId && Number.isFinite(a.timeSec))}))
      .filter(item => item.anchor).sort((a,b) => a.anchor.timeSec-b.anchor.timeSec || a.sequence-b.sequence);
  }
  function currentSourceItems(d) {
    return E.records(api.state,api.context).filter(r => r.anchors.some(a => a.kind === "video" && a.sourceId === d.sourceId));
  }
  function example(d) { return EXAMPLES.find(e => "VF-"+e.sha === d.sourceId); }
  function sameContext(ticket) {
    const d = identity();
    return live() && ticket.serial === serial && ticket.state === api.state && d.ready && d.sourceId === ticket.sourceId
      && api.context.batchId === ticket.batchId && api.context.taskId === ticket.taskId
      && api.context.start === ticket.start && api.context.end === ticket.end;
  }
  function ticket() { return {serial,state:api.state,sourceId:identity().sourceId,...api.context}; }
  function ensure(ticket) { if (!sameContext(ticket)) throw Error("视频或检测任务已更换，请重新选择当前视频的位置。"); }
  function disposeDialog() {
    if (!dialog) return;
    const old = dialog; dialog = null; if (old.open) old.close(); old.remove();
  }
  function markup() {
    return `<section class="position-review" id="taskPositionReview" aria-label="本视频疑似位置复核">
      <div class="position-review-head"><div><b>疑似位置复核</b><span id="taskPositionCount" class="badge mint">待接入视频</span><small>只标位置 · 类型待识别</small></div>
        <div class="position-review-actions"><label><input id="taskFollowVideo" type="checkbox">录像联动车辆</label><label><input id="taskReviewStops" type="checkbox">到点暂停复核</label><button type="button" class="small primary" id="taskQuickMark" data-position-action="mark" disabled>标记疑似位置</button><button type="button" class="small subtle" id="taskLoadPositionExample" data-position-action="example" hidden>载入本视频示例位置</button></div>
      </div>
      <div class="position-review-status"><output id="taskPositionProgress">先接入原始视频，再标记待复核位置。</output><span id="taskPositionCue">记录将与右侧仿真共用编号和位置。</span></div>
      <div class="position-review-timeline" id="taskPositionTimeline" aria-label="按视频出现顺序排列的疑似位置"></div>
      <p id="taskPositionMessage" class="position-review-message" role="status" aria-live="polite">选择需要复核的接缝或表观异常，在原帧点击选点；这里不判断病害类型。</p>
    </section>`;
  }
  function mount(nextHost, nextApi) {
    if (!nextHost || nextApi.route !== "tasks") {
      if (host) {serial++;disposeDialog();host=null;root=null;busy=false;previous=null;}
      api=nextApi; return;
    }
    if (host !== nextHost || api?.state !== nextApi.state || E.key(api.context) !== E.key(nextApi.context)) {
      serial++;disposeDialog();host=nextHost;api=nextApi;busy=false;sourceId=null;listKey="";previous=null;stopAtPoints=false;stopping=false;
      root=document.createElement("div");root.innerHTML=markup();host.replaceChildren(root);
      root.addEventListener("click", event => {
        const button=event.target.closest("[data-position-action]");if(!button || button.disabled)return;
        const action=button.dataset.positionAction;
        Promise.resolve().then(() => action==="mark"?mark():action==="example"?loadExample():openPoint(button.dataset.id)).catch(e=>message(e.message,true));
      });
      $("#taskFollowVideo").addEventListener("change",event=>{api.setFollow(event.target.checked);update();});
      $("#taskReviewStops").addEventListener("change",event=>{stopAtPoints=event.target.checked;previous=null;if(stopAtPoints)api.setFollow(true);message(stopAtPoints?"播放录像后，会在每个已记录的位置停到原帧；点击视频播放继续下一处。":"已关闭到点暂停，可连续播放并查看疑似点顺序。");update();});
      const video=document.querySelector("#monitorVideo");
      video?.addEventListener("seeking",()=>{previous=null;});
    } else api=nextApi;
    update();
  }
  function update() {
    if (!live()) return;
    const d=identity(), recording=isRecording(d), list=items(d);
    if (sourceId !== d.sourceId) {
      serial++;disposeDialog();sourceId=d.sourceId;busy=false;previous=null;stopAtPoints=false;listKey="";
      message(example(d)?"本视频有 3 个已人工选定的复核示例，点击“载入本视频示例位置”即可对照；不是自动识别结果。":"选择需要复核的接缝或表观异常，在原帧点击选点；这里不判断病害类型。");
    }
    $("#taskFollowVideo").checked=api.getFollow();
    $("#taskReviewStops").checked=stopAtPoints;
    $("#taskReviewStops").disabled=!recording || !list.length || busy;
    $("#taskQuickMark").disabled=!d.ready || !d.sourceId || busy;
    const load=$("#taskLoadPositionExample");load.hidden=!example(d);load.disabled=busy || !recording;load.textContent=busy?"正在取帧…":"载入本视频示例位置";
    $("#taskPositionCount").textContent=d.ready?`本视频 ${currentSourceItems(d).length} 处` : "待接入视频";
    const progress=$("#taskPositionProgress"),cue=$("#taskPositionCue");
    if (recording) {
      const mileage=api.context.start+d.timeSec/d.durationSec*(api.context.end-api.context.start);
      progress.textContent=`录像 ${d.timeSec.toFixed(2)} s / ${d.durationSec.toFixed(2)} s · 相对里程 ${mileage.toFixed(3)} m`;
      const near=list.find(x=>Math.abs(x.anchor.timeSec-d.timeSec)<=.12),next=list.find(x=>x.anchor.timeSec>d.timeSec+.12);
      if(near&&near.anchor.mappingRule!=="relative-order-1.0")progress.textContent=`录像 ${d.timeSec.toFixed(2)} s / ${d.durationSec.toFixed(2)} s · 记录里程 ${near.record.mileage.toFixed(3)} m（人工 / 关联）`;
      cue.textContent=near?`本帧疑似点 #${near.sequence} · ${near.record.id}`:next?`下一处 #${next.sequence} · ${next.anchor.timeSec.toFixed(2)} s · ${next.record.mileage.toFixed(3)} m`:list.length?"本视频已记录位置均已经过，可点击编号回看。":"本视频尚无位置标记；点击“标记疑似位置”或载入已复核示例。";
      cue.dataset.currentId=near?.record.id || "";
    } else {progress.textContent=d.ready?"实时源可冻结画面选点；工程位置请在证据面板填写。":"先接入原始视频，再标记待复核位置。";cue.textContent="同一编号对应原帧与右侧仿真；相对位置不是实测里程。";cue.dataset.currentId="";}
    const key=JSON.stringify(list.map(x=>[x.record.id,x.sequence,x.anchor.timeSec,x.record.mileage]));
    if(key!==listKey){
      listKey=key;
      $("#taskPositionTimeline").innerHTML=list.length?list.map(x=>`<button type="button" class="position-review-point" data-position-action="point" data-id="${escape(x.record.id)}" data-time="${x.anchor.timeSec}" title="回到原帧，并对齐仿真疑似位置 ${escape(x.record.id)}"><b><i></i>#${x.sequence} 疑似点</b><span>${x.anchor.timeSec.toFixed(2)} s · ${x.record.mileage.toFixed(3)} m</span></button>`).join(""):'<span class="position-review-empty">暂无本视频的标记。记录或导入后，按出现先后显示在这里。</span>';
    }
    root.querySelectorAll(".position-review-point").forEach(button=>{const at=Number(button.dataset.time),near=recording&&Math.abs(at-d.timeSec)<=.12;button.classList.toggle("is-current",near);button.classList.toggle("is-passed",recording&&at<d.timeSec-.12);button.setAttribute("aria-current",near?"step":"false");button.disabled=busy;});
    // 视频只能在保存的帧上精确标点。顺序条常驻；到点暂停停回原帧，不虚构跨帧追踪。
    const video=document.querySelector("#monitorVideo"), last=previous;previous=recording?{source:d.sourceId,time:d.timeSec}:null;
    if(stopAtPoints&&api.getFollow()&&recording&&video&&!video.paused&&!video.seeking&&!busy&&!stopping&&last?.source===d.sourceId&&d.timeSec>=last.time){
      const crossed=list.find(x=>x.anchor.timeSec>last.time+.001&&x.anchor.timeSec<=d.timeSec);
      if(crossed){stopping=true;openPoint(crossed.record.id).then(()=>message(`已停在疑似点 #${crossed.sequence} 的原帧，左右位置已对齐；点击视频播放继续下一处。`)).catch(e=>message(e.message,true)).finally(()=>{stopping=false;});}
    }
  }
  async function seek(time, t) {
    ensure(t);const video=document.querySelector("#monitorVideo");
    await new Promise((resolve,reject)=>{
      let timer;const cleanup=()=>{clearTimeout(timer);video.removeEventListener("seeked",done);video.removeEventListener("error",failed);video.removeEventListener("canplay",done);document.removeEventListener("tunnel-monitor-state",changed);};
      const done=()=>{if(video.seeking||video.readyState<2||Math.abs(video.currentTime-time)>.02)return;cleanup();resolve();};
      const failed=()=>{cleanup();reject(Error("录像帧未能解码，请重试或使用可播放的本地录像。"));};
      const changed=()=>{if(!sameContext(t)){cleanup();reject(Error("视频或检测任务已更换，本次取帧已取消，未写入位置。"));}};
      video.addEventListener("seeked",done);video.addEventListener("error",failed);video.addEventListener("canplay",done);document.addEventListener("tunnel-monitor-state",changed);timer=setTimeout(failed,20000);
      try{window.TunnelVideoMonitor.seekEvidence({sourceId:t.sourceId,taskId:t.taskId,batchId:t.batchId,timeSec:time});done();}catch(e){cleanup();reject(e);}
    });
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));ensure(t);
  }
  async function openPoint(id) {
    if(busy)return;
    const t=ticket(), record=E.records(api.state,api.context).find(r=>r.id===id),anchor=record?.anchors.find(a=>a.kind==="video"&&a.sourceId===t.sourceId&&Number.isFinite(a.timeSec));
    if(!anchor)throw Error("此位置不属于当前录像，请先导入对应的原视频。");
    E.setSource(api.state,api.context,t.sourceId);E.setMode(api.state,api.context,"evidence");api.changed();
    await seek(anchor.timeSec,t);if(anchor.mappingRule!=="relative-order-1.0")stopAtPoints=false;api.select(record.id,true);update();if(anchor.mappingRule!=="relative-order-1.0")message("已按本点的人工工程坐标定位；相对进度联动已关闭，保持修订后的位置。");
  }
  async function loadExample() {
    const t=ticket(), preset=example(identity());if(!preset||!isRecording(identity()))throw Error("请导入已验证的隧道示例原文件；改名可以，文件内容必须一致。");
    const before=JSON.stringify(api.state.evidenceRecords), staged={...api.state,evidenceRecords:JSON.parse(before),evidenceModes:{...api.state.evidenceModes},evidenceSourceFilters:{...api.state.evidenceSourceFilters}};
    busy=true;update();const added=[];let first;
    try {
      for(const [index,[time,u,v]] of preset.points.entries()){
        ensure(t);const existing=E.records(staged,api.context).find(r=>r.anchors.some(a=>a.kind==="video"&&a.sourceId===t.sourceId&&Math.abs(a.timeSec-time)<.02&&Math.abs(a.u-u)<.006&&Math.abs(a.v-v)<.006));
        if(existing){first=first||existing;continue;}
        message(`正在从本视频提取第 ${index+1} / ${preset.points.length} 处原帧：${time.toFixed(2)} s，解码完成后才保存。`);
        await seek(time,t);const a=window.TunnelVideoMonitor.evidenceSnapshot();a.u=u;a.v=v;a.mappingRule="relative-order-1.0";
        const loc=E.relativeVideo(staged.project,api.context,time,a.durationSec,u,60,120);loc.positionBasis+="；人工预选的隧道复核示例，未判断类型";
        const r=E.add(staged,api.context,{...loc,type:null},a);first=first||r;added.push(r);
      }
      // 首帧也先完成解码再整批写入；界面在整次取帧和定位结束前保持加载态。
      if(first){message("各位置原帧已提取，正在回到第一处复核位置…");await seek(first.anchors.find(a=>a.kind==="video"&&a.sourceId===t.sourceId).timeSec,t);}
      ensure(t);if(JSON.stringify(api.state.evidenceRecords)!==before)throw Error("加载期间位置记录发生变化，本次未写入，请重新加载。");
      api.state.evidenceRecords=staged.evidenceRecords;if(first)api.state.evidenceSelectedId=first.id;E.setMode(api.state,api.context,"evidence");E.setSource(api.state,api.context,t.sourceId);api.save();api.changed();api.setFollow(true);
      if(first)api.select(first.id,true);
      message(added.length?`已载入 ${added.length} 个人工复核示例位置；编号和位置已同步到仿真。可开启“到点暂停复核”。这些位置不是确诊病害。`:"本视频的示例位置已经存在，未重复添加；已回到第一处原帧。");
    } finally {if(t.serial===serial){busy=false;update();}}
  }
  async function mark() {
    const d=identity();if(!isRecording(d)){
      const drawer=document.querySelector("#taskEvidenceDrawer");if(drawer)drawer.open=true;
      drawer?.querySelector('[data-evidence-action="capture-video"]')?.click();drawer?.scrollIntoView({block:"start",behavior:"auto"});
      message("实时画面没有固定录像里程，请在已打开的证据面板选点并填写人工工程位置。");return;
    }
    const t=ticket();await seek(d.timeSec,t);const anchor=window.TunnelVideoMonitor.evidenceSnapshot();
    disposeDialog();dialog=document.createElement("dialog");dialog.className="position-mark-dialog";dialog.setAttribute("aria-labelledby","positionMarkTitle");
    dialog.innerHTML=`<form method="dialog"><div class="position-mark-head"><div><h2 id="positionMarkTitle">标记疑似位置</h2><p>${escape(anchor.sourceName)} · ${anchor.timeSec.toFixed(3)} s · 只记位置，不判断类型</p></div><button class="small subtle" type="submit" aria-label="取消并返回视频">返回视频</button></div><p class="position-mark-help">点击冻结帧中需要复核的接缝或表观异常。位置以“视频时间 + 原像素点 + 相对里程”保存；键盘可用方向键选点。</p><canvas id="positionReviewFrame" tabindex="0" aria-label="冻结原帧，点击或使用方向键选择疑似位置"></canvas><div class="position-mark-settings"><label class="field">画面左侧环位 / °<input id="positionMarkLeft" type="number" min="-720" max="720" step="any" value="${escape(document.querySelector("#evidenceSectorStart")?.value||60)}"></label><label class="field">画面右侧环位 / °<input id="positionMarkRight" type="number" min="-720" max="720" step="any" value="${escape(document.querySelector("#evidenceSectorEnd")?.value||120)}"></label><label class="field">选点说明（选填）<input id="positionMarkNote" maxlength="160" placeholder="记录需要复核该位置的原因"></label></div><div class="position-mark-footer"><output id="positionMarkCoordinates">请先在原帧选点。</output><button type="button" class="primary" id="positionMarkSave" disabled>保存位置并同步仿真</button></div><p id="positionMarkMessage" role="status" class="position-review-message"></p></form>`;
    root.append(dialog);const modal=dialog,canvas=modal.querySelector("canvas"),save=modal.querySelector("#positionMarkSave"),image=new Image();let picked=null;
    const draw=()=>{const ct=canvas.getContext("2d");ct.drawImage(image,0,0);if(picked){const x=picked.u*canvas.width,y=picked.v*canvas.height;ct.strokeStyle="#56d9b1";ct.lineWidth=2;ct.beginPath();ct.arc(x,y,9,0,2*Math.PI);ct.moveTo(x-16,y);ct.lineTo(x+16,y);ct.moveTo(x,y-16);ct.lineTo(x,y+16);ct.stroke();}};
    const choose=(u,v)=>{picked={u:Math.max(0,Math.min(1,u)),v:Math.max(0,Math.min(1,v))};draw();save.disabled=false;modal.querySelector("#positionMarkCoordinates").textContent=`原像素 (${Math.round(picked.u*anchor.width)}, ${Math.round(picked.v*anchor.height)}) · 时间 ${anchor.timeSec.toFixed(3)} s`;};
    canvas.addEventListener("click",event=>{const r=canvas.getBoundingClientRect();choose((event.clientX-r.left)/r.width,(event.clientY-r.top)/r.height);});
    canvas.addEventListener("keydown",event=>{const dirs={ArrowLeft:[-.01,0],ArrowRight:[.01,0],ArrowUp:[0,-.01],ArrowDown:[0,.01]};if(!dirs[event.key])return;event.preventDefault();const p=picked||{u:.5,v:.5};choose(p.u+dirs[event.key][0],p.v+dirs[event.key][1]);});
    save.addEventListener("click",()=>{try{
      ensure(t);if(!picked)throw Error("请先在原帧选点。");
      const left=modal.querySelector("#positionMarkLeft"),right=modal.querySelector("#positionMarkRight");if(!left.checkValidity()||!right.checkValidity()||!left.value.trim()||!right.value.trim())throw Error("请填写 -720 至 720 度内的画面左右环位。");
      const loc=E.relativeVideo(api.state.project,api.context,anchor.timeSec,anchor.durationSec,picked.u,Number(left.value),Number(right.value)),note=modal.querySelector("#positionMarkNote").value.trim();if(note)loc.positionBasis+="；选点说明："+note;
      const r=E.add(api.state,api.context,{...loc,type:null},{...anchor,...picked,mappingRule:"relative-order-1.0"});api.state.evidenceSelectedId=r.id;api.save();disposeDialog();api.changed();api.setFollow(true);api.select(r.id,true);message("疑似位置已保存，原帧编号、出现顺序和仿真坐标已同步；类型留给第二阶段。");
    }catch(e){modal.querySelector("#positionMarkMessage").textContent=e.message;}});
    modal.addEventListener("close",()=>{if(dialog===modal){dialog=null;modal.remove();}});
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error("冻结帧未能显示，请重新标记。"));image.src=anchor.thumbnail;});
    if(dialog!==modal)return;ensure(t);canvas.width=image.width;canvas.height=image.height;draw();modal.showModal();canvas.focus();
  }
  document.addEventListener("tunnel-monitor-state",()=>queueMicrotask(()=>update()));
  window.TunnelPositionReview={mount,update};
})();