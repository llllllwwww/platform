/* 核心验证：node tests/core.test.js。仅验证演示模型实现，不表示工程精度校验。 */
'use strict';
const assert=require('node:assert/strict');
const Core=require('../assets/core.js');
let passed=0;
function test(name,fn) { fn(); passed++; console.log('PASS '+name); }
function near(a,b,tolerance=1e-7) { assert.ok(Math.abs(a-b)<tolerance,`${a} != ${b}`); }

test('统一实体、坐标映射、确定性评估版本及筛选',()=>{
  const s=Core.createState(),all=Core.getDefects(s);
  assert.equal(all.length,16); assert.equal(new Set(all.map(d=>d.id)).size,16);
  const d=all.find(d=>d.id==='D-001'); near(d.mileage,3150.2); near(d.position.x,22.2);
  near(Math.hypot(d.position.y,d.position.z),s.project.radius+d.depth,1e-6);
  assert.ok(all.every(d=>d.source.includes('演示')&&d.units.depth==='m'));
  const before=Core.assess(s);
  s.filters.type='void'; assert.equal(Core.getDefects(s).length,5);
  assert.equal(Core.getDefects(s,{filtered:false}).length,16);
  assert.equal(Core.assess(s).version,before.version,'列表过滤不应偷换全批次评估');
  s.filters.minConfidence=.99; assert.equal(Core.getDefects(s).length,0);
});
test('历史尺寸与复核状态按批次隔离',()=>{
  const s=Core.createState(),now=Core.getDefects(s)[0];
  Core.setReview(s,'D-001','rejected'); assert.equal(Core.getDefects(s)[0].review,'rejected');
  s.batch='B202607'; const old=Core.getDefects(s)[0];
  near(old.diameter,now.diameter*.8); near(old.area,now.area*.64);
  assert.equal(old.review,'confirmed'); assert.match(old.source,/非历史实测/);
  Core.setReview(s,'D-001','pending'); s.batch='B202609';
  assert.equal(Core.getDefects(s)[0].review,'rejected');
  s.batch='B202607'; assert.equal(Core.getDefects(s)[0].review,'pending');
  assert.throws(()=>Core.setReview(s,'D-XXX','pending'),/不存在/);
});
test('AHP 幂法、互反与一致性诊断',()=>{
  const s=Core.createState(),a=Core.calculateAHP(s.ahp);
  near(a.cr,0); near(a.lambdaMax,4); a.weights.forEach((w,i)=>near(w,[.4,.3,.2,.1][i]));
  const bad=s.ahp.map(r=>r.slice()); bad[0][1]=9; bad[1][0]=1/9;
  assert.ok(Core.calculateAHP(bad).cr>.1);
  bad[1][0]=1; assert.throws(()=>Core.calculateAHP(bad),/正互反/);
  assert.throws(()=>Core.calculateAHP([[1]]),/4 × 4/);
  const zero=s.ahp.map(r=>r.slice()); zero[1][2]=0; assert.throws(()=>Core.calculateAHP(zero),/有效数值/);
});
test('熵权处理常量、缺失与样本不足',()=>{
  const single=Core.entropyWeights([[1,2,3,4]]); assert.deepEqual(single.weights,[.25,.25,.25,.25]);
  const constant=Core.entropyWeights([[1,1,1,1],[1,1,1,1]]); assert.deepEqual(constant.weights,[.25,.25,.25,.25]);
  const e=Core.entropyWeights([[0,1,1,1],[1,1,1,1],[.5,1,1,1]]);
  near(e.weights[0],1); near(e.weights[1],0);
  const missing=Core.entropyWeights([[NaN,0,1,2],[.1,1,2,3],[.7,3,4,5]]);
  near(missing.weights.reduce((a,b)=>a+b),1); assert.ok(missing.warnings.some(w=>w.includes('缺失值')));
});
test('组合权重引起可测 SHI 变化，区段长度正确且严重局部不会被稀释',()=>{
  const s=Core.createState(); s.alpha=0; const entropy=Core.assess(s); s.alpha=1; const ahp=Core.assess(s);
  assert.ok(Math.abs(entropy.shi-ahp.shi)>1,'alpha 必须对 SHI 有实际影响');
  near(ahp.weights.reduce((a,b)=>a+b),1); near(ahp.segments.reduce((a,d)=>a+d.end-d.start,0),48);
  assert.equal(ahp.risk,'I'); assert.ok(ahp.items.find(d=>d.id==='D-001').severe);
  s.defects=s.defects.filter(d=>d.id==='D-001'); s.severeDiameter=.4;
  const one=Core.assess(s); assert.ok(one.shi>80); assert.equal(one.risk,'I','总分较高仍必须保留局部最高风险');
  assert.equal(one.segments.filter(d=>d.count===0).length,7);
  Core.setReview(s,'D-001','rejected'); const rejected=Core.assess(s); near(rejected.shi,100); assert.equal(rejected.risk,'IV');
  s.thresholds=[40,40,80]; assert.throws(()=>Core.assess(s),/严格升序/);
});
test('CSV 合法导入、元数据缺失和标定一致性',()=>{
  const r=Core.parseCSV('\uFEFF1,-2,3e-2\r\n4,5,6\r\n');
  assert.equal(r.source,'import'); assert.equal(r.rows,2); assert.equal(r.cols,3); near(r.matrix[0][2],.03);
  assert.equal(r.axes.x,'道号'); assert.equal(r.axes.y,'采样点'); assert.equal(r.warnings.length,3);
  const m=Core.parseCSV('1,2\n3,4',{traceSpacingM:.02,sampleIntervalNs:.1,timeWindowNs:.1,epsilon:6});
  assert.equal(m.warnings.length,0); assert.equal(m.axes.x,'距离 (m)');
  assert.throws(()=>Core.parseCSV('1,2\n3,4',{sampleIntervalNs:.1,timeWindowNs:5}),/不一致/);
  assert.throws(()=>Core.parseCSV('1,2\n3,4','{oops'),/JSON/);
});
test('CSV 拒绝空值、非矩形、非有限值、超限与不支持文本',()=>{
  ['','1,2','1\n2','1,,2\n3,4,5','1,2\n3','1,2\n3,Infinity','a,b\n1,2','1,2\n3,NaN','1,2\n3,1e309','1,2\n3,1000000000001'].forEach(x=>assert.throws(()=>Core.parseCSV(x)));
  assert.throws(()=>Core.parseCSV(('1,2\n').repeat(513)),/超过/);
  assert.throws(()=>Core.parseCSV('1'.repeat(Core.LIMITS.bytes+1)),/5 MB/);
  assert.throws(()=>Core.parseCSV('1,2\n3,4',{traceSpacingM:-1}),/有效数值/);
});
test('示意信号可复现、参数真正改变结果；减背景/增益执行真实数学',()=>{
  const a=Core.generateRadar({seed:42,rows:8,cols:10}),b=Core.generateRadar({seed:42,rows:8,cols:10});
  assert.deepEqual(a,b); assert.notDeepEqual(a,Core.generateRadar({seed:43,rows:8,cols:10}));
  assert.notDeepEqual(a,Core.generateRadar({seed:42,rows:8,cols:10,diameter:.5}));
  assert.notDeepEqual(a,Core.generateRadar({seed:42,rows:8,cols:10,epsilon:12}));
  const input=[[1,3],[2,6]],out=Core.preprocess(input,{gain:1,background:true});
  assert.deepEqual(out,[[-1,1],[-4,4]]); assert.deepEqual(input,[[1,3],[2,6]]);
  assert.deepEqual(Core.preprocess(input,{gain:0,background:false}),input);
});
test('任务边界、覆盖率、时间及同尺寸停障检查',()=>{
  const s=Core.createState(); assert.equal(Core.validateTask(s.tasks[0],s.project).name,'首轮衬砌普查');
  assert.throws(()=>Core.validateTask({...s.tasks[0],end:3200},s.project),/有效数值/);
  assert.throws(()=>Core.validateTask({...s.tasks[0],speed:0},s.project),/有效数值/);
  const clear=Core.simulateVehicle({}); near(clear.duration,80); near(clear.coverage,100); assert.equal(clear.collision,false);
  const sparse=Core.simulateVehicle({spacing:.24}); near(sparse.coverage,50); assert.equal(sparse.missed.length,1);
  const blocked=Core.simulateVehicle({obstacles:[{x:24,z:0,width:1,length:1}]});
  near(blocked.stopAt,22); near(blocked.duration,22/.6,1e-6); near(blocked.coverage,22/48*100,1e-6);
  assert.equal(blocked.collision,false); assert.equal(blocked.stopped,true); assert.equal(blocked.vehicle.length,2.4);
  const lateral=Core.simulateVehicle({obstacles:[{x:24,z:3,width:1,length:1}]}); near(lateral.coverage,100);
  const colliding=Core.simulateVehicle({obstacles:[{x:.5,z:0,width:1,length:1}]}); assert.equal(colliding.collision,true); near(colliding.duration,0);
});
test('圆环解析计算满足量纲与参数单调性',()=>{
  const a=Core.simulateStructure({thickness:.3,E:30,load:100,radius:3}); near(a.stress,1); near(a.displacement,.1);
  const b=Core.simulateStructure({thickness:.6,E:30,load:100,radius:3}); near(b.displacement,a.displacement/2); near(b.stress,a.stress/2);
  const stiffer=Core.simulateStructure({thickness:.3,E:60,load:100,radius:3}); near(stiffer.displacement,a.displacement/2); near(stiffer.stress,a.stress);
  assert.match(a.assumptions,/不计算.*承载力/); assert.throws(()=>Core.simulateStructure({E:0}),/有效数值/);
});
test('方案预算、权重和推荐结果确实联动',()=>{
  const none=Core.comparePlans({budget:0}); assert.ok(none.every(p=>!p.recommended&&!p.affordable));
  const low=Core.comparePlans({budget:10}); assert.equal(low[0].id,'P1'); assert.equal(low[0].recommended,true);
  const risk=Core.comparePlans({budget:100,weights:[0,0,1]}),cost=Core.comparePlans({budget:100,weights:[1,0,0]});
  assert.equal(risk[0].id,'P3'); assert.equal(cost[0].id,'P1');
  assert.throws(()=>Core.comparePlans({weights:[0,0,0]}),/至少一个/);
});
test('导出携带当前筛选/批次/版本/来源；序列化无循环且不泄漏矩阵',()=>{
  const s=Core.createState(); s.filters.type='void'; s.radars=[{id:'R1',batchId:s.batch,matrix:[[1,2],[3,4]],source:'import'}];
  const snapshot=Core.exportSnapshot(s); assert.equal(snapshot.defects.length,5); assert.equal(snapshot.assessment.sampleCount,16);
  assert.equal(snapshot.filteredAssessment.sampleCount,5); assert.equal(snapshot.assessment.version,Core.assess(s).version);
  assert.equal(snapshot.filters.type,'void'); assert.equal(snapshot.radarData[0].matrix,undefined); assert.ok(snapshot.sources.length);
  assert.equal(JSON.parse(JSON.stringify(snapshot)).batch,'B202609');
});
test('两批次任务、预警、日志、雷达与治理任务严格隔离导出',()=>{
  const s=Core.createState(); assert.equal(s.tasks[0].batch,'B202609');
  const makeRecords=()=>[{id:'new',batch:'B202609'},{id:'old',batch:'B202607'},{id:'missing'},{id:'conflict',batch:'B202609',batchId:'B202607'}];
  s.tasks=makeRecords(); s.alerts=makeRecords(); s.logs=makeRecords(); s.maintenance=makeRecords();
  s.radars=[{id:'new-radar',batchId:'B202609',matrix:[[1,2],[3,4]]},{id:'old-radar',batchId:'B202607',matrix:[[4,3],[2,1]]},{id:'unknown-radar'}];
  let snap=Core.exportSnapshot(s);
  ['tasks','alerts','logs','maintenance'].forEach(k=>assert.deepEqual(snap[k].map(x=>x.id),['new']));
  assert.deepEqual(snap.radarData.map(x=>x.id),['new-radar']); assert.equal(snap.radarData[0].matrix,undefined);
  assert.equal(snap.exportScope.excludedCounts.tasks,3); assert.equal(snap.exportScope.excludedCounts.radarData,2);
  assert.ok(s.radars[0].matrix,'导出不应删除源矩阵');
  s.batch='B202607'; snap=Core.exportSnapshot(s);
  ['tasks','alerts','logs','maintenance'].forEach(k=>assert.deepEqual(snap[k].map(x=>x.id),['old']));
  assert.deepEqual(snap.radarData.map(x=>x.id),['old-radar']);
});
test('仿真导出只含当前批次，未绑定外部分析明确标注全局参考',()=>{
  const s=Core.createState();
  s.simulation={sample:{batch:'B202609',result:{rows:96}},vehicle:{batch:'B202607',result:{coverage:100}},
    plans:{result:{score:20}},external:{solver:'external-demo',results:{stress:1},source:'外部结果，未经本平台验证'}};
  let snap=Core.exportSnapshot(s);
  assert.deepEqual(Object.keys(snap.simulation),['sample','external']);
  assert.match(snap.simulation.external.exportScope,/项目全局.*未绑定检测批次/);
  assert.deepEqual(snap.exportScope.globalSimulationKeys,['external']);
  assert.deepEqual(snap.exportScope.excludedSimulationKeys,['vehicle','plans']);
  assert.equal(s.simulation.external.exportScope,undefined,'导出范围标注不应污染原记录');
  s.batch='B202607'; snap=Core.exportSnapshot(s);
  assert.deepEqual(Object.keys(snap.simulation),['vehicle','external']);
  s.simulation.external.batch='B202609'; snap=Core.exportSnapshot(s);
  assert.deepEqual(Object.keys(snap.simulation),['vehicle'],'明确属于其它批次的外部结果同样不得混入');
});
console.log(`\n${passed} tests passed. 演示核心逻辑与边界检查完成。`);
