/* 现场证据对应核心：采集疑似位置，不猜测病害类型；保留旧记录已有分类，工程位置须有依据。 */
(function(root,factory){"use strict";const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.TunnelEvidenceCore=api;})(typeof globalThis!=="undefined"?globalThis:this,function(){
 "use strict";
 const TYPES={crack:"表面裂缝",seepage:"表面渗水",spalling:"剥落 / 破损",corrosion:"锈蚀 / 腐蚀",void:"空气空洞",water:"充水 / 富水异常",debond:"管片脱空",loose:"不密实",rebar:"钢筋异常 / 外露"};
 const copy=x=>JSON.parse(JSON.stringify(x));
 function number(value,name,min,max){if(typeof value!=="number"||!Number.isFinite(value)||value<min||value>max)throw Error(name+"超出有效范围");return value;}
 function text(value,name,max=256){if(typeof value!=="string"||!value.trim()||value.length>max)throw Error(name+"须为有效文本");return value;}
 function init(state){state.evidenceRecords=state.evidenceRecords||[];state.evidenceModes=state.evidenceModes||{};state.evidenceSourceFilters=state.evidenceSourceFilters||{};return state;}
 function key(c){return c.batchId+"/"+c.taskId;}
 function records(state,c){init(state);return state.evidenceRecords.filter(x=>x.batchId===c.batchId&&x.taskId===c.taskId);}
 function visible(state,c){const list=records(state,c),source=state.evidenceSourceFilters[key(c)]||"all";return source==="all"?list:list.filter(r=>r.anchors.some(a=>a.sourceId===source));}
 function setSource(state,c,id){init(state);state.evidenceSourceFilters[key(c)]=id;}
 function mode(state,c){init(state);return state.evidenceModes[key(c)]||"demo";}
 function setMode(state,c,value){if(!["demo","evidence"].includes(value))throw Error("场景来源无效");init(state);state.evidenceModes[key(c)]=value;}
 function location(p,c,input){
  const mileage=number(input.mileage,"里程 / m",Math.max(p.start,c.start),Math.min(p.start+p.length,c.end));
  const angle=number(input.angle,"环向角 / °",0,359.999999),depth=number(input.depth,"径向埋深 / m",0,10);
  const basis=text(input.positionBasis,"定位依据",500),rad=angle*Math.PI/180,r=p.radius+depth;
  return {mileage,angle,depth,positionBasis:basis,ring:Math.floor((mileage-p.start)/p.ringLength)+p.ringStart,position:{x:mileage-p.start,y:r*Math.cos(rad),z:r*Math.sin(rad)}};
 }
 function anchor(value,c){
  const a=copy(value);if(!a||!["video","radar"].includes(a.kind))throw Error("证据类型无效");
  text(a.sourceId,"源编号");text(a.sourceName,"来源名称");
  if(a.batchId!==c.batchId||a.taskId!==c.taskId)throw Error("证据不属于当前批次 / 任务");
  if(a.kind==="video"){
   number(a.width,"视频宽度",1,16384);number(a.height,"视频高度",1,16384);
   number(a.u,"画面横坐标",0,1);number(a.v,"画面纵坐标",0,1);
   if(a.timeSec!==null)number(a.timeSec,"录像位置 / s",0,864000);
   if(typeof a.thumbnail!=="string"||!/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(a.thumbnail)||a.thumbnail.length>400000)throw Error("需要有效视频帧缩略图，跨域源请先解决截图权限");
   text(a.frameAt,"取帧时间");
  }else{
   number(a.rows,"采样点数",2,512);number(a.cols,"道数",2,2048);
   number(a.trace,"道号",0,a.cols-1);number(a.sample,"采样点",0,a.rows-1);
   if(!Number.isInteger(a.trace)||!Number.isInteger(a.sample))throw Error("雷达道号与采样点须为整数");
   text(a.archiveId,"雷达快照编号");
  }
  // 只接受定义好的字段，禁止把连接地址、令牌或任意输入对象带入导出。
  const fields=a.kind==="video"?["kind","sourceId","sourceName","batchId","taskId","width","height","u","v","timeSec","frameAt","thumbnail","identityBasis","nature","durationSec","mappingRule"]:["kind","sourceId","sourceName","batchId","taskId","rows","cols","trace","sample","archiveId","amplitude","direction","mappingRule"];
  return Object.fromEntries(fields.filter(f=>a[f]!==undefined).map(f=>[f,a[f]]));
 }
 function validate(state,c,input){
  const type=input.type===undefined||input.type===null||input.type===""?null:input.type;
  if(type!==null&&(typeof type!=="string"||!Object.hasOwn(TYPES,type)))throw Error("不支持的病害类型；采集疑似位置可留空");
  if(!/^E-[A-Za-z0-9-]{3,76}$/.test(input.id)||!Array.isArray(input.anchors))throw Error("需要 E- 开头的唯一编号与证据数组");
  const loc=location(state.project,c,input),anchors=input.anchors.map(a=>anchor(a,c));
  if(!anchors.length||anchors.length>20)throw Error("每个病害需有 1～20 条证据");
  return Object.assign({id:text(input.id,"病害编号",80),batchId:c.batchId,taskId:c.taskId,type,typeName:type===null?"疑似点（待识别）":TYPES[type],classificationStatus:type===null?"pending":"provided",labelBasis:type===null?"人工记录疑似位置（未判断类型）":"保留或人工复核的类型记录（非自动识别）",review:"待专业复核",createdAt:input.createdAt||new Date().toISOString(),updatedAt:new Date().toISOString(),anchors},loc);
 }
 function ordered(values){
  const groups=new Map();
  for(const r of values)for(const a of r.anchors){const order=a.kind==="video"?a.timeSec:a.trace;if(order===null)continue;const k=r.batchId+"/"+r.taskId+"/"+a.kind+"/"+a.sourceId;const list=groups.get(k)||[];list.push({order,mileage:r.mileage,direction:a.kind==="radar"&&a.direction===-1?-1:1});groups.set(k,list);}
  for(const list of groups.values()){
   list.sort((a,b)=>a.order-b.order);
   for(let i=1;i<list.length;i++)if(list[i].direction!==list[i-1].direction||(list[i].order>list[i-1].order&&(list[i].mileage-list[i-1].mileage)*list[i].direction < -1e-7))throw Error("病害先后顺序与源视频时间 / 雷达道号不一致，请修正定位或明确测线方向");
  }
 }
 function remapRange(state,oldContext,newContext){
  const changes=records(state,oldContext).map(r=>{
   const input=copy(r);
   if(r.positionBasis.startsWith("相对位置映射"))input.mileage=newContext.start+(r.mileage-oldContext.start)/(oldContext.end-oldContext.start)*(newContext.end-newContext.start);
   return validate(state,newContext,input);
  });
  ordered([...state.evidenceRecords.filter(r=>r.batchId!==oldContext.batchId||r.taskId!==oldContext.taskId),...changes]);
  for(const next of changes){const old=state.evidenceRecords.find(r=>r.id===next.id);state.evidenceRecords[state.evidenceRecords.indexOf(old)]=next;}
 }
 function relativeVideo(p,c,time,duration,u,startAngle,endAngle){
  number(duration,"录像时长",.001,864000);number(time,"录像位置",0,duration);number(u,"画面横坐标",0,1);number(startAngle,"环向起角",-720,720);number(endAngle,"环向终角",-720,720);
  return location(p,c,{mileage:c.start+(c.end-c.start)*time/duration,angle:((startAngle+(endAngle-startAngle)*u)%360+360)%360,depth:0,positionBasis:"相对位置映射：录像时间 / 时长 → 任务里程；画面横向 → 指定环向范围；非实测定位"});
 }
 function relativeRadar(p,c,data,trace,sample,angle){
  number(trace,"道号",0,data.cols-1);number(sample,"采样点",0,data.rows-1);
  return location(p,c,{mileage:c.start+(c.end-c.start)*trace/(data.cols-1),angle,depth:p.thickness*sample/(data.rows-1),positionBasis:"相对位置映射：道号 → 任务里程；采样序号 → 示意衬砌厚度；非实测埋深"});
 }
 function add(state,c,input,a,targetId){
  init(state);const existing=targetId?records(state,c).find(r=>r.id===targetId):null;
  if(targetId&&!existing)throw Error("待关联病害不存在或属于其他任务");
  const value=validate(state,c,Object.assign({},input,{type:existing&&(input.type===null||input.type===undefined||input.type==="")?existing.type:input.type,id:existing?.id||"E-"+Date.now().toString(36)+"-"+Math.random().toString(36).slice(2,8),anchors:existing?[...existing.anchors,a]:[a],createdAt:existing?.createdAt}));
  if(existing&&(existing.type!==value.type||["mileage","angle","depth"].some(k=>Math.abs(existing[k]-value[k])>1e-7)))throw Error("关联同一病害时，类型及位置必须一致；请先统一修改病害位置，再关联证据");
  ordered([...state.evidenceRecords.filter(r=>r!==existing),value]);
  if(state.evidenceRecords.length>=200&&!existing)throw Error("现场标注达到 200 条上限，请先导出或删除旧记录");
  if(existing)state.evidenceRecords[state.evidenceRecords.indexOf(existing)]=value;else state.evidenceRecords.push(value);
  setMode(state,c,"evidence");setSource(state,c,a.sourceId);return copy(value);
 }
 function edit(state,c,id,input){
  const old=records(state,c).find(r=>r.id===id);if(!old)throw Error("当前病害不存在");
  const value=validate(state,c,Object.assign({},old,input,{id,anchors:old.anchors}));
  if(["mileage","angle","depth"].some(k=>Math.abs(value[k]-old[k])>1e-7)){
   // 手工修订位置后不再按旧时间映射联动车辆，也不再自动按任务范围重映射。
   for(const a of value.anchors)a.mappingRule="manual-location-1.0";
   if(value.positionBasis===old.positionBasis)value.positionBasis="人工工程位置修订（原映射已失效）："+old.positionBasis.slice(0,430);
  }
  ordered([...state.evidenceRecords.filter(r=>r!==old),value]);state.evidenceRecords[state.evidenceRecords.indexOf(old)]=value;return copy(value);
 }
 function remove(state,c,id){const row=records(state,c).find(r=>r.id===id);if(!row)throw Error("当前病害不存在");state.evidenceRecords.splice(state.evidenceRecords.indexOf(row),1);}
 function radarPosition(p,c,data,trace,sample,cal){
  number(trace,"道号",0,data.cols-1);number(sample,"采样点",0,data.rows-1);
  const m=data.metadata||{};number(m.traceSpacingM,"标定道间距",1e-9,1000);number(m.sampleIntervalNs,"标定采样间隔",1e-9,1000);number(m.epsilon,"标定介电常数",1,100);
  number(cal.startMileage,"测线起点",p.start,p.start+p.length);number(cal.angle,"测线环位",0,359.999999);number(cal.zeroSample,"表面零点采样",0,data.rows-1);
  if(![1,-1].includes(cal.direction))throw Error("测线方向须为 +1 或 -1");
  if(sample<cal.zeroSample)throw Error("采样点位于表面零点之前，不能推算埋深");
  const result={mileage:cal.startMileage+cal.direction*trace*m.traceSpacingM,angle:cal.angle,depth:(sample-cal.zeroSample)*m.sampleIntervalNs*.299792458/(2*Math.sqrt(m.epsilon)),positionBasis:"测线标定换算（须核验起点、方向、环位、零点与介电常数）"};
  return Object.assign(result,location(p,c,result));
 }
 function scene(state,c,options={}){return visible(state,c).slice().sort((a,b)=>a.mileage-b.mileage).map((r,i)=>({id:r.id,type:options.positionOnly||!r.type?"suspected":r.type,typeName:options.positionOnly||!r.type?"疑似点":r.typeName,classificationStatus:r.type?"provided":"pending",sequence:i+1,mileage:r.mileage,ring:r.ring,angle:r.angle,depth:r.depth,position:copy(r.position),risk:"unrated",score:null,diameter:.12,length:.35,area:0,confidence:null,review:"pending",markerOnly:true,source:r.labelBasis}));}
 function exportData(state,c){return {schemaVersion:2,kind:"tunnel-evidence-correspondence",project:copy(state.project),context:copy(c),records:copy(records(state,c)),boundary:"采集阶段仅记录疑似位置与出现顺序；新视频记录 type=null，待第二阶段识别。保留已有类型记录。像素/道号不自动等于工程坐标；三维尺寸仅为定位标记。"};}
 function importData(state,c,value){
  if(!value||![1,2].includes(value.schemaVersion)||value.kind!=="tunnel-evidence-correspondence"||!Array.isArray(value.records))throw Error("不是有效的现场证据对应 JSON");
  if(value.context?.batchId!==c.batchId||value.context?.taskId!==c.taskId)throw Error("导入批次 / 任务与当前页面不一致");
  if(["start","length","radius","ringStart","ringLength"].some(k=>value.project?.[k]!==state.project[k]))throw Error("导入项目坐标基准不一致");
  init(state);const staged=value.records.map(input=>{if(input.batchId!==c.batchId||input.taskId!==c.taskId)throw Error("记录批次 / 任务不一致");return validate(state,c,input);});
  if(staged.length+state.evidenceRecords.length>200)throw Error("导入后超过 200 条上限");
  const ids=new Set(state.evidenceRecords.map(r=>r.id));for(const r of staged){if(ids.has(r.id))throw Error("编号已存在，请避免重复导入");ids.add(r.id);}
  ordered([...state.evidenceRecords,...staged]);state.evidenceRecords.push(...staged);setSource(state,c,"all");setMode(state,c,"evidence");return staged.length;
 }
 return {TYPES,init,key,records,visible,setSource,mode,setMode,location,anchor,add,edit,remove,radarPosition,remapRange,relativeVideo,relativeRadar,ordered,scene,exportData,importData};
});
