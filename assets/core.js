/* 隧雷智检：离线业务计算核心。
 * 本文件不依赖 DOM、网络、模型权重或外部数学库，浏览器与 Node 共用同一实现。
 * 数据/阈值/预测效果均为可复现演示配置，评分不代表工程鉴定结论。
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TunnelCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  var VERSION = 'demo-assessment-2.0';
  var RISKS = ['I', 'II', 'III', 'IV'];
  var TYPES = {
    void: { name: '空气空洞', severity: 1 }, water: { name: '充水 / 富水异常', severity: 0.9 },
    debond: { name: '管片脱空', severity: 0.8 }, loose: { name: '不密实', severity: 0.55 },
    crack: { name: '裂缝 / 渗漏', severity: 0.7 }, rebar: { name: '钢筋异常', severity: 0.45 }
  };
  var VEHICLE = Object.freeze({ length: 2.4, width: 1.4, safety: 0.3, scanWidth: 0.12 });
  var LIMITS = Object.freeze({ rows: 512, cols: 2048, bytes: 5 * 1024 * 1024, amplitude: 1e12 });
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }
  function finite(v, name, min, max) {
    if (typeof v !== 'number' || !Number.isFinite(v) || (min != null && v < min) || (max != null && v > max))
      throw new Error(name + ' 必须是有效数值' + (min != null ? '，范围 ' + min + ' 至 ' + max : ''));
    return v;
  }
  function numberOption(v, fallback, name, min, max) { return finite(v == null ? fallback : Number(v), name, min, max); }
  function sum(a) { return a.reduce(function (s, x) { return s + x; }, 0); }
  function normalize(a) { var s = sum(a); return s > 1e-12 ? a.map(function (x) { return x / s; }) : a.map(function () { return 1 / a.length; }); }
  function round(v, places) { return Number(v.toFixed(places == null ? 6 : places)); }
  function hash(value) {
    var s = JSON.stringify(value), h = 2166136261;
    for (var i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    return (h >>> 0).toString(16).padStart(8, '0');
  }

  // 沿用原平台 16 条演示实体。尺寸为历史演示台账给定值，并非当前雷达导入的识别结果。
  var SEEDS = [
    ['D-001','void',998,3,.12,.42,.55,.139,.96], ['D-002','water',1013,121,.34,.38,.72,.113,.91],
    ['D-003','void',983,60,.09,.31,1.15,.075,.94], ['D-004','water',990,300,.22,.24,.30,.045,.88],
    ['D-005','void',1006,20,.15,.22,.25,.038,.90], ['D-006','void',984,76,.44,.20,.28,.031,.87],
    ['D-007','debond',1002,35,.06,.19,.60,.028,.86], ['D-008','debond',1015,160,.11,.16,.45,.020,.84],
    ['D-009','loose',994,250,.24,1.29,2.40,1.300,.83], ['D-010','loose',987,108,.20,.90,1.10,.640,.80],
    ['D-011','loose',1010,190,.22,.70,.80,.380,.79], ['D-012','water',1006,322,.20,.13,.18,.013,.81],
    ['D-013','void',998,40,.16,.08,.10,.005,.86], ['D-014','crack',990,355,0,.02,1.80,.036,.77],
    ['D-015','rebar',1002,135,.04,.30,.60,.070,.75], ['D-016','rebar',1013,239,.05,.25,.50,.049,.72]
  ];
  function createState() {
    var defs = SEEDS.map(function (d, i) {
      return { id:d[0], type:d[1], ring:d[2], angle:d[3], depth:d[4], diameter:d[5], length:d[6], area:d[7], confidence:d[8],
        review:i < 3 ? 'confirmed' : 'pending', lineId:'L-' + String(i % 3 + 1).padStart(2,'0'), source:'内置演示台账（原平台 16 条）',
        version:'demo-labels-2.0', evidenceId:'DEMO-' + d[0], units:{ depth:'m',diameter:'m',length:'m',area:'m²' },
        measurementBasis:'演示台账给定尺寸；未关联真实标定或已接入识别模型', createdAt:'2026-09-28T00:00:00.000Z' };
    });
    return {
      schemaVersion:2, project:{ id:'P-SLZJ-001',name:'隧雷智检 · 示范隧道',start:3128,length:48,radius:2.7,thickness:.35,ringStart:980,ringLength:1.2,
        section:'圆形盾构隧道',lining:'钢筋混凝土管片（演示）', units:'m', source:'参数化隧道与演示台账' },
      batch:'B202609', batches:[{id:'B202609',name:'2026 年 9 月检测',date:'2026-09-28',source:'演示'}, {id:'B202607',name:'2026 年 7 月检测',date:'2026-07-15',source:'演示历史派生：尺寸为当前台账的 80%'}],
      selectedId:'D-001', filters:{type:'all',risk:'all',review:'all',minConfidence:0,start:3128,end:3176}, alpha:.6,
      ahp:[[1,4/3,2,4],[3/4,1,1.5,3],[.5,2/3,1,2],[.25,1/3,.5,1]], thresholds:[40,60,80], severeDiameter:.3,
      defects:defs, reviewsByBatch:{}, tasks:[{id:'T-001',batch:'B202609',name:'首轮衬砌普查',start:3128,end:3176,speed:.6,spacing:.1,status:'ready',progress:0,source:'演示任务',lineId:'L-01',scanStart:-90,scanEnd:90}],
      alerts:[], logs:[], radars:[], maintenance:[], processing:{status:'idle',algorithmVersion:'未接入 RCAN / RTM / 识别模型'}, simulation:{},
      createdAt:'2026-09-28T00:00:00.000Z', dataMode:'demo'
    };
  }

  function rawDefects(state) {
    var p = state.project, historical = state.batch !== 'B202609';
    if (!state.batches.some(function (b) { return b.id === state.batch; })) throw new Error('未知检测批次');
    return state.defects.map(function (item, i) {
      var d = clone(item), factor = historical ? .8 : 1;
      d.diameter = round(d.diameter * factor); d.length = round(d.length * factor); d.area = round(d.area * factor * factor);
      d.batchId = state.batch;
      d.source = historical ? '历史演示派生（直径/长度 ×0.8，面积 ×0.64；非历史实测）' : item.source;
      var reviews = state.reviewsByBatch && state.reviewsByBatch[state.batch];
      d.review = reviews && reviews[d.id] || (historical ? (i < 2 ? 'confirmed' : 'pending') : item.review);
      // 坐标：X 为轴线前进方向，原点为项目起点；Y 向上，Z 为环向右侧，0°拱顶、90°右墙。
      // 环号定位在管片环中心；径向深度从内表面向衬砌/壁后方向增加，单位全部为 m。
      d.mileage = round(p.start + (d.ring - (p.ringStart || 980) + .5) * (p.ringLength || 1.2));
      var radius = p.radius + d.depth, rad = d.angle * Math.PI / 180;
      d.position = { x:round(d.mileage-p.start), y:round(radius*Math.cos(rad)), z:round(radius*Math.sin(rad)) };
      return d;
    });
  }
  function setReview(state, id, review) {
    if (!['pending','confirmed','rejected'].includes(review)) throw new Error('复核状态无效');
    if (!state.defects.some(function (d) { return d.id === id; })) throw new Error('病害不存在');
    if (!state.reviewsByBatch) state.reviewsByBatch = {};
    if (!state.reviewsByBatch[state.batch]) state.reviewsByBatch[state.batch] = {};
    state.reviewsByBatch[state.batch][id] = review;
    return review;
  }

  function calculateAHP(matrix) {
    if (!Array.isArray(matrix) || matrix.length !== 4 || matrix.some(function (row) { return !Array.isArray(row) || row.length !== 4; }))
      throw new Error('AHP 判断矩阵必须为 4 × 4');
    matrix.forEach(function (row, i) { row.forEach(function (v, j) {
      finite(v,'AHP 元素',1/100,100);
      if (i === j && Math.abs(v - 1) > 1e-7) throw new Error('AHP 对角元素必须为 1');
      if (Math.abs(v * matrix[j][i] - 1) > 1e-5) throw new Error('AHP 判断矩阵必须正互反：aᵢⱼ × aⱼᵢ = 1');
    }); });
    var w = [.25,.25,.25,.25];
    // 正矩阵幂法求 Perron 主特征向量，迭代至 L∞ 差 <1e-12。
    for (var k = 0; k < 1000; k++) {
      var next = normalize(matrix.map(function (row) { return sum(row.map(function (v,j) { return v*w[j]; })); }));
      var delta = Math.max.apply(null,next.map(function (v,j) { return Math.abs(v-w[j]); }));
      w = next; if (delta < 1e-12) break;
    }
    var lambda = sum(matrix.map(function (row,i) { return sum(row.map(function (v,j) { return v*w[j]; }))/w[i]; }))/4;
    // Saaty n=4 随机一致性指标 RI=0.90。CR=(lambdaMax-n)/((n-1)*RI)。这里只作演示一致性诊断。
    return {weights:w,lambdaMax:lambda,cr:Math.max(0,(lambda-4)/3/.9),converged:k<1000};
  }
  function entropyWeights(matrix) {
    var count = matrix.length, warnings = [], n = 4;
    if (count < 2) return {weights:[.25,.25,.25,.25],warnings:['有效样本少于 2 条，熵权退化为等权。'],sampleCount:count};
    var columns = [0,1,2,3].map(function (j) {
      var known = matrix.map(function (r) { return r[j]; }).filter(Number.isFinite);
      var mean = known.length ? sum(known)/known.length : 0;
      if (known.length !== count) warnings.push('指标 ' + (j+1) + ' 缺失值以该列有效样本均值填补；无有效值时填 0。');
      return matrix.map(function (r) { return Number.isFinite(r[j]) ? r[j] : mean; });
    });
    var differences = columns.map(function (col,j) {
      var low = Math.min.apply(null,col), high = Math.max.apply(null,col);
      if (high-low < 1e-12) { warnings.push('指标 ' + (j+1) + ' 为常量，熵权区分度为 0。'); return 0; }
      // 熵权样本矩阵为当前批次未驳回病害的四项严重度。逐列极差标准化后 pᵢⱼ=zᵢⱼ/Σzᵢⱼ。
      // eⱼ=-Σ(p ln p)/ln(n)，约定 0 ln 0=0；差异系数 1-e。熵权仅体现离散程度，不代表因果重要性。
      var z = col.map(function (v) { return (v-low)/(high-low); }), total = sum(z);
      var e = -sum(z.map(function (v) { var p = v/total; return p ? p*Math.log(p) : 0; }))/Math.log(count);
      return Math.max(0,1-e);
    });
    if (sum(differences) < 1e-12) warnings.push('全部指标缺少区分度，熵权退化为等权。');
    return {weights:normalize(differences),warnings:warnings,sampleCount:count};
  }
  function indicators(d) {
    // 四项均为负向指标：数值越大越严重。所有边界/类别严重度为演示假设，可替换成经审定指标体系。
    // 类型：固定类别映射；尺寸：0.6*min(d/1.3,1)+0.4*min(L/2.4,1)；
    // 浅埋：1-min(h/0.6,1)；位置：0.35+0.65*max(cos(theta),0)，突出拱顶。
    return [TYPES[d.type] ? TYPES[d.type].severity : .5, .6*clamp(d.diameter/1.3,0,1)+.4*clamp(d.length/2.4,0,1),
      1-clamp(d.depth/.6,0,1), .35+.65*Math.max(Math.cos(d.angle*Math.PI/180),0)];
  }
  function riskFromScore(score, thresholds) {
    return score < thresholds[0] ? 'I' : score < thresholds[1] ? 'II' : score < thresholds[2] ? 'III' : 'IV';
  }
  function worstRisk(risks) { return RISKS[Math.min.apply(null,risks.map(function (r) { return RISKS.indexOf(r); }).concat([3]))]; }
  function checkConfig(state) {
    finite(state.alpha,'组合系数 α',0,1); finite(state.severeDiameter,'严重病害直径阈值',.001,100);
    if (!Array.isArray(state.thresholds) || state.thresholds.length !== 3) throw new Error('风险阈值应为三个严格升序数字');
    state.thresholds.forEach(function (v,i) { finite(v,'风险阈值',0,100); if (i && v<=state.thresholds[i-1]) throw new Error('风险阈值必须严格升序'); });
  }
  function assessInternal(state, defects, scope) {
    checkConfig(state);
    var a = calculateAHP(state.ahp), active = defects.filter(function (d) { return d.review !== 'rejected'; });
    var e = entropyWeights(active.map(indicators)), warnings = e.warnings.slice();
    if (a.cr > .1) warnings.push('AHP 一致性 CR > 0.10；当前权重仅供诊断，建议修正矩阵后再比较。');
    if (!a.converged) warnings.push('AHP 幂法未收敛，请修正判断矩阵。');
    warnings.push('台账、指标阈值及严重规则为演示假设；置信度不参与风险评分，不能等同诊断可靠性。');
    warnings.push('无病害记录的区段暂按 SHI=100 参与长度加权；这是演示无异常扣分假设，不表示已确认健康。');
    var start = scope ? scope.start : state.project.start, end = scope ? scope.end : state.project.start+state.project.length;
    finite(start,'评估起点'); finite(end,'评估终点'); if (end<=start) throw new Error('评估终点必须大于起点');
    function withAlpha(alpha) {
      var weights = a.weights.map(function (v,j) { return alpha*v+(1-alpha)*e.weights[j]; });
      var items = defects.map(function (d) {
        var ind = indicators(d), score = clamp(100*(1-sum(ind.map(function (v,j) { return v*weights[j]; }))),0,100);
        // 严重直径规则为可配置演示触发器，应用全部候选类型；不得当作各病害真实工程分级规范。
        var severe = d.review !== 'rejected' && d.diameter >= state.severeDiameter;
        return {id:d.id,score:round(score),risk:d.review==='rejected'?'IV':(severe?'I':riskFromScore(score,state.thresholds)),
          indicators:ind,severe:severe,included:d.review!=='rejected',review:d.review,mileage:d.mileage};
      });
      var segments = [];
      // 固定 6 m 区段，以项目起点为网格原点；筛选范围边缘按实际长度截断。
      var origin = state.project.start;
      for (var x = origin + Math.floor((start-origin)/6)*6; x<end-1e-9; x+=6) {
        var lo=Math.max(x,start), hi=Math.min(x+6,end);
        if (hi<=lo) continue;
        var members = items.filter(function (d) { return d.included && d.mileage>=lo && (d.mileage<hi || hi===end && d.mileage===hi); });
        var shi = members.length ? Math.min.apply(null,members.map(function (d) { return d.score; })) : 100;
        segments.push({start:lo,end:hi,shi:round(shi),risk:worstRisk(members.map(function (d) { return d.risk; }).concat([riskFromScore(shi,state.thresholds)])),count:members.length,
          assumption:members.length?'区段采用有效病害最低分':'无异常扣分（演示假设）'});
      }
      // 全隧道 SHI=Σ(区段最低分×区段长度)/总长度。全局风险同时保留局部最高风险，避免平均值掩盖严重病害。
      var shi = sum(segments.map(function (s) { return s.shi*(s.end-s.start); }))/(end-start);
      return {weights:weights,items:items,segments:segments,shi:round(shi),risk:worstRisk(items.filter(function (d) { return d.included; }).map(function (d) { return d.risk; }).concat([riskFromScore(shi,state.thresholds)]))};
    }
    var result = withAlpha(state.alpha);
    result.ahpWeights = a.weights; result.entropyWeights = e.weights; result.cr = a.cr; result.lambdaMax=a.lambdaMax;
    result.sensitivity = [0,.25,.5,.75,1].map(function (alpha) { var r=withAlpha(alpha); return {alpha:alpha,shi:r.shi,risk:r.risk}; });
    result.warnings=warnings; result.sampleCount=e.sampleCount; result.batchId=state.batch;
    result.scope={start:start,end:end,description:scope?'当前筛选病害及里程范围（过滤掉的病害不参与，非完整区域鉴定）':'当前批次全区段；不受列表筛选影响'};
    result.version=VERSION+'-'+hash([state.batch,state.alpha,state.ahp,state.thresholds,state.severeDiameter,defects,start,end]);
    result.formula='w=α·w_AHP+(1−α)·w_熵权；SHI_i=100·(1−Σw_j·严重度_ij)；区段取最低分，全区段按长度加权；全局风险保留局部最高等级。';
    return result;
  }
  function assess(state) { return assessInternal(state,rawDefects(state)); }
  function getDefects(state, options) {
    var raw=rawDefects(state), result=assessInternal(state,raw), map={};
    result.items.forEach(function (i) { map[i.id]=i; });
    var list=raw.map(function (d) { return Object.assign(d,map[d.id]); });
    if (options && options.filtered===false) return list;
    var f=state.filters||{};
    return list.filter(function (d) {
      return (!f.type||f.type==='all'||d.type===f.type) && (!f.risk||f.risk==='all'||d.risk===f.risk)
        && (!f.review||f.review==='all'||d.review===f.review) && d.confidence>=(f.minConfidence||0)
        && d.mileage>=(f.start==null?-Infinity:f.start) && d.mileage<=(f.end==null?Infinity:f.end);
    });
  }

  function validateMatrix(matrix) {
    if (!Array.isArray(matrix)||matrix.length<2||matrix.length>LIMITS.rows) throw new Error('矩阵采样点数须为 2 至 '+LIMITS.rows);
    var cols=Array.isArray(matrix[0])?matrix[0].length:0;
    if (cols<2||cols>LIMITS.cols) throw new Error('矩阵道数须为 2 至 '+LIMITS.cols);
    matrix.forEach(function (row,i) {
      if (!Array.isArray(row)||row.length!==cols) throw new Error('第 '+(i+1)+' 行列数不一致，必须为矩形矩阵');
      row.forEach(function (v,j) { finite(v,'第 '+(i+1)+' 行第 '+(j+1)+' 列振幅',-LIMITS.amplitude,LIMITS.amplitude); });
    });
    return {rows:matrix.length,cols:cols};
  }
  function parseCSV(text, metadata) {
    if (typeof text!=='string') throw new Error('CSV 内容必须为文本');
    var byteLength=typeof TextEncoder!=='undefined'?new TextEncoder().encode(text).length:unescape(encodeURIComponent(text)).length;
    if (byteLength>LIMITS.bytes) throw new Error('CSV 文件超过 5 MB 上限');
    if (metadata==null) metadata={};
    if (typeof metadata==='string') { try { metadata=JSON.parse(metadata); } catch (_) { throw new Error('JSON 元数据解析失败'); } }
    if (!metadata||typeof metadata!=='object'||Array.isArray(metadata)) throw new Error('元数据应为 JSON 对象');
    var content=text.replace(/^\uFEFF/,'').trim();
    if (!content) throw new Error('CSV 文件为空');
    var rows=content.split(/\r?\n/);
    if (rows.length>LIMITS.rows) throw new Error('CSV 采样点数超过 '+LIMITS.rows);
    var matrix=rows.map(function (row,i) {
      var cells=row.split(',');
      if (cells.length>LIMITS.cols) throw new Error('CSV 道数超过 '+LIMITS.cols);
      return cells.map(function (cell,j) {
        var v=cell.trim();
        if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(v)) throw new Error('第 '+(i+1)+' 行第 '+(j+1)+' 列不是有效数值（不允许表头、空值或文本）');
        return finite(Number(v),'CSV 振幅',-LIMITS.amplitude,LIMITS.amplitude);
      });
    });
    var size=validateMatrix(matrix), m=clone(metadata), warnings=[];
    ['traceSpacingM','sampleIntervalNs','timeWindowNs','epsilon'].forEach(function (k) { if (m[k]!=null) finite(m[k],'元数据 '+k,1e-12,1e12); });
    if (!m.traceSpacingM) warnings.push('缺少道间距：横轴使用道号，不推算实际里程。');
    if (!m.sampleIntervalNs&&!m.timeWindowNs) warnings.push('缺少时间标定：纵轴使用采样点，不推算时间或埋深。');
    if (!m.epsilon) warnings.push('缺少介电参数 / 波速：不推算物理埋深。');
    if (m.sampleIntervalNs&&m.timeWindowNs&&Math.abs(m.sampleIntervalNs*(size.rows-1)-m.timeWindowNs)>Math.max(.001,m.timeWindowNs*.01))
      throw new Error('时间窗与采样间隔不一致：应满足 timeWindowNs=(采样点数−1)×sampleIntervalNs，容差 1%');
    return {matrix:matrix,rows:size.rows,cols:size.cols,metadata:m,source:'import',warnings:warnings,
      axes:{x:m.traceSpacingM?'距离 (m)':'道号',y:m.sampleIntervalNs||m.timeWindowNs?'双程旅行时间 (ns)':'采样点'},
      units:{amplitude:m.amplitudeUnit||'设备振幅（未标定）'},version:'csv-adapter-2.0'};
  }
  function generateRadar(params) {
    params=params||{};
    var rows=numberOption(params.rows,96,'采样点数',2,LIMITS.rows),cols=numberOption(params.cols,160,'道数',2,LIMITS.cols);
    if (!Number.isInteger(rows)||!Number.isInteger(cols)) throw new Error('采样点数与道数须为整数');
    var seed=numberOption(params.seed,20260928,'随机种子',0,4294967295)>>>0;
    var noise=numberOption(params.noise,.15,'噪声',0,2),depth=numberOption(params.depth,.2,'示意埋深参数',0,5);
    var diameter=numberOption(params.diameter,.3,'示意尺寸参数',.001,5),epsilon=numberOption(params.epsilon,6,'示意介电参数',1,100);
    var thickness=numberOption(params.thickness,.35,'示意衬砌厚度参数',.01,5),rebarSpacing=numberOption(params.rebarSpacing,.2,'示意钢筋间距参数',.02,2);
    var voidPosition=numberOption(params.voidPosition,.55,'空洞横向位置比例',0,1),matrix=[];
    function random() { seed=(Math.imul(seed,1664525)+1013904223)>>>0; return seed/4294967296; }
    // 以下为可复现的解析图形信号：高斯调制正弦 + 示意抛物线反射 + 均匀噪声。
    // epsilon/depth/thickness 只调节示意纹理位置，不求解 Maxwell 方程，不是电磁仿真/RTM，不据此反演真实尺寸。
    var center=clamp((.18+depth*.7)*Math.sqrt(epsilon/6),.1,.82)*rows,spread=cols*(.04+diameter*.08);
    for (var r=0;r<rows;r++) {
      var row=[];
      for (var c=0;c<cols;c++) {
        var t=r-center-Math.pow((c-cols*voidPosition)/spread,2)*1.7;
        var reflection=Math.cos(t*1.15)*Math.exp(-t*t/14)*(0.4+diameter);
        var direct=Math.sin(r*.8)*Math.exp(-Math.pow((r-rows*.09)/4,2))*.75;
        var lining=Math.sin((r-rows*clamp(thickness,.05,.8))*.9)*Math.exp(-Math.pow((r-rows*clamp(thickness,.05,.8))/3,2))*.25;
        var bars=.23*Math.sin(r*1.3)*Math.exp(-Math.pow((r-rows*.24-Math.abs(Math.sin(c/(cols*rebarSpacing*.12)))*6)/5,2));
        row.push(reflection+direct+lining+bars+(random()-.5)*2*noise);
      }
      matrix.push(row);
    }
    return matrix;
  }
  function preprocess(matrix, options) {
    validateMatrix(matrix); options=options||{};
    var gain=numberOption(options.gain,1,'线性时间增益',0,20),background=options.background!==false;
    // 真正数值预处理：逐采样点沿道方向减均值，抑制水平相干背景；再乘 1+gain*r/(rows-1)。
    // 它不等同 RCAN 杂波抑制，更不是 RTM 偏移成像；保持输入矩阵不变。
    return matrix.map(function (row,r) { var mean=background?sum(row)/row.length:0, g=1+gain*r/(matrix.length-1); return row.map(function (v) { return (v-mean)*g; }); });
  }

  function validateTask(task, project) {
    if (!task||!project) throw new Error('任务和项目参数不能为空');
    if (!String(task.name||'').trim()) throw new Error('请输入任务名称');
    finite(task.start,'任务起点',project.start,project.start+project.length);
    finite(task.end,'任务终点',project.start,project.start+project.length);
    if (task.end<=task.start) throw new Error('任务终点必须大于起点');
    finite(task.speed,'行驶速度 (m/s)',.05,5); finite(task.spacing,'扫描间距 (m)',.01,2);
    if (task.scanStart!=null) finite(task.scanStart,'扫描起角 (°)',-180,180);
    if (task.scanEnd!=null) finite(task.scanEnd,'扫描止角 (°)',-180,180);
    if (task.scanStart!=null&&task.scanEnd!=null&&task.scanEnd<=task.scanStart) throw new Error('扫描止角必须大于起角');
    return Object.assign({},task,{name:String(task.name).trim()});
  }
  function simulateVehicle(params) {
    params=params||{};
    var start=numberOption(params.start,0,'作业起点',-1e8,1e8),end=numberOption(params.end,48,'作业终点',-1e8,1e8);
    if (end<=start) throw new Error('作业终点必须大于起点');
    var speed=numberOption(params.speed,.6,'行驶速度 (m/s)',.05,5),spacing=numberOption(params.spacing,.1,'扫描间距 (m)',.01,2);
    var scanWidth=numberOption(params.scanWidth,VEHICLE.scanWidth,'有效扫描幅宽 (m)',.001,2);
    var scanStart=numberOption(params.scanStart,-90,'扫描起角',-180,180),scanEnd=numberOption(params.scanEnd,90,'扫描止角',-180,180);
    if (scanEnd<=scanStart) throw new Error('扫描止角必须大于起角');
    var obstacles=params.obstacles||[];
    if (params.obstacle!=null&&params.obstacle!==false) obstacles=obstacles.concat([typeof params.obstacle==='number'?{x:params.obstacle}:params.obstacle]);
    if (!Array.isArray(obstacles)) throw new Error('障碍物应为数组');
    var events=[],stopAt=end,collision=false;
    obstacles.forEach(function (ob,i) {
      var x=numberOption(ob.x,24,'障碍物位置',-1e8,1e8),z=numberOption(ob.z,0,'障碍物横向位置',-100,100);
      var width=numberOption(ob.width,1,'障碍物宽度',.01,100),length=numberOption(ob.length,1,'障碍物长度',.01,100);
      // 保守二维包围盒：车沿 z=0 中心线直行；半车长/半障碍长 +0.3 m 安全裕度提前停障。
      // start/end 表示车辆中心路径；障碍 x 与 start/end 使用同一轴线坐标，不隐式转换里程。
      if (Math.abs(z)<=(width+VEHICLE.width)/2+VEHICLE.safety && x+length/2>=start-VEHICLE.length/2 && x-length/2<=end+VEHICLE.length/2) {
        var halt=x-length/2-VEHICLE.length/2-VEHICLE.safety;
        if (halt<stopAt) {
          stopAt=Math.max(start,halt);
          collision=Math.abs(x-start)<(length+VEHICLE.length)/2&&Math.abs(z)<(width+VEHICLE.width)/2;
        }
        events.push({obstacle:i,x:x,z:z,stopAt:Math.max(start,halt),action:halt<start?'起点安全区不足，禁止启动':'提前停障'});
      }
    });
    var travelled=Math.max(0,stopAt-start),longitudinal=travelled/(end-start),sampling=Math.min(1,scanWidth/spacing);
    // 仅对所选环向作业范围计算覆盖率。未进入区段全部漏检；已驶过区段按幅宽/间距估计覆盖。
    var coverage=100*longitudinal*sampling,missed=[];
    if (sampling<1&&travelled>0) missed.push({start:start,end:stopAt,reason:'扫描间距大于有效幅宽',fraction:1-sampling});
    if (stopAt<end) missed.push({start:stopAt,end:end,reason:'停障后未进入作业范围',fraction:1});
    return {duration:round(travelled/speed),plannedDuration:round((end-start)/speed),coverage:round(coverage),collision:collision,stopAt:round(stopAt),missed:missed,
      stopped:stopAt<end,events:events,distance:round(travelled),vehicle:clone(VEHICLE),scanRange:[scanStart,scanEnd],
      samplingCoverage:round(sampling*100),circumferenceFraction:(scanEnd-scanStart)/360,
      formula:'作业时间=实际中心路径长度/速度；覆盖率=已行驶长度/计划长度 × min(1,有效扫描幅宽/扫描间距) × 100%。',
      assumptions:'车辆长 2.4 m、宽 1.4 m，直线行驶，安全距离 0.3 m；障碍用平面包围盒判断；覆盖率针对所选环向范围，不计定位误差、机械臂动态及绕障。',source:'简化运动与几何覆盖演示'};
  }
  function simulateStructure(params) {
    params=params||{};
    var thickness=numberOption(params.thickness,.35,'衬砌厚度 (m)',.05,2),E=numberOption(params.E,30,'弹性模量 (GPa)',1,200);
    var load=numberOption(params.load,100,'均匀径向压力 (kPa)',0,10000),radius=numberOption(params.radius,2.7,'平均半径 (m)',.5,30);
    // 单位宽度薄圆环轴对称膜力模型：N=pR，sigma=pR/t，u=pR²/(Et)。输入 kPa/GPa 转 Pa，输出 mm/MPa。
    // 连续闭合圆环、小变形、线弹性、均匀外压、自由径向收缩；忽略弯矩、土弹簧、接缝、裂缝及屈曲。
    var p=load*1000,elastic=E*1e9,stress=p*radius/thickness/1e6,displacement=p*radius*radius/(elastic*thickness)*1000;
    return {displacement:round(displacement),stress:round(stress),formula:'N=pR；σ=pR/t；u=pR²/(Et)。输入 p:kPa，E:GPa，R/t:m；输出径向位移:mm、压应力:MPa。',
      assumptions:'单位宽度、连续闭合薄圆环、轴对称均匀外压、线弹性小变形、自由径向收缩；忽略接缝、土体约束、弯矩及局部缺陷。仅用于参数敏感性演示，不计算安全系数、极限承载力或鉴定结论。',
      warning:thickness/radius>.15?'厚径比超过 0.15，薄圆环假设适用性较差。':null,units:{displacement:'mm',stress:'MPa'},source:'简化解析圆环模型',version:'membrane-ring-1.0'};
  }
  function comparePlans(params) {
    params=params||{};
    var budget=numberOption(params.budget,60,'预算 (万元)',0,1e6),rawWeights=params.weights||[.3,.2,.5],weights;
    if (!Array.isArray(rawWeights)) rawWeights=[rawWeights.cost==null?.3:rawWeights.cost,rawWeights.duration==null?(rawWeights.time==null?.2:rawWeights.time):rawWeights.duration,rawWeights.reduction==null?(rawWeights.risk==null?.5:rawWeights.risk):rawWeights.reduction];
    if (rawWeights.length!==3) throw new Error('方案权重需要成本、工期、预期降险三项');
    rawWeights.forEach(function (w) { finite(w,'方案评价权重',0,100); });
    if (sum(rawWeights)<=0) throw new Error('至少一个方案评价权重大于 0');
    weights=normalize(rawWeights);
    var plans=[{id:'P1',name:'专项复检 + 加密监测',cost:8,duration:2,reduction:15,traffic:'夜间短时作业'},
      {id:'P2',name:'分段注浆 + 复检',cost:35,duration:7,reduction:55,traffic:'分段安排作业窗口'},
      {id:'P3',name:'综合修复 + 长期监测',cost:68,duration:14,reduction:78,traffic:'专项评估交通组织'}];
    plans.forEach(function (p) {
      p.affordable=p.cost<=budget; p.feasible=p.affordable;
      p.score=round(100*(weights[0]*(1-p.cost/68)+weights[1]*(1-p.duration/14)+weights[2]*p.reduction/100),3);
      p.reason=p.affordable?'预算内，按成本 / 工期 / 预期降险组合分排序':'超出预算 '+round(p.cost-budget,2)+' 万元';
      p.expectedReduction=p.reduction; p.source='预算、工期及降险比例为演示假设，未基于实际维修报价或效果标定';
      p.weights=weights; p.budget=budget;
    });
    plans.sort(function (a,b) { return Number(b.affordable)-Number(a.affordable)||b.score-a.score; });
    plans.forEach(function (p,i) { p.rank=i+1; p.recommended=i===0&&p.affordable; });
    return plans;
  }
  function exportSnapshot(state) {
    var assessment=assess(state),defects=getDefects(state),filters=clone(state.filters||{}),p=state.project;
    var start=Math.max(p.start,filters.start==null?p.start:filters.start),end=Math.min(p.start+p.length,filters.end==null?p.start+p.length:filters.end);
    var filteredAssessment=end>start?assessInternal(state,defects,{start:start,end:end}):null;
    // 批次边界必须在统一导出入口落实，不能仅依赖某个报告页面再次过滤。
    // 兼容业务实体的 batch 与雷达记录的 batchId；字段冲突、缺少归属的条目均不推定为当前批次。
    function isCurrentBatch(entry) {
      if (!entry || !(entry.batch || entry.batchId)) return false;
      return (!entry.batch || entry.batch===state.batch) && (!entry.batchId || entry.batchId===state.batch);
    }
    function batchEntries(entries) { return (Array.isArray(entries)?entries:[]).filter(isCurrentBatch).map(clone); }
    var scopedSimulation={},globalSimulationKeys=[],excludedSimulationKeys=[];
    Object.keys(state.simulation||{}).forEach(function (key) {
      var entry=state.simulation[key];
      if (isCurrentBatch(entry)) {
        scopedSimulation[key]=Object.assign(clone(entry),{exportScope:'当前检测批次 '+state.batch});
      } else if (key==='external' && entry && typeof entry==='object' && !entry.batch && !entry.batchId) {
        // 外部有限元文件当前未要求批次字段：保留为项目全局参考，并明确不属于本批次实测或诊断结果。
        scopedSimulation[key]=Object.assign(clone(entry),{exportScope:'项目全局外部分析参考，未绑定检测批次；不作为当前批次实测或诊断结论'});
        globalSimulationKeys.push(key);
      } else excludedSimulationKeys.push(key);
    });
    var scopedTasks=batchEntries(state.tasks),scopedAlerts=batchEntries(state.alerts),scopedLogs=batchEntries(state.logs);
    var scopedRadars=batchEntries(state.radars),scopedMaintenance=batchEntries(state.maintenance);
    return {schemaVersion:2,exportedAt:new Date().toISOString(),project:clone(p),batch:state.batch,batchInfo:clone(state.batches.find(function (b) { return b.id===state.batch; })),
      filters:filters,defects:defects,assessment:assessment,filteredAssessment:filteredAssessment,assessmentScope:assessment.scope.description,
      evaluationConfig:{alpha:state.alpha,ahp:clone(state.ahp),thresholds:state.thresholds.slice(),severeDiameter:state.severeDiameter},
      tasks:scopedTasks,alerts:scopedAlerts,logs:scopedLogs,maintenance:scopedMaintenance,processing:clone(state.processing),simulation:scopedSimulation,
      radarData:scopedRadars.map(function (r) { delete r.matrix; return r; }),
      exportScope:{batch:state.batch,records:'检测任务、预警、日志、雷达及治理任务仅包含明确归属当前批次的记录；缺失归属或批次字段冲突的记录排除。',
        excludedCounts:{tasks:(state.tasks||[]).length-scopedTasks.length,alerts:(state.alerts||[]).length-scopedAlerts.length,logs:(state.logs||[]).length-scopedLogs.length,
          radarData:(state.radars||[]).length-scopedRadars.length,maintenance:(state.maintenance||[]).length-scopedMaintenance.length},
        simulation:'仅保留当前批次仿真；无批次外部分析以项目全局参考单独标注，不视为本批次结果。',globalSimulationKeys:globalSimulationKeys,excludedSimulationKeys:excludedSimulationKeys},
      evidenceRecords:clone((state.evidenceRecords||[]).filter(function(r){return r.batchId===state.batch;})),
      evidenceBoundary:'现场类型为人工标注；对应位置保留相对映射 / 标定依据，未自动纳入演示 SHI。',
      coordinateDefinition:'X=里程−项目起点；Y=(内半径+径向埋深)cos角度；Z=(内半径+径向埋深)sin角度；环向 0° 拱顶，90° 右墙，单位 m；病害定位于管片环中心。',
      sources:['原平台 16 条演示台账；历史批次为尺寸比例派生','外部 CSV / JSON / 网关帧只解析数值与元数据，不自动产生病害诊断','RCAN / RTM / 候选识别模型尚未接入，真实预处理为减背景与线性时间增益'],
      limitations:['演示评估不构成正式工程鉴定。','列表筛选不改变全局评估；filteredAssessment 单列当前筛选范围的诊断性分数。','置信度非诊断可靠性；内置证据与仿真信号均为演示。','严重病害直径规则适用于全部演示候选类型，实际规则应由专业人员按病害类别审定。']};
  }
  return {VERSION:VERSION,TYPES:TYPES,RISKS:RISKS,VEHICLE:VEHICLE,LIMITS:LIMITS,createState:createState,getDefects:getDefects,setReview:setReview,
    assess:assess,calculateAHP:calculateAHP,entropyWeights:entropyWeights,riskFromScore:riskFromScore,parseCSV:parseCSV,
    generateRadar:generateRadar,preprocess:preprocess,validateTask:validateTask,simulateVehicle:simulateVehicle,simulateStructure:simulateStructure,
    comparePlans:comparePlans,exportSnapshot:exportSnapshot};
}));
