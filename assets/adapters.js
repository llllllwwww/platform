/* 算法适配器注册表：仅调用显式注册的本地回调，无网络访问、无默认算法替身。
 * 浏览器：window.TunnelAdapters；Node：require('./adapters.js')。
 * 终态回调：run(payload, {signal}) -> {status:'completed', source, version, ...results}。
 * failed/cancelled 转为异常；只有通过输出契约校验的 completed 结果进入缓存。
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TunnelAdapters = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  var DEFAULT_NAMES = ['RCAN', 'RTM', 'TunGPR', 'T-GPRMask'];
  var registry = new Map(), cache = new Map(), epoch = 0, CACHE_LIMIT = 8;

  function requiredText(value, field) {
    if (typeof value !== 'string' || !value.trim()) throw new TypeError(field + ' 必须是非空字符串');
    return value.trim();
  }
  function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
  function abortError(message) { var e = new Error(message || '算法任务已取消'); e.name = 'AbortError'; return e; }
  function checkAbort(signal) { if (signal && signal.aborted) throw abortError(); }
  function checkSignal(signal) {
    if (signal && (typeof signal.aborted !== 'boolean' || typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function'))
      throw new TypeError('signal 必须是 AbortSignal');
  }
  // 规范 JSON 序列化：排序对象键，保留数组顺序，拒绝非有限数字、不可序列化值及循环。
  // 缓存使用完整字符串键而非短哈希，因此不会因哈希碰撞混用不同矩阵或标定。
  function canonical(value, ancestors) {
    ancestors = ancestors || new Set();
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) throw new TypeError('算法数据必须包含有限数值');
      return JSON.stringify(value);
    }
    if (typeof value !== 'object') throw new TypeError('算法数据必须为 JSON 可序列化内容，不允许 undefined、函数或其他特殊值');
    if (ancestors.has(value)) throw new TypeError('算法数据不能包含循环引用');
    var proto = Object.getPrototypeOf(value);
    if (!Array.isArray(value) && proto !== Object.prototype && proto !== null) throw new TypeError('算法数据仅支持普通 JSON 对象和数组');
    ancestors.add(value);
    var text;
    if (Array.isArray(value)) {
      // Array.map 会跳过空洞，因此显式遍历，拒绝稀疏数组。
      var cells = [];
      for (var i=0;i<value.length;i++) {
        if (!Object.prototype.hasOwnProperty.call(value,i)) throw new TypeError('算法数据不能包含稀疏数组');
        cells.push(canonical(value[i],ancestors));
      }
      text = '['+cells.join(',')+']';
    } else {
      text = '{'+Object.keys(value).sort().map(function (key) { return JSON.stringify(key)+':'+canonical(value[key],ancestors); }).join(',')+'}';
    }
    ancestors.delete(value);
    return text;
  }
  function copy(value) { return JSON.parse(canonical(value)); }
  function preparePayload(input) {
    if (!object(input)) throw new TypeError('算法输入必须为对象');
    var matrix=input.matrix;
    if (!Array.isArray(matrix) || matrix.length<2 || matrix.length>512 || !Array.isArray(matrix[0]) || matrix[0].length<2 || matrix[0].length>2048)
      throw new TypeError('算法矩阵必须为 2×2 至 512×2048 的二维数组');
    var cols=matrix[0].length;
    matrix.forEach(function (row) {
      if (!Array.isArray(row)||row.length!==cols) throw new TypeError('算法矩阵必须为矩形');
      for (var j=0;j<cols;j++) if (typeof row[j]!=='number'||!Number.isFinite(row[j])) throw new TypeError('算法矩阵只能包含有限数字');
    });
    // 允许缺省业务关联，但显式置 null 并参与缓存键，绝不自动猜测项目/批次/测线。
    // 真正模型接入可在其回调中对所需关联和标定进一步严格校验。
    var payload={matrix:matrix,metadata:input.metadata==null?{}:input.metadata,parameters:input.parameters==null?{}:input.parameters,
      projectId:input.projectId==null?null:input.projectId,batchId:input.batchId==null?null:input.batchId,
      taskId:input.taskId==null?null:input.taskId,lineId:input.lineId==null?null:input.lineId,source:requiredText(input.source,'输入 source')};
    if (!object(payload.metadata)||!object(payload.parameters)) throw new TypeError('metadata 和 parameters 必须为 JSON 对象');
    ['projectId','batchId','taskId','lineId'].forEach(function (key) { if (payload[key]!==null) requiredText(payload[key],key); });
    return copy(payload);
  }
  function validateResult(result, adapter) {
    if (!object(result)) throw new TypeError('算法输出必须是包含 source、version、status 的对象');
    requiredText(result.source,'输出 source');
    var version=requiredText(result.version,'输出 version');
    var status=requiredText(result.status,'输出 status');
    if (version!==adapter.version) throw new TypeError('算法输出版本与注册版本不一致');
    if (!['completed','failed','cancelled'].includes(status)) throw new TypeError('算法输出状态非法：'+status+'；必须为 completed、failed 或 cancelled');
    if (status==='failed') throw new Error(typeof result.message==='string'&&result.message.trim()?result.message:'算法回调报告处理失败');
    if (status==='cancelled') throw abortError(typeof result.message==='string'?result.message:undefined);
    return copy(result);
  }
  function clearCache() { cache.clear(); epoch++; }
  function register(name, adapter) {
    name=requiredText(name,'算法名称');
    if (!object(adapter)) throw new TypeError('适配器必须包含 version 和 run');
    var version=requiredText(adapter.version,'适配器 version');
    if (typeof adapter.run!=='function') throw new TypeError('适配器 run 必须为函数');
    registry.set(name,{version:version,run:adapter.run});
    // 重新注册即使沿用版本号也不能继承旧回调缓存，且会阻止旧执行中的任务回填缓存。
    clearCache();
    return {name:name,version:version,connected:true};
  }
  function list() {
    var names=DEFAULT_NAMES.concat(Array.from(registry.keys()).filter(function (n) { return !DEFAULT_NAMES.includes(n); }));
    return names.map(function (name) { var a=registry.get(name); return {name:name,version:a?a.version:null,connected:!!a,status:a?'registered':'unconnected'}; });
  }
  async function run(name, input, options) {
    options=options||{};
    var signal=options.signal;
    checkSignal(signal); checkAbort(signal);
    name=requiredText(name,'算法名称');
    var adapter=registry.get(name);
    if (!adapter) throw new Error(name+' 未接入：请显式注册已验证且带版本的算法回调；未生成推理结果');
    var payload=preparePayload(input);
    var key=canonical({name:name,version:adapter.version,input:payload});
    checkAbort(signal);
    if (cache.has(key)) {
      // 最多保留 8 个完成结果；最近使用的条目移到末尾，避免矩阵多版本无限累积。
      var cached=cache.get(key); cache.delete(key); cache.set(key,cached);
      return copy(cached);
    }
    var startEpoch=epoch, onAbort;
    // 不共享执行中的 Promise：每次调用独立管理 signal，取消一个任务不会误取消另一个任务。
    var cancellation=signal?new Promise(function (_,reject) {
      onAbort=function () { reject(abortError()); };
      signal.addEventListener('abort',onAbort,{once:true});
      if (signal.aborted) onAbort();
    }):null;
    try {
      checkAbort(signal);
      // 回调获得输入副本；任意算法内就地处理不会污染调用者的原始矩阵/元数据。
      var operation=Promise.resolve().then(function () { checkAbort(signal); return adapter.run(copy(payload),{signal:signal}); });
      var result=await (cancellation?Promise.race([operation,cancellation]):operation);
      checkAbort(signal);
      var checked=validateResult(result,adapter);
      checkAbort(signal);
      if (epoch===startEpoch && registry.get(name)===adapter) {
        cache.delete(key);
        while (cache.size>=CACHE_LIMIT) cache.delete(cache.keys().next().value);
        cache.set(key,copy(checked));
      }
      return copy(checked);
    } finally {
      if (signal&&onAbort) signal.removeEventListener('abort',onAbort);
    }
  }
  return {register:register,run:run,clearCache:clearCache,list:list};
}));
