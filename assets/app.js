/* 隧雷智检 V2 工作台。静态离线应用：业务状态只在浏览器本地保存，不上传任何文件。 */
(function () {
  "use strict";
  const C = window.TunnelCore,
    $ = (s) => document.querySelector(s),
    $$ = (s) => Array.from(document.querySelectorAll(s));
  const STORE = "slzj-workbench-v2",
    NAMES = { I: "严重", II: "较重", III: "关注", IV: "低风险" },
    REVIEW = { pending: "待复核", confirmed: "已复核", rejected: "已驳回" };
  const escape = (s) =>
    String(s == null ? "" : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const fmt = (n, d = 1) => Number(n).toFixed(d),
    mile = (n) =>
      "K" + Math.floor(n / 1000) + "+" + fmt(n % 1000, 1).padStart(5, "0");
  let state = C.createState(),
    persistenceError = "",
    route = "overview",
    simTab = "sample",
    simResult = null,
    processed = null,
    trace = 80,
    palette = 1,
    zoom = 1,
    timer = null,
    guide = 0;
  let overlayRegions = true;
  let metadataText = "",
    importError = "",
    importNotice = "",
    radarMode = "demo",
    selectedRadar = 0,
    taskRunning = false;
  const taskPanels = { equipment: false, evidence: false };
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "null");
    if (saved && saved.schemaVersion === 2) {
      C.assess(saved);
      state = saved;
      state.tasks.forEach((t) => {
        if (t.status === "running") t.status = "paused";
      });
    }
  } catch (e) {
    persistenceError = "本地缓存不可用或格式不兼容，已使用初始演示。";
  }
  state.tasks.forEach((t) => {
    if (!t.batch) t.batch = "B202609";
  });
  state.processingByBatch = state.processingByBatch || {};
  state.simulationsByBatch = state.simulationsByBatch || {};
  state.maintenance = state.maintenance || [];
  state.simulation = state.simulation || {};
  state.processing = state.processing || {};
  // 清理已经移除的识别模型选择；保留输入、数值处理结果与其他业务数据。
  function clearRetiredModelChoice(record) {
    if (!record || typeof record !== "object") return false;
    const hadChoice = Object.prototype.hasOwnProperty.call(record, "model"),
      oldChoice = record.model,
      message = typeof record.message === "string" ? record.message : "";
    const obsoleteFailure = record.status === "failed" && (
      (typeof oldChoice === "string" && oldChoice.length > 0 && message.includes(oldChoice)) ||
      (/算法.*未接入/.test(message) && !message.includes("RCAN") && !message.includes("RTM"))
    );
    if (hadChoice) delete record.model;
    if (obsoleteFailure) record.message = "旧识别模型入口已移除；当前影像检测使用独立裂缝分割流程。";
    return hadChoice || obsoleteFailure;
  }
  let retiredChoiceCleared = clearRetiredModelChoice(state.processing);
  for (const record of Object.values(state.processingByBatch)) {
    retiredChoiceCleared = clearRetiredModelChoice(record) || retiredChoiceCleared;
  }
  if (retiredChoiceCleared) save();
  state.scenePrefs = {
    shell: true,
    defect: true,
    rebar: false,
    grout: false,
    ground: false,
    profile: false,
    car: true,
    ray: true,
    coverage: true,
    risk: false,
    labels: true,
    labelsAll: false,
    hud: true,
    stars: true,
    city: true,
    opacity: 0.5,
    cut: null,
    backgroundImage: null,
    ...state.scenePrefs,
  };
  window.TunnelEvidenceCore.init(state);
  const icons = {
    overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    tasks: "M3 17V7h12l5 5v5H3z M7 17v3 M17 17v3 M6 11h7 M15 7v5h5",
    radar: "M3 12h3l2-6 4 13 3-10 2 3h4",
    processing: "M5 3v18 M12 3v18 M19 3v18 M2 8h6 M9 16h6 M16 7h6",
    defects: "M5 3h14v18H5z M8 7h8 M8 12h8 M8 17h5",
    twin: "M12 2 3 7v10l9 5 9-5V7z M3 7l9 5 9-5 M12 12v10",
    health: "M3 12h4l3-7 4 14 3-7h4",
    alerts: "M12 3 2 21h20z M12 9v5 M12 17v1",
    reports: "M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h8",
    simulation: "M8 3h8 M9 3v6l-6 10q-1 2 2 2h14q3 0 2-2L15 9V3 M7 15h10",
  };
  const groups = [
    { title: "工作台", items: [["overview", "项目总览"]] },
    {
      title: "01 / 检测采集",
      items: [
        ["tasks", "检测任务与设备"],
        ["radar", "雷达数据管理"],
      ],
    },
    {
      title: "02 / 智能解析",
      items: [
        ["processing", "智能处理工作流"],
        ["defects", "病害清单与复核"],
      ],
    },
    {
      title: "03 / 孪生评估与运维",
      items: [
        ["twin", "三维数字孪生"],
        ["health", "结构健康评估"],
        ["alerts", "预警与治理"],
        ["reports", "检测报告"],
      ],
    },
    { title: "04 / 仿真推演", items: [["simulation", "仿真验证工作台"]] },
  ];
  const multisceneEntries = [
    {
      id: "overview",
      label: "多场景总览",
      src: "algorithm/web/multiscene/index.html",
    },
    {
      id: "dvp_handheld",
      label: "DVP 手持",
      src: "algorithm/web/multiscene/dvp_handheld/index.html",
    },
    {
      id: "rail_test",
      label: "铁路短片",
      src: "algorithm/web/multiscene/rail_test/index.html",
    },
    {
      id: "rail_train",
      label: "铁路长片",
      src: "algorithm/web/multiscene/rail_train/index.html",
    },
    {
      id: "tumvi_fisheye",
      label: "TUM VI 鱼眼",
      src: "algorithm/web/multiscene/tumvi_fisheye/index.html",
    },
  ];
  let activeMultiscene = "overview";
  const equipmentEntries = [
    {
      id: "vehicle",
      no: "01",
      name: "三臂智能检测车",
      role: "移动作业平台 · 机械臂 · 车载雷达",
      status: "场景已接入",
      tone: "mint",
      description: "平台的主作业载体，承载三臂机械臂、车载雷达、照明和车载显示单元。",
      specs: ["车体：2.4 × 1.4 m", "速度：0.05–5 m/s", "作业：启动 / 暂停 / 停障"],
      platform: "检测任务与设备 → 三维作业场景；支持第一人称、自由漫游和车载 HUD。",
    },
    {
      id: "radar",
      no: "02",
      name: "车载探地雷达阵列",
      role: "天线阵列 · B-scan · 单道波形",
      status: "接口示意",
      tone: "blue",
      description: "负责衬砌内部回波采集和测线记录，平台用 B-scan、单道波形与 CSV 目录展示接入边界。",
      specs: ["输入：矩形数值矩阵", "元数据：道间距 / 采样间隔", "输出：B-scan、单道和来源记录"],
      platform: "雷达数据管理 → 导入 CSV + JSON；当前示意信号与真实设备格式分开标识。",
    },
    {
      id: "vision",
      no: "03",
      name: "影像与照明单元",
      role: "前视相机 · 补光 · 候选证据",
      status: "复核链路",
      tone: "mint",
      description: "采集隧道表面影像、同帧画面和邻帧证据，为第二模块的影像三维复核提供来源。",
      specs: ["视图：前视 / 鱼眼示意", "证据：原始帧 / 掩码 / 邻帧", "用途：候选复核，不等于确诊"],
      platform: "检测采集 → 现场视频接入；智能解析 → 病害清单与隧道影像三维复核。",
    },
    {
      id: "pose",
      no: "04",
      name: "定位与姿态采集",
      role: "里程编码器 · IMU · 环位关联",
      status: "定位关联",
      tone: "amber",
      description: "把车辆行进、扫描轨迹和姿态信息关联到里程、环号和三维坐标，支撑后续定位评估。",
      specs: ["关联：里程 / 环号 / 轨迹", "姿态：车辆方向与扫描角", "校核：单位和坐标定义"],
      platform: "检测任务参数与三维数字孪生共享状态；当前为可复现的几何演示配置。",
    },
    {
      id: "edge",
      no: "05",
      name: "边缘采集与数据终端",
      role: "采集控制 · 本地存储 · 状态回传",
      status: "数据接入",
      tone: "blue",
      description: "现场负责采集控制、缓存和数据整理的终端，保证原始矩阵、元数据和作业日志可追溯。",
      specs: ["数据：CSV / JSON / 日志", "状态：任务进度与设备状态", "边界：本地浏览器演示，无后端服务"],
      platform: "本地工作台与浏览器 localStorage；重要原始数据需单独保存和备份。",
    },
    {
      id: "safety",
      no: "06",
      name: "安全与辅助保障",
      role: "警示 · 照明 · 通信 · 人员防护",
      status: "作业保障",
      tone: "amber",
      description: "用于现场封锁、照明、通信和人员安全的辅助设备，保证检测任务按计划执行。",
      specs: ["安全：警示灯 / 反光标识", "环境：照明 / 通风检查", "管理：对讲和作业记录"],
      platform: "任务前置检查和作业日志；平台只记录演示状态，不替代现场安全审批。",
    },
  ];
  let activeEquipment = "vehicle";
  const parentRoutes = {
    tasks: "overview",
    radar: "tasks",
    processing: "radar",
    defects: "processing",
    twin: "defects",
    health: "twin",
    alerts: "health",
    reports: "alerts",
    simulation: "overview",
  };
  let routeTrail = [],
    pendingAnchor = "";
  const icon = (k) =>
    `<span class="nav-icon"><svg viewBox="0 0 24 24"><path d="${icons[k] || icons.overview}"/></svg></span>`;
  const risk = (r) => `<span class="risk risk-${r}">${r} · ${NAMES[r]}</span>`;
  const btn = (text, action, cls = "", attrs = "") =>
    `<button class="${cls}" data-action="${action}" ${attrs}>${text}</button>`;
  const field = (label, id, value, min, max, step = 1) =>
    `<label class="field">${label}<input type="number" id="${id}" value="${value}" min="${min}" max="${max}" step="${step}" required></label>`;
  const note = (text, type = "") => `<div class="note ${type}">${text}</div>`;
  const panel = (title, body, aside = "", padding = true) =>
    `<section class="panel"><div class="panel-head"><h2>${title}</h2>${aside}</div>${padding ? '<div class="panel-body">' : ""}${body}${padding ? "</div>" : ""}</section>`;
  const equipmentSvg = (id) =>
    ({
      vehicle: `<svg viewBox="0 0 240 116" role="img" aria-label="三臂智能检测车示意图"><path d="M31 78h151l16-12h20" fill="none" stroke="#56d9b1" stroke-width="3" stroke-linecap="round"/><rect x="36" y="47" width="135" height="39" rx="8" fill="#162c3d" stroke="#56d9b1" stroke-width="2"/><path d="M55 47 68 27h60l24 20" fill="#1c3a4b" stroke="#7f9bb5" stroke-width="2"/><path d="M79 47V22l-16-12M79 22l18-14M115 47V19l18-14M115 19l-15-10" fill="none" stroke="#efb65d" stroke-width="3" stroke-linecap="round"/><rect x="88" y="53" width="32" height="13" rx="3" fill="#0b1018" stroke="#72afff"/><circle cx="66" cy="88" r="13" fill="#0b1018" stroke="#efb65d" stroke-width="3"/><circle cx="145" cy="88" r="13" fill="#0b1018" stroke="#efb65d" stroke-width="3"/><path d="M186 37h19v21h-19zM205 47h15" fill="none" stroke="#56d9b1" stroke-width="2"/><text x="120" y="108" fill="#9fb2c4" font-size="9" text-anchor="middle">车载移动扫描平台</text></svg>`,
      radar: `<svg viewBox="0 0 240 116" role="img" aria-label="车载探地雷达阵列示意图"><rect x="57" y="22" width="126" height="35" rx="6" fill="#162c3d" stroke="#72afff" stroke-width="2"/><path d="M75 57v22h90V57" fill="#1b3c4c" stroke="#56d9b1" stroke-width="2"/><path d="M89 79v13M108 79v13M127 79v13M146 79v13" stroke="#efb65d" stroke-width="5" stroke-linecap="round"/><path d="M78 39h84M89 31h62" stroke="#9fb2c4" stroke-width="2"/><path d="M183 33q22 14 0 28M194 25q36 22 0 44" fill="none" stroke="#56d9b1" stroke-width="2" opacity=".85"/><path d="M57 33Q35 46 57 60M46 25Q10 46 46 68" fill="none" stroke="#72afff" stroke-width="2" opacity=".75"/><text x="120" y="108" fill="#9fb2c4" font-size="9" text-anchor="middle">天线阵列 · B-scan</text></svg>`,
      vision: `<svg viewBox="0 0 240 116" role="img" aria-label="影像与照明单元示意图"><rect x="57" y="35" width="88" height="48" rx="8" fill="#162c3d" stroke="#56d9b1" stroke-width="2"/><circle cx="104" cy="59" r="20" fill="#0b1018" stroke="#72afff" stroke-width="4"/><circle cx="104" cy="59" r="8" fill="#56d9b1"/><rect x="74" y="25" width="27" height="10" rx="3" fill="#efb65d"/><path d="M145 47h31l28 12-28 12h-31z" fill="#1d3b4d" stroke="#f5b942" stroke-width="2" opacity=".9"/><path d="M177 46 220 28M177 72l43 18" stroke="#f5b942" stroke-width="2" stroke-dasharray="4 4"/><circle cx="183" cy="59" r="4" fill="#ff6b6b"/><text x="120" y="108" fill="#9fb2c4" font-size="9" text-anchor="middle">前视相机 · 补光 · 同帧证据</text></svg>`,
      pose: `<svg viewBox="0 0 240 116" role="img" aria-label="定位与姿态采集示意图"><circle cx="79" cy="61" r="34" fill="#162c3d" stroke="#efb65d" stroke-width="3"/><circle cx="79" cy="61" r="11" fill="#0b1018" stroke="#56d9b1" stroke-width="3"/><path d="M79 22v78M40 61h78" stroke="#7f9bb5" stroke-dasharray="3 4"/><rect x="132" y="31" width="58" height="45" rx="6" fill="#1c3a4b" stroke="#72afff" stroke-width="2"/><path d="M145 44h32M145 54h20M145 64h27" stroke="#56d9b1" stroke-width="2"/><path d="M113 61h18" stroke="#efb65d" stroke-width="3" marker-end="url(#equipmentArrow)"/><path d="M30 100h177" stroke="#7f9bb5" stroke-width="2"/><circle cx="41" cy="100" r="4" fill="#56d9b1"/><circle cx="102" cy="100" r="4" fill="#56d9b1"/><circle cx="174" cy="100" r="4" fill="#56d9b1"/><text x="120" y="17" fill="#9fb2c4" font-size="9" text-anchor="middle">里程 · IMU · 环位</text></svg>`,
      edge: `<svg viewBox="0 0 240 116" role="img" aria-label="边缘采集与数据终端示意图"><path d="M49 31h142l13 65H36z" fill="#162c3d" stroke="#72afff" stroke-width="2"/><rect x="63" y="43" width="114" height="39" rx="4" fill="#0b1018" stroke="#56d9b1"/><path d="M76 55h38M76 64h63M76 73h29" stroke="#9fb2c4" stroke-width="2"/><circle cx="155" cy="55" r="4" fill="#56d9b1"/><circle cx="155" cy="68" r="4" fill="#efb65d"/><path d="M103 98h35" stroke="#7f9bb5" stroke-width="4" stroke-linecap="round"/><path d="M191 53h22M191 64h32M191 75h22" stroke="#56d9b1" stroke-width="2"/><text x="120" y="112" fill="#9fb2c4" font-size="9" text-anchor="middle">采集控制 · 本地缓存 · 日志</text></svg>`,
      safety: `<svg viewBox="0 0 240 116" role="img" aria-label="安全与辅助保障示意图"><path d="M39 83h162" stroke="#7f9bb5" stroke-width="3"/><path d="M57 82 73 39l16 43M106 82l16-43 16 43M155 82l16-43 16 43" fill="#3a2d22" stroke="#f5b942" stroke-width="3"/><path d="M67 56h12M116 56h12M165 56h12" stroke="#ff6b6b" stroke-width="4"/><path d="M52 28q18-19 36 0v8H52z" fill="#1c3a4b" stroke="#56d9b1" stroke-width="2"/><path d="M43 28h54" stroke="#56d9b1" stroke-width="3"/><path d="M205 28v54M195 38h20M195 50h20M195 62h20" stroke="#72afff" stroke-width="2"/><text x="120" y="108" fill="#9fb2c4" font-size="9" text-anchor="middle">警示 · 照明 · 通信 · 防护</text></svg>`,
    })[id] || "";
  const equipmentDetail = (id = activeEquipment) => {
    const item = equipmentEntries.find((x) => x.id === id) || equipmentEntries[0];
    return `<div class="equipment-detail-head"><div><div class="equipment-kicker">SELECTED EQUIPMENT · ${item.no}</div><h3>${escape(item.name)}</h3><p>${escape(item.description)}</p></div><span class="badge ${item.tone}">${escape(item.status)}</span></div><div class="equipment-specs">${item.specs.map((x) => `<span>${escape(x)}</span>`).join("")}</div><div class="equipment-platform"><b>平台关联</b><span>${escape(item.platform)}</span></div>`;
  };
  const equipmentPanel = () =>
    panel(
      "项目设备示意",
      `<div class="equipment-intro"><div><b>现场采集链路</b><p>用示意图快速认识本项目需要的设备、数据流和平台入口；点击卡片查看配置说明。</p></div><span class="badge mint">6 类设备 / 组件</span></div><div class="equipment-flow" aria-label="设备数据链路"><span>现场采集</span><i>→</i><span>边缘记录</span><i>→</i><span>智能解析</span><i>→</i><span>三维复核</span></div><div class="equipment-grid">${equipmentEntries.map((item) => `<button type="button" class="equipment-card${activeEquipment === item.id ? " active" : ""}" data-action="equipment-select" data-equipment="${item.id}" aria-pressed="${activeEquipment === item.id}"><span class="equipment-card-top"><span class="equipment-no">${item.no}</span><span class="badge ${item.tone}">${escape(item.status)}</span></span><span class="equipment-visual">${equipmentSvg(item.id)}</span><strong>${escape(item.name)}</strong><small>${escape(item.role)}</small><span class="equipment-more">查看配置 <span>→</span></span></button>`).join("")}</div><div class="equipment-detail" id="equipmentDetail">${equipmentDetail()}</div><p class="muted equipment-note">图形为项目配置示意，不代表当前已经接入真实硬件；真实设备接入仍需补充驱动、采集协议、标定和现场安全验证。</p>`,
      '<span class="badge mint">设备链路 · 演示</span>',
    );
  const metric = (label, value, unit, hint, target, cls = "") =>
    `<button class="metric" data-nav="${target}"><div class="label">${label}<span>↗</span></div><div class="value ${cls}">${value}<small>${unit}</small></div><div class="hint">${hint}</div></button>`;
  function toast(message, error = false) {
    let el = $("#toast");
    el.textContent = message;
    el.className = "toast show" + (error ? " error" : "");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.remove("show"), 3000);
  }
  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify(state));
      $("#saveStatus").textContent =
        "已保存在本地 · " + new Date().toLocaleTimeString("zh-CN");
    } catch (e) {
      $("#saveStatus").textContent = "存储空间不足 · 本次状态仅保留在内存";
      toast("浏览器存储不足，请导出 JSON 保存当前记录。", true);
    }
  }
  function log(message) {
    state.logs.unshift({
      time: new Date().toISOString(),
      message,
      batch: state.batch,
    });
    state.logs = state.logs.slice(0, 100);
    save();
  }
  function batchTasks() {
    return state.tasks.filter((t) => t.batch === state.batch);
  }
  function task() {
    let list = batchTasks();
    if (!list.length) {
      const t = {
        ...C.createState().tasks[0],
        id: "T-" + state.batch,
        batch: state.batch,
        name: state.batch === "B202607" ? "历史批次复演任务" : "新批次检测任务",
      };
      state.tasks.push(t);
      list = [t];
    }
    return list.find((t) => t.id === state.activeTask) || list[0];
  }
  function all() {
    return C.getDefects(state, { filtered: false });
  }
  function chosen() {
    return all().find((d) => d.id === state.selectedId) || all()[0];
  }
  function currentAlerts() {
    return state.alerts.filter((a) => a.batch === state.batch);
  }
  function ensureAlerts() {
    const list = all();
    list
      .filter((d) => d.risk === "I" || d.risk === "II")
      .forEach((d) => {
        if (
          !state.alerts.some(
            (a) => a.batch === state.batch && a.defectId === d.id,
          )
        )
          state.alerts.push({
            id: "A-" + state.batch + "-" + d.id,
            defectId: d.id,
            batch: state.batch,
            status: "new",
            createdAt: new Date().toISOString(),
            condition: d.severe ? "直径达到演示严重阈值" : "SHI 低于关注阈值",
            level: d.risk,
            history: [
              {
                time: new Date().toISOString(),
                status: "new",
                note: "演示规则生成",
              },
            ],
          });
      });
    state.alerts
      .filter((a) => a.batch === state.batch)
      .forEach((a) => {
        const d = list.find((x) => x.id === a.defectId);
        if (d) {
          a.level = d.risk;
          a.ruleActive =
            d.review !== "rejected" && ["I", "II"].includes(d.risk);
          a.condition = d.severe
            ? "直径 ≥ " + state.severeDiameter + " m（演示严重规则）"
            : "病害分值 " + fmt(d.score) + " / 100";
        }
      });
  }
  function navigate(next) {
    if (route === next) {
      render();
      return;
    }
    location.hash = next;
  }
  function syncRouteTrail(next) {
    const last = routeTrail[routeTrail.length - 1],
      previous = routeTrail[routeTrail.length - 2];
    if (!last) routeTrail = [next];
    else if (last !== next) {
      if (previous === next) routeTrail.pop();
      else {
        routeTrail.push(next);
        if (routeTrail.length > 32) routeTrail.shift();
      }
    }
    return next;
  }
  function backRoute(fallback) {
    const target =
      routeTrail.length > 1
        ? routeTrail[routeTrail.length - 2]
        : fallback || parentRoutes[route] || "overview";
    if (routeTrail.length > 1) routeTrail.pop();
    location.hash = target === route ? fallback || parentRoutes[route] || "overview" : target;
  }
  function scrollToAnchor(id) {
    const scroll = () => {
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        pendingAnchor = "";
      }
    };
    pendingAnchor = id;
    if (route === "defects") requestAnimationFrame(scroll);
  }
  function pageNavigation() {
    if (route === "overview") return "";
    const fallback = parentRoutes[route] || "overview";
    return (
      btn("← 返回上一页", "go-back", "subtle back-button", `data-fallback="${fallback}"`) +
      btn("返回项目总览", "go-overview", "small subtle")
    );
  }
  function heading(title, desc, part, actions = "") {
    return `<div class="page-heading"><div><div class="eyebrow">${part}</div><h1>${title}</h1><p>${desc}</p></div><div class="heading-actions">${pageNavigation()}${actions}</div></div>`;
  }
  function tabs(items) {
    return `<div class="subnav">${items.map(([id, name]) => `<a href="#${id}" class="${route === id ? "active" : ""}">${name}</a>`).join("")}</div>`;
  }
  function moduleTabs() {
    const group = groups.find((g) => g.items.some((x) => x[0] === route));
    return group && group.items.length > 1 ? tabs(group.items) : "";
  }
  function filters() {
    return `<div class="filter-bar"><label>病害类型<select data-filter="type" aria-label="病害类型筛选"><option value="all">全部类型</option>${Object.entries(
      C.TYPES,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${state.filters.type === k ? "selected" : ""}>${v.name}</option>`,
      )
      .join(
        "",
      )}</select></label><label>风险等级<select data-filter="risk" aria-label="风险等级筛选"><option value="all">全部等级</option>${C.RISKS.map((r) => `<option ${state.filters.risk === r ? "selected" : ""}>${r}</option>`).join("")}</select></label><label>复核状态<select data-filter="review" aria-label="复核状态筛选"><option value="all">全部状态</option>${Object.entries(
      REVIEW,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${state.filters.review === k ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label><label>最低置信度<input class="confidence" type="number" data-filter="minConfidence" value="${state.filters.minConfidence}" min="0" max="1" step="0.05" aria-label="最低置信度"></label><label>起点里程 / m<input type="number" data-filter="start" value="${state.filters.start}" min="3128" max="3175.9" step="0.1" aria-label="筛选起点"></label><label>终点里程 / m<input type="number" data-filter="end" value="${state.filters.end}" min="3128.1" max="3176" step="0.1" aria-label="筛选终点"></label>${btn("重置", "reset-filters", "small subtle")}</div>`;
  }
  function scene(large = false, controls = false) {
    const pref = state.scenePrefs;
    const check = (name, label) =>
      `<label><input type="checkbox" data-layer="${name}" ${pref[name] ? "checked" : ""}>${label}</label>`;
    return `<div class="scene ${large ? "large" : ""}"><div class="scene-tags"><span class="badge mint" data-scene-data>${evidenceActive()?"疑似位置 · 仿真":"数字孪生 · 演示"}</span><span class="badge">40 环 / 48 m</span></div><iframe id="twinFrame" title="隧道三维交互场景" src="assets/tunnel-scene.html"></iframe><div class="scene-note">鼠标旋转 / 缩放 / 平移 · F 第一人称 · P 启停 · G 自由漫游</div></div>
    <div class="scene-tools">${btn("全景", "view", "small", 'data-view="iso"')}${btn("侧视", "view", "small", 'data-view="side"')}${btn("俯视", "view", "small", 'data-view="top"')}${btn("第一人称视角", "roam", "small")}${btn(taskRunning ? "暂停检测" : "启动 / 继续", "scene-run", "small primary")}${btn("自由漫游", "fly", "small")}${btn("全屏", "fullscreen-scene", "small subtle")}</div>
    <details class="scene-settings" ${controls ? "open" : ""}><summary>场景工具、图层与背景</summary><div class="scene-tools">
    ${btn("洞内视角", "view", "small", 'data-view="in"')}${btn("横断面", "view", "small", 'data-view="cross"')}${btn("拱顶特写", "view", "small", 'data-view="crown"')}${btn("仰拱特写", "view", "small", 'data-view="invert"')}${btn("剖切总览", "iso-cut", "small")}${btn("自动旋转", "rotate", "small")}${btn(evidenceActive()?"上一疑似点":"上一病害", "step-defect", "small", 'data-dir="-1"')}${btn(evidenceActive()?"下一疑似点":"下一病害", "step-defect", "small", 'data-dir="1"')}${btn("重置视角", "view", "small", 'data-view="iso"')}
    </div><div class="scene-tools">${check("shell", "管片衬砌")}${check("defect", "病害对象")}${check("rebar", "双层钢筋")}${check("grout", "注浆层")}${check("ground", "五层地层")}${check("profile", "断面偏差")}${check("car", "检测车 / 机械臂")}${check("ray", "雷达射线")}${check("coverage", "作业轨迹")}${check("risk", "风险热力")}${check("labels", "病害标签")}${check("labelsAll", "全部标注")}${check("hud", "车载雷达面板")}</div>
    <div class="scene-tools">${check("stars", "星空背景")}${check("city", "城市背景")}<label>衬砌透明度<input aria-label="衬砌透明度" id="opacity" type="range" min="0.05" max="0.8" step="0.05" value="${pref.opacity}"></label><label>纵向剖切<input aria-label="纵向剖切" id="cut" type="range" min="0" max="48" step="0.5" value="${pref.cut ?? 48}"></label><label class="background-upload">本地背景图片 <input type="file" id="backgroundImage" accept="image/png,image/jpeg,image/webp" aria-label="选择本地背景图片"></label>${btn("恢复默认背景", "restore-background", "small subtle")}</div>
     <div class="scene-shortcuts">自由漫游：W/A/S/D 移动，Q/E 升降，Shift 加速；第一人称：鼠标环视、空格切换扫掠，Esc 退出。暂停只冻结作业，相机仍可操作。图片仅在本地读取。</div></details>`;
  }
  function multiscenePanel() {
    const active =
      multisceneEntries.find((entry) => entry.id === activeMultiscene) ||
      multisceneEntries[0];
    return `<div id="image-review" class="image-review-anchor">${panel(
      "隧道影像三维复核",
      `<div class="review-context"><span class="badge mint">02 / 智能解析</span><span>病害候选识别后的影像、三维表面与同帧证据复核</span></div><div class="multiscene-switcher" role="tablist" aria-label="隧道影像三维复核场景">${multisceneEntries
        .map(
          (entry) =>
            `<button class="small ${entry.id === active.id ? "primary" : "subtle"}" data-action="multiscene-select" data-scene="${entry.id}" role="tab" aria-selected="${entry.id === active.id}">${entry.label}</button>`,
        )
        .join("")}</div><div class="multiscene-frame-wrap"><iframe id="multisceneFrame" title="${active.label}" src="${active.src}" loading="lazy"></iframe></div><p class="muted multiscene-note">该复核区属于智能解析模块，用于把识别候选与原始影像、三维表面和相邻帧证据对应起来；三维数字孪生模块继续负责工程空间定位、健康评估和运维决策。页面保留 algorithm/web/multiscene 中原有的场景切换、三维旋转、候选筛选、帧证据和录像回放，不覆盖原始文件。</p>`,
      btn("返回病害清单", "scroll-defects-top", "small subtle"),
      false,
    )}</div>`;
  }
  function post(data) {
    const f = $("#twinFrame");
    if (f && f.contentWindow)
      f.contentWindow.postMessage(
        Object.assign({ channel: "slzj" }, data),
        location.protocol === "file:" ? "*" : location.origin,
      );
  }
  function sceneMode(type) {
    post({ type });
    $("#twinFrame")?.contentWindow?.focus();
  }
  function evidenceContext(){const t=task();return {batchId:state.batch,taskId:t.id,start:t.start,end:t.end};}
  function evidenceActive(){return ["tasks","twin"].includes(route)&&window.TunnelEvidenceCore.mode(state,evidenceContext())==="evidence";}
  function radarCorrespondenceData(){const context=evidenceContext(),r=state.radars[selectedRadar];return route==="radar"&&window.TunnelEvidenceCore.mode(state,context)==="evidence"&&radarMode==="import"&&r?.batchId===context.batchId&&r.taskId===context.taskId?r:null;}
  function refreshEvidenceViews(){
    const needsTwinLayout=route==="twin"&&(evidenceActive()!==!!$("#evidenceUnfold"));
    if(route==="radar"||needsTwinLayout)render();else {window.TunnelEvidenceLink.refresh();drawCanvases();}
    syncScene();
    const badge=$("#taskSceneDataBadge");if(badge){badge.textContent=evidenceActive()?"02 / 同源疑似点 · 顺序位置对应":"02 / 仿真作业 · 演示";badge.className="badge "+(evidenceActive()?"mint":"amber");}
    if(evidenceActive())$("#dataBadge").textContent="现场标注 · 相对位置对应";
    $$('[data-action="step-defect"]').forEach(button=>{button.textContent=(button.dataset.dir==="-1"?"上一":"下一")+(evidenceActive()?"疑似点":"病害");});
    $$("[data-scene-data]").forEach(badge=>{badge.textContent=evidenceActive()?"疑似位置 · 仿真":"数字孪生 · 演示";});
    const count=$("#taskEvidenceCount");if(count)count.textContent=window.TunnelEvidenceCore.records(state,evidenceContext()).length+" 条记录";
  }
  function selectEvidence(id,align=false){const r=window.TunnelEvidenceCore.records(state,evidenceContext()).find(x=>x.id===id);if(!r)return;if(align&&route==="tasks"&&!r.anchors.some(a=>a.kind==="video"&&a.sourceId===window.TunnelVideoMonitor.evidenceIdentity().sourceId&&a.mappingRule==="relative-order-1.0"))window.TunnelEvidenceLink.setFollow(false);state.evidenceSelectedId=id;window.TunnelEvidenceCore.setMode(state,evidenceContext(),"evidence");if(align){if(taskRunning)toggleTask();const t=task();t.progress=(r.mileage-t.start)/(t.end-t.start);t.status="paused";refreshPlayback();if($("#taskPercent"))$("#taskPercent").textContent=fmt(t.progress*100)+"%";if($("#taskBar"))$("#taskBar").style.width=t.progress*100+"%";if($("#taskDistance"))$("#taskDistance").textContent="相对对齐位置 "+fmt(t.progress*(t.end-t.start))+" m";}save();syncScene();if(align)post({type:"select",id});window.TunnelEvidenceLink.refresh();}
  function syncScene() {
    const a = C.assess(state);
    post({
      type: "sync",
      defects: evidenceActive() ? window.TunnelEvidenceCore.scene(state, evidenceContext(), {positionOnly:route==="tasks"}) : C.getDefects(state),
      selectedId: evidenceActive() ? state.evidenceSelectedId : state.selectedId,
      evidenceMode: evidenceActive(),
      shi: a.shi,
      risk: a.risk,
      batch: state.batch,
    });
    syncTask();
    Object.entries(state.scenePrefs).forEach(([name, visible]) => {
      if (typeof visible === "boolean") post({ type: "layer", name, visible });
    });
    post({ type: "opacity", value: state.scenePrefs.opacity });
    post({ type: "cut", value: state.scenePrefs.cut });
    if (state.scenePrefs.backgroundImage)
      post({ type: "background", url: state.scenePrefs.backgroundImage });
  }
  function taskParams() {
    const t = task();
    return {
      start: t.start - state.project.start,
      end: t.end - state.project.start,
      speed: t.speed,
      spacing: t.spacing,
      scanStart: t.scanStart ?? -90,
      scanEnd: t.scanEnd ?? 90,
      obstacle: t.obstacle
        ? { x: t.obstacleX ?? 24, z: 0, width: 1, length: 1 }
        : null,
    };
  }
  function syncTask() {
    const t = task(),
      p = taskParams(),
      r = C.simulateVehicle(p),
      x = p.start + (p.end - p.start) * (t.progress || 0);
    post({
      type: "task",
      progress: x / 48,
      playing: taskRunning,
      speed: t.speed,
      start: p.start,
      end: p.end,
      obstacle: p.obstacle,
      stopAt: r.stopAt,
      scanStart: p.scanStart,
      scanEnd: p.scanEnd,
    });
  }
  function table(list, compact = false) {
    if (!list.length)
      return '<div class="empty"><b>没有符合条件的病害</b>调整筛选条件或重置筛选。</div>';
    return `<div class="table-wrap"><table><thead><tr><th>病害编号 / 类型</th><th>里程 / 环号</th><th>风险</th>${compact ? "" : "<th>直径 / 埋深 (m)</th><th>置信度</th><th>复核</th>"}<th>操作</th></tr></thead><tbody>${list.map((d) => `<tr class="${d.id === state.selectedId ? "selected" : ""}"><td><strong>${d.id}</strong><br><span class="muted">${C.TYPES[d.type].name}</span></td><td>${mile(d.mileage)}<br><span class="muted">${d.ring} 环 · ${d.angle}°</span></td><td>${risk(d.risk)}</td>${compact ? "" : `<td>${fmt(d.diameter, 2)} / ${fmt(d.depth, 2)}</td><td>${fmt(d.confidence * 100, 0)}%</td><td>${REVIEW[d.review]}</td>`}<td>${btn("定位", "select", "small", `data-id="${d.id}"`)} ${compact ? "" : btn("证据", "evidence", "small subtle", `data-id="${d.id}"`)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function detail() {
    const d = chosen();
    if (!d || !C.getDefects(state).some((x) => x.id === d.id))
      return note("当前筛选没有选中对象，请调整筛选或重置。");
    return panel(
      "病害详情",
      `<span class="badge">${d.lineId} · 演示台账</span><div class="detail-id">${d.id} <span style="font-size:12px">${risk(d.risk)}</span></div><div class="detail-type">${C.TYPES[d.type].name} · ${REVIEW[d.review]}</div><dl class="dl-grid"><div><dt>隧道里程</dt><dd>${mile(d.mileage)}</dd></div><div><dt>环号 / 环向</dt><dd>${d.ring} / ${d.angle}°</dd></div><div><dt>等效直径 / 长度</dt><dd>${fmt(d.diameter, 2)} / ${fmt(d.length, 2)} m</dd></div><div><dt>径向埋深</dt><dd>${fmt(d.depth, 2)} m</dd></div><div><dt>模型置信度（演示）</dt><dd>${fmt(d.confidence * 100, 0)}%</dd></div><div><dt>健康评分</dt><dd>${fmt(d.score)} / 100</dd></div></dl><div class="split-heading">三维坐标 / m</div><div class="code">X ${fmt(d.position.x, 2)} · Y ${fmt(d.position.y, 2)} · Z ${fmt(d.position.z, 2)}</div><div class="h-divider"></div><div class="radar-wrap"><canvas id="detailRadar" aria-label="选中病害示意雷达证据" style="height:120px"></canvas></div><div class="radar-caption">${d.evidenceId} · 程序示意证据</div>${note("尺寸来自演示台账。置信度不是诊断可靠性；建议专项复测并由专业人员复核。", "info")}<div class="detail-actions">${btn("雷达证据", "evidence", "small", `data-id="${d.id}"`)}${btn("已复核", "review", "small", `data-review="confirmed"`)}${btn("驳回", "review", "small subtle", `data-review="rejected"`)}${btn("待复核", "review", "small subtle", `data-review="pending"`)}${btn("加入治理", "create-maintenance", "small primary")}</div>`,
    );
  }
  // 与三维共享 Y/Z 坐标的环位横断面；0° 拱顶、90° 右墙。
  function ringSection() {
    const d = chosen();
    if (!d || !C.getDefects(state).some((x) => x.id === d.id)) return "";
    const r = state.project.radius,
      thickness = state.project.thickness,
      scale = 88 / (r + Math.max(thickness, d.depth) + 0.2),
      cx = 150,
      cy = 128,
      a = (d.angle * Math.PI) / 180,
      px = cx + d.position.z * scale,
      py = cy - d.position.y * scale,
      ix = cx + r * Math.sin(a) * scale,
      iy = cy - r * Math.cos(a) * scale,
      arcX = cx + 29 * Math.sin(a),
      arcY = cy - 29 * Math.cos(a);
    return panel(
      "选中病害环位横断面",
      `<svg id="ringSection" class="ring-section" viewBox="0 0 300 256" role="img" aria-label="${d.id} 第${d.ring}环横断面，环向${d.angle}度，埋深${d.depth}米" data-defect-id="${d.id}" data-angle="${d.angle}" data-depth="${d.depth}">
      <circle cx="${cx}" cy="${cy}" r="${(r + thickness) * scale}" fill="#253749" stroke="#7293aa"/><circle cx="${cx}" cy="${cy}" r="${r * scale}" fill="#0b1723" stroke="#86adb9"/>
      <path d="M150 24V232 M38 128H262" stroke="#426074" stroke-dasharray="3 5"/>
      <text x="150" y="16" text-anchor="middle">拱顶 0° · +Y</text><text x="260" y="118" text-anchor="end">90° · +Z</text><text x="40" y="118">270°</text><text x="150" y="249" text-anchor="middle">仰拱 180°</text>
      <path d="M150 99 A29 29 0 ${d.angle > 180 ? 1 : 0} 1 ${arcX} ${arcY}" fill="none" stroke="#efb65d"/>
      <line x1="${cx}" y1="${cy}" x2="${px}" y2="${py}" stroke="#56d9b1" stroke-dasharray="4 3"/><line x1="${ix}" y1="${iy}" x2="${px}" y2="${py}" stroke="#efb65d" stroke-width="4"/>
      <circle class="ring-defect-marker" cx="${px}" cy="${py}" r="5" fill="#ff787f" stroke="#fff" data-y="${d.position.y}" data-z="${d.position.z}"/>
      <text x="150" y="153" text-anchor="middle" class="ring-id">${d.id}</text>
    </svg><div class="ring-section-caption">第 ${d.ring} 环 · ${mile(d.mileage)}<br>环向 ${d.angle}° · 径向埋深 ${fmt(d.depth, 3)} m</div><p class="muted" style="font-size:10px">圆环为内表面与衬砌外缘；黄色线段标注从内表面至病害中心的径向埋深，红点为位置示意。Y/Z 与三维坐标一致，病害点大小不表示实际尺寸。</p>`,
    );
  }
  function healthHistory() {
    const points = state.batches
      .map((batch) => ({
        ...batch,
        time: Date.parse(batch.date + "T00:00:00Z"),
        shi: C.assess({ ...state, batch: batch.id }).shi,
      }))
      .sort((a, b) => a.time - b.time);
    if (points.length < 2)
      return note("至少需要两期检测批次才能计算 SHI 变化率。");
    const first = points[0],
      last = points.at(-1),
      days = (last.time - first.time) / 86400000,
      delta = last.shi - first.shi,
      rate = days > 0 ? (delta / days) * 365 : null,
      x = (p) =>
        70 +
        (days > 0 ? (p.time - first.time) / (last.time - first.time) : 0) * 200,
      y = (p) => 143 - p.shi * 1.13;
    return `<div class="shi-history"><div class="split-heading">全隧道 SHI 历史曲线</div><svg id="shiHistory" class="shi-history-chart" viewBox="0 0 340 180" role="img" aria-label="同一评估配置下的全隧道SHI历史曲线">
      ${[0, 25, 50, 75, 100].map((v) => `<path d="M48 ${143 - v * 1.13}H292" stroke="#2c4053"/><text x="38" y="${147 - v * 1.13}" text-anchor="end">${v}</text>`).join("")}
      <polyline points="${points.map((p) => `${x(p)},${y(p)}`).join(" ")}" fill="none" stroke="#56d9b1" stroke-width="2.5"/>
      ${points.map((p) => `<g data-batch="${p.id}" data-shi="${p.shi}"><circle cx="${x(p)}" cy="${y(p)}" r="4" fill="#56d9b1"/><text x="${x(p)}" y="${y(p) - 10}" text-anchor="middle" class="shi-value">${fmt(p.shi, 2)}</text><text x="${x(p)}" y="165" text-anchor="middle">${p.date}</text></g>`).join("")}
      </svg><div id="shiHistoryRate" class="history-rate" data-days="${days}" data-delta="${delta}" data-rate="${rate ?? ""}"><span>${fmt(days, 0)} 天变化 <b>${delta >= 0 ? "+" : ""}${fmt(delta, 3)} 分</b></span><span>年化变化率 <b>${rate === null ? "无法计算" : (rate >= 0 ? "+" : "") + fmt(rate, 3) + " 分/年"}</b></span></div><p class="muted" style="font-size:10px">R = (末期 SHI − 首期 SHI) / 相隔天数 × 365；负值表示评分下降。两期均采用当前 AHP、组合系数与阈值，并保留各批次复核状态。演示历史由尺寸比例派生；年化仅为区间变化率，不作趋势预测。</p></div>`;
  }
  function gauge(a) {
    return `<div class="gauge"><svg viewBox="0 0 200 145"><path d="M25 118 A83 83 0 1 1 175 118" fill="none" stroke="#293a4b" stroke-width="11" stroke-linecap="round"/><path d="M25 118 A83 83 0 1 1 175 118" fill="none" stroke="#56d9b1" stroke-width="11" stroke-linecap="round" pathLength="100" stroke-dasharray="${a.shi} 100"/></svg><div class="gauge-number">${fmt(a.shi)}<small>SHI / 100 · 越高越健康</small></div></div>`;
  }
  function timeline() {
    return state.logs.length
      ? `<ol class="timeline">${state.logs
          .slice(0, 5)
          .map(
            (l) =>
              `<li><time>${new Date(l.time).toLocaleString("zh-CN")}</time><p>${escape(l.message)}</p></li>`,
          )
          .join("")}</ol>`
      : '<div class="empty">开始检测或处理预警后，操作记录会显示在这里。</div>';
  }
  function overview() {
    const a = C.assess(state),
      t = task(),
      ads = currentAlerts().filter((x) => x.status !== "closed"),
      ds = all();
    return (
      heading(
        "项目总览",
        "从隧道现场到运维决策，让每一处异常都有可追溯的证据。",
        "WORKSPACE / PROJECT OVERVIEW",
        btn("完整演示流程", "guide", "subtle") +
          btn("进入检测任务", "go-tasks", "primary"),
      ) +
      `<div class="metrics">${metric("本次检测进度", fmt(t.progress * 100, 0), "%", fmt((t.end - t.start) * t.progress) + " m / " + (t.end - t.start) + " m · " + batchTasks().length + " 个任务", "tasks", "accent")}${metric("病害记录", ds.filter((d) => d.review !== "rejected").length, "处", ds.filter((d) => d.review === "pending").length + " 处待复核 · 同源演示台账", "defects")}${metric("结构健康指数", fmt(a.shi), "/ 100", "全隧道评估 · " + a.segments.length + " 个区段", "health")}${metric("待处置预警", ads.length, "条", "最高等级 " + a.risk + " · 局部严重单独触发", "alerts", "amber")}</div><div class="grid-main"><div>${panel("隧道数字孪生", scene(), '<span class="badge">' + state.batches.find((b) => b.id === state.batch).name + "</span>", false)}${panel("重点病害", table(ds.filter((d) => d.risk === "I").slice(0, 3), true), '<a href="#defects" class="caption">查看完整清单 ↗</a>', false)}</div><aside>${panel(
        "按计划书组织的业务模块",
        groups
          .slice(1)
          .map(
            (g, i) =>
              `<div class="module-card"><span class="num">PLAN ${String(i + 1).padStart(2, "0")}</span><b>${g.title.slice(5)}</b><p>${["检测车作业 · 雷达数据采集", "预处理 · 模型接口 · 病害复核", "三维定位 · 健康评估 · 治理闭环", "样本 · 避障 · 结构 · 方案比较"][i]}</p><a href="#${g.items[0][0]}">进入模块 →</a></div>`,
          )
          .join(""),
      )}${panel("项目档案", `<div class="mini-stat"><small>断面 / 内径</small><span>圆形 / 5.40 m</span></div><div class="mini-stat"><small>衬砌厚度</small><span>0.35 m</span></div><div class="mini-stat"><small>里程范围</small><span>K3+128 — K3+176</span></div><div class="mini-stat"><small>数据模式</small><span class="badge mint">可复现演示</span></div>`)}</aside></div><div class="grid-2">${panel("风险区段分布", segments(a), '<a href="#health" class="caption">评估依据 ↗</a>')}${panel("近期操作", timeline())}</div>`
    );
  }
  function segments(a) {
    return `<div class="segment-strip">${a.segments.map((s) => `<button class="risk-${s.risk}" data-action="segment" data-start="${s.start}" data-end="${s.end}" title="${mile(s.start)} 至 ${mile(s.end)}，SHI ${fmt(s.shi)}">${s.risk}</button>`).join("")}</div><div class="seg-labels"><span>K3+128</span><span>沿隧道轴线方向 →</span><span>K3+176</span></div><div class="legend">${C.RISKS.map(risk).join("")}</div><p class="muted" style="font-size:10px">区段采用有效病害最低分；全隧道按区段长度加权。无入库异常区段的 100 分仅为演示假设。</p>`;
  }
  function tasksPage() {
    const t = task(),
      v = C.simulateVehicle(taskParams());
    const evidenceTotal = window.TunnelEvidenceCore.records(state, evidenceContext()).length;
    return (
      '<div class="task-page-layout">' + heading(
        "检测任务与设备",
        "左侧看现场视频，右侧核对仿真；在同一工作区记录疑似位置、操作车辆与核对证据。",
        "01 / 对应计划第 1 部分",
        btn("新建检测任务", "new-task", "primary"),
      ) +
      moduleTabs() +
      `<details class="task-drawer task-equipment-drawer" id="taskEquipmentDrawer" ${taskPanels.equipment ? "open" : ""}><summary class="task-drawer-summary"><span class="task-drawer-icon">${icon("tasks")}</span><span><b>设备配置与项目示意</b><small>检测车 · 雷达 · 影像 · 定位 · 边缘终端 · 安全保障</small></span><span class="badge mint">6 类设备</span></summary><div class="task-drawer-body">${equipmentPanel()}</div></details><section class="task-workspace" aria-label="现场视频与仿真左右对照"><div class="task-workspace-head"><div><span class="task-workspace-kicker">LIVE VIDEO / DIGITAL TWIN</span><h2>现场与仿真 · 同屏对照</h2><p>${escape(t.name)}<span> / </span>${mile(t.start)} — ${mile(t.end)}</p></div><div class="task-workspace-actions">${btn("冻结视频并标记", "task-capture-video", "primary", 'disabled id="taskCaptureVideo"')}${btn("疑似位置与对应", "task-evidence", "subtle")}${btn("作业参数", "task-parameters", "subtle")}</div></div><div id="taskPositionReviewMount"></div><div class="task-comparison-grid" id="taskComparison"><div class="task-video-pane"><div id="liveMonitorMount"></div><details class="task-drawer task-evidence-drawer" id="taskEvidenceDrawer" ${taskPanels.evidence || state.pendingVideoEvidence ? "open" : ""}><summary class="task-drawer-summary"><span class="task-drawer-icon">${icon("defects")}</span><span><b>疑似位置与对应</b><small>冻结原帧、记录疑似位置与顺序、查看对应证据</small></span><span class="badge mint" id="taskEvidenceCount">${evidenceTotal} 条记录</span></summary><div class="task-drawer-body"><div id="evidenceMount"></div></div></details></div><aside class="task-simulation-pane" aria-label="对应仿真作业场景">${panel("检测车作业场景 · 仿真演示", scene(true) + `<div class="panel-body"><div class="progress-label"><span id="taskStatus">${escape(t.name)} · ${statusTask(t.status)}</span><span id="taskPercent">${fmt(t.progress * 100)}%</span></div><div class="progress"><span id="taskBar" style="width:${t.progress * 100}%"></span></div><div class="actions">${btn(taskRunning ? "暂停作业" : "开始 / 继续", "toggle-task", "primary")}${btn("重置进度", "reset-task", "subtle")}<span class="muted" id="taskDistance">已行驶 ${fmt(t.progress * (t.end - t.start))} m</span><a href="#radar" style="margin-left:auto">查看雷达数据 →</a></div></div>`, evidenceActive()?'<span class="badge mint" id="taskSceneDataBadge">02 / 同源疑似点 · 顺序位置对应</span>':'<span class="badge amber" id="taskSceneDataBadge">02 / 仿真作业 · 演示</span>', false)}</aside></div></section><div class="grid-main task-configuration" id="taskConfiguration"><div>${panel(
        "任务列表",
        `<div class="table-wrap"><table><thead><tr><th>任务</th><th>检测范围</th><th>速度</th><th>进度</th><th>状态</th><th>操作</th></tr></thead><tbody>${batchTasks()
          .map(
            (x) =>
              `<tr data-task-row="${escape(x.id)}"><td>${escape(x.name)}</td><td>${mile(x.start)} — ${mile(x.end)}</td><td>${x.speed} m/s</td><td data-task-progress>${fmt(x.progress * 100)}%</td><td data-task-status>${statusTask(x.status)}</td><td>${btn("选择", "choose-task", "small", `data-id="${x.id}"`)}</td></tr>`,
          )
          .join("")}</tbody></table></div>`,
        "",
        false,
      )}</div><aside>${panel("作业参数", `<form id="taskForm"><div class="form-grid">${field("起点里程 / m", "taskStart", t.start, 3128, 3175.9, 0.1)}${field("终点里程 / m", "taskEnd", t.end, 3128.1, 3176, 0.1)}${field("行驶速度 / m·s⁻¹", "taskSpeed", t.speed, 0.05, 5, 0.05)}${field("扫描间距 / m", "taskSpacing", t.spacing, 0.01, 2, 0.01)}${field("扫描起角 / °", "scanStart", t.scanStart ?? -90, -180, 179, 1)}${field("扫描止角 / °", "scanEnd", t.scanEnd ?? 90, -179, 180, 1)}${field("障碍位置 / 相对起点 m", "obstacleX", t.obstacleX ?? 24, 2, 46, 0.5)}<label class="field task-obstacle-field"><span><input id="taskObstacle" type="checkbox" ${t.obstacle ? "checked" : ""}> 设置中心线障碍物</span></label></div><div class="actions"><button type="submit" class="primary">应用参数</button></div></form><div class="h-divider"></div><div class="task-parameter-metrics"><div class="mini-stat"><small>车体尺寸 / m</small><span>2.4 × 1.4</span></div><div class="mini-stat"><small>扫描有效幅宽</small><span>0.12 m</span></div><div class="mini-stat"><small>预计作业用时</small><span>${fmt(v.duration)} s</span></div><div class="mini-stat"><small>预计范围覆盖率</small><span>${fmt(v.coverage)}%</span></div><div class="mini-stat"><small>碰撞检查</small><span>${v.collision ? "初始重叠，禁止作业" : v.stopped ? "预计提前停障" : "路径通畅"}</span></div></div>${v.stopped ? note("车辆中心在相对起点 " + fmt(v.stopAt) + " m 提前停止，安全距离 0.3 m。", "warning") : ""}<p class="muted" style="font-size:10px">覆盖率仅对应所选环向范围。三臂动作是运动学演示，未验证硬件控制和动态碰撞。</p>`)}</aside></div>${panel("作业日志", `<div id="taskLog">${timeline()}</div>`)}</div>`
    );
  }
  function statusTask(s) {
    return (
      {
        ready: "待开始",
        running: "作业中",
        paused: "已暂停",
        completed: "已完成",
        blocked: "停障等待",
      }[s] || s
    );
  }
  function demoMatrix(d = chosen()) {
    return C.generateRadar({
      seed: 20260928 + Number(d.id.slice(2)),
      depth: d.depth,
      diameter: d.diameter,
      noise: 0.12,
      epsilon: 6,
      voidPosition: 0.55,
    });
  }
  function radarDemoData() {
    const d = chosen();
    return {
      id: "demo-" + d.id, name: d.lineId + " · " + d.id + " 示意证据",
      matrix: demoMatrix(d), rows: 96, cols: 160, source: "demo", metadata: {},
      warnings: ["程序生成的示意信号；不用于尺寸反演或算法准确率评估。"],
      axes: { x: "道号", y: "采样点" },
    };
  }
  function radarData() {
    if (radarMode === "import" && state.radars[selectedRadar]?.batchId === state.batch)
      return state.radars[selectedRadar];
    return radarDemoData();
  }
  function radarOptions() {
    return `<label>当前数据 <select id="radarSource" aria-label="选择雷达数据"><option value="demo" ${radarMode === "demo" ? "selected" : ""}>内置示意 · ${chosen().lineId} / ${chosen().id}</option>${state.radars.map((r, i) => (r.batchId !== state.batch ? "" : `<option value="${i}" ${radarMode === "import" && selectedRadar === i ? "selected" : ""}>外部快照 · ${escape(r.name)}</option>`)).join("")}</select></label>`;
  }
  function radarControls(demoOnly = false) {
    const r = demoOnly ? radarDemoData() : radarData();
    trace = Math.min(trace, r.cols - 1);
    return `<div class="filter-bar">${demoOnly ? "" : radarOptions()}<label>色标增益<select id="palette"><option value="1" ${palette === 1 ? "selected" : ""}>标准 ×1</option><option value="2" ${palette === 2 ? "selected" : ""}>增强 ×2</option><option value="0.5" ${palette === 0.5 ? "selected" : ""}>柔和 ×0.5</option></select></label><label>横向缩放<select id="radarZoom"><option value="1" ${zoom === 1 ? "selected" : ""}>1 倍</option><option value="2" ${zoom === 2 ? "selected" : ""}>2 倍</option><option value="4" ${zoom === 4 ? "selected" : ""}>4 倍</option></select></label><label>单道序号<input type="number" id="trace" value="${trace}" min="0" max="${r.cols - 1}" step="1" aria-label="单道序号"></label>${r.source === "demo" ? `<label><span><input type="checkbox" id="roiOverlay" ${overlayRegions ? "checked" : ""}> 示意异常区域</span></label>` : ""}</div>`;
  }
  function radarContext() {
    const current = task();
    return {batchId:state.batch, batchName:state.batches.find(b=>b.id===state.batch)?.name || state.batch,
      taskId:current.id, taskName:current.name, lineId:current.lineId};
  }
  function radarTransportLabel(r) {
    return ({"http":"HTTP 网关帧","websocket":"WebSocket 网关帧","file-json":"本地 JSON","file-csv":"本地 CSV"})[r.provenance?.transport] || "本地 CSV";
  }
  function radarDirectory() {
    const entries=state.radars.map((r,i)=>({r,i})).filter(({r})=>r.batchId===state.batch);
    if(!entries.length)return '<div class="empty">当前批次没有外部数据；导入文件或保存接入帧后在这里选用。</div>';
    return `<div class="table-wrap"><table><thead><tr><th>数据 / 任务 / 测线</th><th>接入方式与来源声明</th><th>矩阵 / 标定</th><th>操作</th></tr></thead><tbody>${entries.map(({r,i})=>`<tr><td>${escape(r.name)}<br><small class="muted">${escape(r.taskId || "未关联任务")} / ${escape(r.lineId || r.metadata?.lineId || "未提供测线")}</small></td><td>${radarTransportLabel(r)}<br><small class="muted">${escape(r.provenance?.declaredSource || r.metadata?.source || "真实性未核验")}</small></td><td>${r.rows} × ${r.cols}<br><small class="muted">${r.warnings.length ? "有来源或标定提示" : "标定字段完整（未核验）"}</small></td><td>${btn("选为处理输入", "use-radar", "small", `data-id="${i}"`)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function radarPage() {
    const r=radarCorrespondenceData() || radarDemoData();
    return heading("雷达数据管理", "先接入外部原始矩阵与网关数据帧，再对照独立仿真示例；保存的快照可送入智能处理。", "01 / 对应计划第 1 部分",
      btn("下载 CSV 样例", "sample-csv", "subtle")+btn("进入智能处理", "go-processing", "primary")) + moduleTabs() +
      `<div id="radarAcquisitionMount"></div><div id="evidenceMount"></div><div id="radarSimulation">${panel(radarCorrespondenceData()?"雷达信号 · 同源病害对应复核":"雷达信号 · 仿真演示", radarControls(true)+`<div class="radar-wrap"><canvas id="rawRadar" aria-label="仿真B-scan雷达图"></canvas></div><div class="radar-caption"><span>${escape(r.name)}</span><span>${radarCorrespondenceData()?"同源固定矩阵 / 人工病害定位":"程序示意信号"} · ${r.rows} 点 × ${r.cols} 道</span></div><div class="radar-wrap wave"><canvas id="wave" aria-label="仿真单道波形"></canvas></div><div class="radar-caption"><span>单道 #<span id="traceNumber">${trace}</span> · 示意归一化振幅</span><span>点击剖面可切换单道</span></div>${note(r.warnings.join(" "), "info")}<div class="table-wrap"><table><thead><tr><th>数据</th><th>来源</th><th>矩阵</th><th>状态</th></tr></thead><tbody><tr><td>${radarCorrespondenceData()?escape(r.name):"L-01 / L-02 / L-03"}</td><td>${radarCorrespondenceData()?"同源固定快照 / 现场人工标注":"病害参数派生示意"}</td><td>${r.rows} × ${r.cols}</td><td>${radarCorrespondenceData()?"位置对应复核（非雷达物理求解）":"可预览"}</td></tr></tbody></table></div>`, radarCorrespondenceData()?'<span class="badge mint">02 / 同一快照 · 对应示意</span>':'<span class="badge amber">02 / 仿真数据 · 演示</span>')}</div>`;
  }
  function processingPage() {
    const r = radarData(),
      p = state.processing,
      ready = processed && processed.input === r.id;
    return (
      heading(
        "智能处理工作流",
        "沿计划第 2 部分组织计算步骤，输入、参数、输出和算法版本全程可查。",
        "02 / 对应计划第 2 部分",
        btn("运行数值预处理", "run-preprocess", "primary") +
          btn("查看病害台账", "go-defects", "subtle") +
          btn("进入影像三维复核", "go-image-review", "subtle"),
      ) +
      moduleTabs() +
      panel(
        "当前影像检测算法",
        `<div id="currentImageAlgorithm"><div class="grid-2"><div><h3>crack-seg U-Net · 裂缝分割</h3><p class="muted">输入为原始影像帧，输出为裂缝掩码与候选观测。由 algorithm/defect_detect.py 独立运行；现有候选可继续在隧道影像三维复核中查看。</p></div><div>${note("当前检测范围为裂缝候选，须人工复核。视频接入不会自动启动 Python 算法，算法结果尚未自动写入工作台病害记录。", "info")}</div></div><div class="actions">${btn("查看影像检测与三维复核", "go-image-review", "primary")}<a class="btn subtle" href="algorithm/README.md" target="_blank" rel="noopener">查看当前算法说明 ↗</a></div></div>`,
        '<span class="badge mint">独立影像算法</span>',
      ) +
      radarControls() +
      `<div class="flow">${[
        ["原始数据", r.source === "import" ? radarTransportLabel(r) + " · 外部快照" : "内置示意", "已加载"],
        ["数值预处理", "减背景 + 时间增益", ready ? "已完成" : "可运行"],
        ["RCAN 杂波抑制", "残差通道注意网络", "待接入"],
        ["RTM 逆时偏移", "全波方程成像接口", "待接入"],
      ]
        .map(
          (s, i) =>
            `<div class="flow-step ${i === 0 || (i === 1 && ready) ? "done" : ""}"><span class="step-num">STEP 0${i + 1}</span><b>${s[0]}</b><small>${s[1]}</small><span class="badge ${i === 0 || (i === 1 && ready) ? "mint" : ""}">${s[2]}</span></div>`,
        )
        .join(
          "",
        )}</div><div class="grid-3">${panel("原始输入", `<div class="radar-wrap"><canvas id="rawRadar" aria-label="原始雷达对比图"></canvas></div><div class="radar-caption">${escape(r.name)} · ${r.rows} × ${r.cols}</div>`)}${panel("实际数值预处理", ready ? `<div class="radar-wrap"><canvas id="processedRadar" aria-label="数值预处理对比图"></canvas></div><div class="radar-caption">${fmt(p.durationMs, 2)} ms · ${p.version}</div>` : '<div class="empty"><b>等待运行</b>对当前输入矩阵减去逐行均值，再施加线性时间增益。</div>')}${panel("RTM 成像输出", '<div class="empty"><b>尚未连接 RTM 求解器</b>连接经验证的求解器后才显示成像结果。</div>' + btn("查看算法接入契约", "adapter", "small"))}</div><div class="grid-2">${panel("处理参数", `<div class="form-grid">${field("线性时间增益 g", "gain", p.gain ?? 1, 0, 20, 0.1)}<label class="field">背景去除<span><input type="checkbox" id="background" ${p.background === false ? "" : "checked"}> 逐采样点减去道均值</span></label><label class="field">预处理版本<input value="background-gain-1.0" readonly></label></div><p class="code">y[r,c] = (x[r,c] − mean(x[r,:])) × (1 + g·r/(N−1))</p>${note("真实预处理不会自动生成病害。16 条病害是沿用的演示台账；RCAN 与 RTM 雷达接口尚未接入。影像裂缝检测由上方独立算法流程完成。", "warning")}<div class="actions">${btn("运行 / 重试", "run-preprocess", "primary")}${btn("取消处理", "cancel-processing", "subtle")}${btn("检查 RTM 接口", "try-model", "small")}</div>`)}${panel("RTM 参数与运行记录", `<div class="form-grid">${field("相对介电常数 εr", "rtmEpsilon", p.epsilon || 6, 1, 100, 0.1)}${field("网格步长 / m", "rtmDx", p.dx || 0.01, 0.0001, 1, 0.001)}${field("时间步长 / ns", "rtmDt", p.dt || 0.02, 0.0001, 10, 0.001)}<label class="field">边界条件<select id="rtmBoundary"><option>CPML（待求解器实现）</option></select></label></div><div class="actions">${btn("保存并校核参数", "save-rtm", "small")}</div><p class="muted" style="font-size:11px">二维等距网格参考稳定性诊断：Δt ≤ Δx /(v√2)，v=0.299792458/√εr m/ns。实际限制取决于离散格式与求解器。</p><div class="note info">状态：${escape(p.status || "idle")}<br>输入：${escape(p.inputName || "未运行")}<br>处理时间：${escape(p.finishedAt || "—")}<br>最近提示：${escape(p.message || "等待输入")}</div>`)}</div>`
    );
  }
  function defectsPage() {
    return (
      heading(
        "病害清单与复核",
        "病害列表、雷达证据与三维对象共用同一个编号和坐标体系。",
        "02 / 对应计划第 2 部分",
        btn("导出当前清单 CSV", "export-csv", "primary"),
      ) +
      moduleTabs() +
      filters() +
      `<div class="grid-main"><div>${panel("病害记录 · " + C.getDefects(state).length + " 处", table(C.getDefects(state)), '<span class="badge">演示台账</span>', false)}${note("空洞、富水、裂缝等类型来自原平台演示台账，不表示已通过当前雷达输入识别。物理尺寸为台账设定值。", "info")}</div><aside>${detail()}</aside></div>${multiscenePanel()}`
    );
  }
  function twinPage() {
    if (evidenceActive()) return heading("三维数字孪生", "现场疑似点与视频 / 雷达共用编号、先后顺序和位置；未识别点使用统一标记，已有分类保留。", "03 / 对应计划第 3 部分", btn("返回影像三维复核", "go-image-review", "subtle")) + moduleTabs() + '<div id="evidenceMount"></div>' + `<div class="grid-main evidence-twin"><div>${panel("现场疑似位置空间对应", scene(true,true), '<span class="badge mint">当前任务现场标注 · 未作风险鉴定</span>',false)}${panel("现场疑似位置环向展开",'<canvas id="evidenceUnfold" aria-label="现场病害里程与环向位置"></canvas><p class="muted">点选标记可按同一坐标定位并对齐检测车。</p>')}</div><aside>${panel("所选现场疑似位置环位",'<canvas id="evidenceSection" aria-label="现场病害横断面位置"></canvas>')}${panel("对应规则",'<p>录像时间进度对应任务区间，画面横向对应指定环位；雷达道序对应里程，采样序对应相对深度。人工位置与已校核测线标定也可填写。</p><p class="muted">相对映射保持先后順序，不等于实测定位。标注不自动改变演示 SHI、预警或历史派生值。切回“原有演示台账”可使用原历史评估。</p>')}</aside></div>`;
    return (
      heading(
        "三维数字孪生",
        "把病害、检测作业、风险区段和雷达证据定位到同一座隧道；影像候选复核位于 02 / 智能解析。",
        "03 / 对应计划第 3 部分",
        btn("返回影像三维复核", "go-image-review", "subtle"),
      ) +
      moduleTabs() +
      '<div id="evidenceMount"></div>' +
      filters() +
       `<div class="grid-main"><div>${panel("隧道空间定位", scene(true, true), '<span class="badge">X 轴沿里程前进</span>', false)}${panel("环向展开图", `<canvas id="unfold" class="unfold" aria-label="隧道病害环向展开图"></canvas><p class="muted" style="font-size:10px">横轴：里程；纵轴：环向角，0° 拱顶、90° 右墙。点击标记联动三维与雷达。</p>`)}${panel("历史批次变化", `<div id="historyComparison">${historyTable()}</div>`)}</div><aside>${detail()}${ringSection()}${panel("坐标定义", `<p class="code">X = 里程 − 3128 m<br>Y = (2.7 + 埋深) × cos θ<br>Z = (2.7 + 埋深) × sin θ</p><p class="muted" style="font-size:11px">病害位于管片环中心；X/Y/Z 单位 m。径向深度从衬砌内表面向外。历史批次按演示比例派生，不代表历史实测。</p>`)}</aside></div>`
    );
  }
  function historyTable() {
    const d = chosen(),
      latest = C.getDefects(Object.assign({}, state, { batch: "B202609" }), {
        filtered: false,
      }).find((x) => x.id === d.id),
      old = C.getDefects(Object.assign({}, state, { batch: "B202607" }), {
        filtered: false,
      }).find((x) => x.id === d.id);
    return `<div class="table-wrap"><table><thead><tr><th>${d.id}</th><th>2026 年 7 月</th><th>2026 年 9 月</th><th>变化</th></tr></thead><tbody><tr><td>等效直径 / m</td><td>${fmt(old.diameter, 3)}</td><td>${fmt(latest.diameter, 3)}</td><td>+${fmt(latest.diameter - old.diameter, 3)}</td></tr><tr><td>健康评分</td><td>${fmt(old.score)}</td><td>${fmt(latest.score)}</td><td>${fmt(latest.score - old.score)}</td></tr></tbody></table></div><p class="muted" style="font-size:10px">历史直径和长度 = 当前 × 0.8，面积 = 当前 × 0.64；两期使用同一评估配置。用于对比操作演示。</p>${healthHistory()}`;
  }
  function healthPage() {
    const a = C.assess(state);
    return (
      heading(
        "结构健康评估",
        "病害量化 → 指标归一化 → AHP / 熵权 → 组合赋权 → SHI → 风险分级。",
        "03 / 对应计划第 3 部分",
        btn("生成评估报告", "go-reports", "primary"),
      ) +
      moduleTabs() +
      `<div class="grid-main"><div><div class="grid-2">${panel("全隧道结构健康指数", gauge(a) + `<div style="text-align:center">${risk(a.risk)} <span class="muted" style="font-size:10px">全局保留局部最高风险</span></div>`)}${panel("组合权重", a.weights.map((w, i) => `<div class="weight-label"><span>${["病害类型", "几何尺寸", "浅埋程度", "环向位置"][i]}</span><span>${fmt(w * 100)}%</span></div><div class="weight-bar"><span style="width:${w * 100}%"></span></div>`).join(""), '<span class="badge mint">α = ' + fmt(state.alpha, 2) + "</span>")}</div>${panel("区段评估", segments(a) + `<div class="table-wrap"><table><thead><tr><th>区段里程</th><th>病害数</th><th>SHI</th><th>等级</th></tr></thead><tbody>${a.segments.map((s) => `<tr><td>${mile(s.start)} — ${mile(s.end)}</td><td>${s.count}</td><td>${fmt(s.shi)}</td><td>${risk(s.risk)}</td></tr>`).join("")}</tbody></table></div>`)}${panel("指标、赋权与聚合依据", `<div class="table-wrap"><table><thead><tr><th>指标</th><th>严重度 x ∈ [0,1]，均为负向</th><th>AHP 权重</th><th>熵权</th></tr></thead><tbody>${["类型严重度：预设 0.45～1", "尺寸：0.6 min(d/1.3,1) + 0.4 min(L/2.4,1)", "浅埋：1 − min(h/0.6,1)", "位置：0.35 + 0.65 max(cos θ,0)"].map((t, i) => `<tr><td>${["类型", "尺寸", "浅埋", "位置"][i]}</td><td>${t}</td><td>${fmt(a.ahpWeights[i], 3)}</td><td>${fmt(a.entropyWeights[i], 3)}</td></tr>`).join("")}</tbody></table></div><p class="code">w = α·wAHP + (1−α)·w熵；病害分值 = 100·(1 − Σwj xj)</p><p class="muted" style="font-size:11px">熵权样本：当前批次未驳回病害 × 四项指标，逐列极差归一化，p=z/Σz，e=−Σp ln p/ln n，w∝1−e。常量列区分度为 0；样本不足、全部无区分度时退化等权。缺失值按列有效均值填补。</p><p class="muted" style="font-size:11px">6 m 区段采用最低病害分值，全隧道按区段长度加权；无记录区段按 100 分仅为演示假设。等级 I/II/III/IV 分别对应分值 &lt;${state.thresholds[0]}、&lt;${state.thresholds[1]}、&lt;${state.thresholds[2]}、≥${state.thresholds[2]}。严重规则可越级触发 I。</p>${note(a.warnings.map(escape).join("<br>"), "warning")}`)}</div><aside>${panel("组合系数与敏感性", `<label class="field">AHP 占比 α <span class="range-value">${fmt(state.alpha, 2)}</span><input id="alpha" aria-label="AHP组合系数" type="range" min="0" max="1" step="0.05" value="${state.alpha}"></label><p class="muted" style="font-size:10px">0 = 纯熵权；1 = 纯 AHP。松开后更新评估、风险、预警与报告。</p><svg class="sparkline" viewBox="0 0 260 74" role="img" aria-label="组合系数敏感性曲线"><path d="M0 64H260" stroke="#334659"/><polyline points="${a.sensitivity.map((p, i) => `${(i * 260) / (a.sensitivity.length - 1)},${64 - p.shi * 0.55}`).join(" ")}"/></svg><div class="seg-labels"><span>α = 0</span><span>SHI ${fmt(a.sensitivity[0].shi)} → ${fmt(a.sensitivity.at(-1).shi)}</span><span>α = 1</span></div>`)}${panel("AHP 判断矩阵", `<p class="muted" style="font-size:10px">指标顺序：类型、尺寸、浅埋、位置。编辑上三角，下三角自动取倒数。</p><div class="matrix">${state.ahp.flatMap((row, i) => row.map((v, j) => `<input type="number" class="ahp-cell" data-i="${i}" data-j="${j}" value="${fmt(v, 6)}" ${j <= i ? "readonly" : ""} min="0.01" max="100" step="0.01" aria-label="判断矩阵第${i + 1}行第${j + 1}列">`)).join("")}</div><div class="actions">${btn("应用矩阵", "apply-ahp", "small primary")}${btn("恢复默认", "reset-ahp", "small subtle")}</div><div class="mini-stat"><small>λmax</small><span>${fmt(a.lambdaMax, 5)}</span></div><div class="mini-stat"><small>一致性 CR</small><span class="${a.cr > 0.1 ? "red" : "accent"}">${fmt(a.cr, 4)} ${a.cr <= 0.1 ? "通过" : "需修正"}</span></div><p class="muted" style="font-size:10px">CR=(λmax−4)/(3×0.90)，参考阈值 0.10。</p>`)}${panel("演示风险配置", `<div class="form-grid">${state.thresholds.map((x, i) => field("分界 " + (i + 1), "threshold" + i, x, 0, 100, 1)).join("")}${field("严重直径 / m", "severe", state.severeDiameter, 0.01, 5, 0.01)}</div><div class="actions">${btn("保存分级规则", "save-thresholds", "small primary")}</div><p class="muted" style="font-size:10px">默认严重直径规则适用于全部演示类型。工程使用前需按病害类别审定。</p>`)}</aside></div>`
    );
  }
  const ALERT_STATES = {
      new: "待确认",
      confirmed: "已确认",
      processing: "处理中",
      recheck: "待复检",
      closed: "已关闭",
    },
    NEXT = {
      new: "confirmed",
      confirmed: "processing",
      processing: "recheck",
      recheck: "closed",
    };

  function simulationSummary(key, value) {
    const names = {
      sample: "雷达示意样本",
      vehicle: "作业几何验证",
      structure: "简化结构响应",
      plans: "运维方案比较",
      external: "外部分析参考",
    };
    const r = value.result || {},
      p = value.params || {};
    let desc = "";
    if (key === "sample")
      desc = `${r.rows}采样点 × ${r.cols}道，种子 ${p.seed}；程序示意，非电磁仿真。`;
    else if (key === "vehicle")
      desc = `覆盖率 ${fmt(r.coverage)}%，作业 ${fmt(r.duration)} s，停止位置 ${fmt(r.stopAt)} m；${r.assumptions}`;
    else if (key === "structure")
      desc = `径向位移 ${fmt(r.displacement, 4)} mm，压应力 ${fmt(r.stress, 4)} MPa；${r.assumptions}`;
    else if (key === "plans")
      desc = Array.isArray(r)
        ? r
            .map(
              (x) =>
                `${x.name}：${x.cost}万元 / ${x.duration}天 / 假设降险${x.reduction}% / 得分${x.score}${x.recommended ? "（预算内首选）" : ""}`,
            )
            .join("；")
        : "";
    else
      desc = `求解器 ${value.solver || "未提供"}，模型 ${value.modelId || "未提供"}；外部结果未经本平台验证。`;
    return (
      escape(names[key] || key) +
      "：" +
      escape(desc) +
      "（" +
      escape(value.createdAt || value.importedAt || "已保存") +
      "）"
    );
  }
  function drawRoi(cv, d) {
    const ctx = cv.getContext("2d"),
      center = 0.18 + d.depth * 0.7,
      spread = 0.04 + d.diameter * 0.08,
      x = 45 + 0.55 * 583,
      w = Math.max(35, spread * 583 * 2),
      y = 12 + center * 202;
    ctx.save();
    ctx.strokeStyle = "#efb65d";
    ctx.setLineDash([5, 3]);
    ctx.strokeRect(x - w / 2, y - 12, w, 36);
    ctx.fillStyle = "#f2c77e";
    ctx.font = "10px Microsoft YaHei";
    ctx.fillText(d.id + " 示意区域", x - w / 2, y - 17);
    ctx.restore();
  }

  function radarImage(matrix) {
    const cv = document.createElement("canvas");
    cv.width = 640;
    cv.height = 210;
    paintRadar(cv, matrix, false, 1);
    return cv.toDataURL("image/png");
  }
  function reportBody(snapshot) {
    const s = snapshot,
      a = s.assessment,
      filtered = s.filteredAssessment;
    return `<article class="report-paper"><div class="report-meta">隧雷智检 / 检测与运维记录 · 演示报告</div><h2>隧道结构健康检测与评估报告</h2><p>${escape(s.project.name)} · ${escape(s.batchInfo.name)} · ${mile(s.project.start)} — ${mile(s.project.start + s.project.length)}</p><div class="report-meta">导出时间 ${escape(s.exportedAt)}<br>评估版本 ${escape(a.version)} · α=${state.alpha} · AHP CR=${fmt(a.cr, 4)}<br>筛选 ${escape(JSON.stringify(s.filters))}</div><div class="report-kpis"><div><b>${s.defects.length}</b><small>当前筛选病害 / 处</small></div><div><b>${fmt(a.shi)}</b><small>全批次 SHI / 100</small></div><div><b>${a.risk}</b><small>全局最高风险</small></div><div><b>${filtered ? fmt(filtered.shi) : "—"}</b><small>筛选范围诊断分</small></div></div><div class="note">本报告使用演示台账与演示阈值，不构成工程鉴定。导入 CSV 仅为数值解析，不自动生成诊断；所有内置病害与示意证据均单独标明来源。</div><h3>一、项目与检测范围</h3><p>圆形盾构隧道，内半径 ${s.project.radius} m，衬砌厚度 ${s.project.thickness} m，${s.project.length} m / 40 环。任务：${s.tasks.map((t) => escape(t.name) + "（" + mile(t.start) + "—" + mile(t.end) + "，进度 " + fmt(t.progress * 100) + "%）").join("；")}。</p><h3>二、数据来源与处理流程</h3><p>${s.sources.map(escape).join("<br>")}</p><p>处理状态：${escape(s.processing.status || "未运行")}；输入 ${escape(s.processing.inputName || "—")}；版本 ${escape(s.processing.version || "—")}；耗时 ${fmt(s.processing.durationMs || 0, 2)} ms。导入数据 ${s.radarData.length} 组，未与演示台账建立实测识别关系。</p><h3>三、当前筛选病害清单</h3><table><thead><tr><th>编号 / 类型</th><th>里程 / 环向</th><th>直径 / 埋深 m</th><th>三维坐标 m</th><th>风险 / 复核</th></tr></thead><tbody>${s.defects.map((d) => `<tr><td>${d.id}<br>${C.TYPES[d.type].name}</td><td>${mile(d.mileage)}<br>${d.angle}°</td><td>${fmt(d.diameter, 3)} / ${fmt(d.depth, 3)}</td><td>${fmt(d.position.x, 2)}, ${fmt(d.position.y, 2)}, ${fmt(d.position.z, 2)}</td><td>${d.risk} / ${REVIEW[d.review]}</td></tr>`).join("") || '<tr><td colspan="5">当前筛选无记录</td></tr>'}</tbody></table><p>尺寸依据：演示台账给定，不是当前 CSV 反演；置信度不能等同诊断可靠性。${escape(s.coordinateDefinition)}</p><h3>四、证据图</h3><p>以下展示当前筛选前两条示意证据，其余对象可在病害详情按编号逐一预览。</p>${
      s.defects
        .slice(0, 2)
        .map(
          (d) =>
            `<figure class="report-evidence"><p>${d.id} · ${d.evidenceId} · ${d.lineId} · ${d.version} · 置信度 ${fmt(d.confidence * 100)}% · ${escape(d.source)}</p><img src="${radarImage(demoMatrix(d))}" alt="${d.id}程序示意雷达证据"><p>基于该条演示台账参数生成的示意信号，无物理标定，非 RTM 成像结果。</p></figure>`,
        )
        .join("") || "<p>无符合筛选条件的证据。</p>"
    }<h3>五、健康评估与风险区段</h3><p>组合权重 ${a.weights.map((w) => fmt(w, 4)).join(" / ")}；病害分值=100(1−Σwj xj)。区段取未驳回病害最低分，全隧道按区段长度加权。无入库异常区段按100分仅为演示假设。阈值 ${state.thresholds.join("/")}；严重直径阈值 ${state.severeDiameter} m；严重规则不被整体均值掩盖。</p><div class="report-chart">${a.segments.map((x) => `<div><span style="height:${Math.max(1, x.shi)}px"></span>${fmt(x.shi)}<br>${mile(x.start)}</div>`).join("")}</div><p>${escape(s.assessmentScope)}。列表筛选不会改变全批次分值，筛选诊断分单独计算。</p><h3>六、预警、治理与仿真</h3><p>${
      s.alerts
        .filter((x) => x.batch === s.batch)
        .map(
          (x) =>
            x.defectId +
            "：" +
            ALERT_STATES[x.status] +
            "，" +
            escape(x.condition),
        )
        .join("；") || "暂无预警。"
    }</p><p>治理任务：${
      state.maintenance
        .filter((x) => x.batch === s.batch)
        .map(
          (x) =>
            x.defectId +
            " / " +
            escape(x.name) +
            " / " +
            escape(x.date) +
            " / " +
            escape(x.status),
        )
        .join("；") || "尚未创建。"
    }</p><p>${
      Object.entries(s.simulation)
        .map(([key, val]) => simulationSummary(key, val))
        .join("<br>") || "未保存仿真工况。"
    }</p><h3>七、建议与局限性</h3><p>优先专项复核高风险对象，结合钻孔、其他无损检测与运营条件确认治理方案。停运、限速等重大措施须专业复核。复检证据应记录后再关闭预警，不通过点击操作改变病害真实状态。</p><p>${s.limitations.map(escape).join("<br>")}</p><p>RCAN、RTM 雷达接口、真实车辆硬件及经验证有限元尚未接入；当前影像检测采用独立的 crack-seg U-Net 裂缝分割流程，尚未自动写入工作台病害记录。结构响应只提供简化圆环参数敏感性，方案预算与降险为演示假设。</p></article>`;
  }
  function reportsPage() {
    const snap = C.exportSnapshot(state);
    return (
      heading(
        "检测报告",
        "当前批次、筛选条件、评估参数和处置状态同步生成可追溯快照。",
        "03 / 对应计划第 3 部分",
        btn("病害 CSV", "export-csv", "subtle") +
          btn("项目 JSON", "export-json", "subtle") +
          btn("下载 HTML 报告", "export-html", "primary") +
          btn("打印 / PDF", "print-report", "subtle"),
      ) +
      moduleTabs() +
      `<div class="print-hide">${filters()}${note("报告始终显示全批次 SHI，并单列当前筛选诊断分。HTML 内嵌证据图，可离线打开和打印为 PDF。", "info")}<br></div>` +
      reportBody(snap)
    );
  }
  function download(name, content, type) {
    const blob = new Blob([content], { type }),
      a = document.createElement("a"),
      url = URL.createObjectURL(blob);
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast("已生成 " + name);
  }
  function csvExport() {
    const ds = C.getDefects(state),
      a = C.assess(state),
      rows = [
        [
          "批次",
          "病害编号",
          "类型",
          "里程_m",
          "环号",
          "环向_deg",
          "X_m",
          "Y_m",
          "Z_m",
          "直径_m",
          "长度_m",
          "埋深_m",
          "置信度",
          "风险",
          "复核状态",
          "测线",
          "证据编号",
          "模型版本",
          "来源",
          "评估版本",
        ],
        ...ds.map((d) => [
          state.batch,
          d.id,
          C.TYPES[d.type].name,
          d.mileage,
          d.ring,
          d.angle,
          d.position.x,
          d.position.y,
          d.position.z,
          d.diameter,
          d.length,
          d.depth,
          d.confidence,
          d.risk,
          REVIEW[d.review],
          d.lineId,
          d.evidenceId,
          d.version,
          d.source,
          a.version,
        ]),
      ];
    download(
      "病害清单_" + state.batch + ".csv",
      "\uFEFF" +
        rows
          .map((r) =>
            r.map((x) => '"' + String(x).replace(/"/g, '""') + '"').join(","),
          )
          .join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  function htmlExport() {
    const snap = C.exportSnapshot(state),
      body = reportBody(snap);
    const css =
      "body{margin:0;background:#0b1018;color:#e8f0f7;font:14px/1.8 \"Microsoft YaHei\",\"PingFang SC\",Arial,sans-serif;padding:30px 25px}button{background:#1c2a3b;border:1px solid #33465b;border-radius:7px;color:#e8f0f7;padding:8px 13px;margin:0 auto 18px;display:block;cursor:pointer}button:hover{background:#283e50;border-color:#56d9b1}a{color:#56d9b1}.report-paper{max-width:1040px;margin:0 auto;background:#131b27;color:#e8f0f7;border:1px solid rgba(86,217,177,.14);border-radius:14px;box-shadow:0 16px 36px rgba(0,0,0,.18);padding:30px 34px}.report-paper h2{color:#e8f0f7;font-size:28px;margin:0 0 8px}.report-paper h3{color:#56d9b1;margin-top:28px}.report-paper p{color:#b9c9d7}.report-paper table{width:100%;border-collapse:collapse;color:#e8f0f7;font-size:11px}.report-paper td,.report-paper th{padding:9px;border:1px solid rgba(86,217,177,.12);text-align:left}.report-paper th{background:#111b28;color:#9fb2c4}.report-paper tr:nth-child(even){background:rgba(127,155,181,.025)}.report-paper tr:hover{background:rgba(86,217,177,.06)}.report-meta{font-size:11px;color:#7f9bb5}.report-kpis{display:flex;gap:40px;margin:24px 0}.report-kpis b{color:#56d9b1;font-size:30px;display:block}.report-kpis small{color:#9fb2c4}.note{padding:12px;background:rgba(86,217,177,.08);border:1px solid rgba(86,217,177,.2);border-radius:7px;color:#bfeee0}.report-paper img{max-width:100%;height:auto;border:1px solid rgba(86,217,177,.14);border-radius:8px}.report-chart{display:flex;align-items:end;gap:9px;height:125px;border-bottom:1px solid rgba(86,217,177,.2)}.report-chart div{flex:1;color:#9fb2c4;text-align:center;font-size:9px}.report-chart span{display:block;background:linear-gradient(180deg,#56d9b1,#2f806e);border-radius:3px 3px 0 0}p{overflow-wrap:anywhere}tr{break-inside:avoid}.report-evidence{margin:20px 0;break-inside:avoid}.report-chart{break-inside:avoid}h2,h3{break-after:avoid}@page{size:A4;margin:14mm}@media print{button{display:none}body{margin:0;padding:0;background:#fff;color:#243346}.report-paper{background:#fff;color:#243346;border:0;box-shadow:none;padding:0}.report-paper h2,.report-paper h3{color:#243346}.report-paper p,.report-paper th,.report-paper td,.report-meta,.report-kpis small,.report-chart div{color:#243346}.report-paper th{background:#ecf2f5}.report-paper td,.report-paper th{border-color:#ccd6de}.note{background:#e9f2ee;border-color:#bdd0c9;color:#49685f}.report-kpis b{color:#243346}.report-chart{border-bottom-color:#bbb}.report-chart span{background:#4b8c80}}";
    download(
      "隧道检测报告_" + state.batch + ".html",
      '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>隧道检测评估报告</title><style>' +
        css +
        '</style><button onclick="window.print()">打印 / 保存为 PDF</button>' +
        body +
        "</html>",
      "text/html;charset=utf-8",
    );
    state.lastReport = {
      batch: state.batch,
      time: snap.exportedAt,
      version: snap.assessment.version,
    };
    save();
  }
  // 雷达画布的坐标轴始终来自元数据；示意数据不伪造物理尺度。
  function paintRadar(
    canvas,
    matrix,
    overlay = true,
    gain = palette,
    meta = {},
    selectedTrace = trace,
    selectedZoom = zoom,
  ) {
    if (!canvas || !matrix) return;
    const W = 640,
      H = 240;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d"),
      rows = matrix.length,
      cols = matrix[0].length,
      low = document.createElement("canvas");
    low.width = cols;
    low.height = rows;
    const lc = low.getContext("2d"),
      im = lc.createImageData(cols, rows);
    let max = 0;
    for (const row of matrix)
      for (const x of row) max = Math.max(max, Math.abs(x));
    max = max || 1;
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        const v = Math.max(-1, Math.min(1, (matrix[r][c] / max) * gain)),
          i = (r * cols + c) * 4,
          amp = Math.abs(v);
        im.data[i] = v > 0 ? 30 + amp * 170 : 23 + amp * 31;
        im.data[i + 1] = v > 0 ? 48 + amp * 165 : 39 + amp * 90;
        im.data[i + 2] = v > 0 ? 58 + amp * 150 : 58 + amp * 155;
        im.data[i + 3] = 255;
      }
    lc.putImageData(im, 0, 0);
    ctx.fillStyle = "#08121f";
    ctx.fillRect(0, 0, W, H);
    ctx.drawImage(low, 45, 12, W - 57, H - 38);
    ctx.font = "10px Segoe UI,Microsoft YaHei";
    ctx.fillStyle = "#88a3b9";
    ctx.fillText(meta.traceSpacingM ? "距离 / m" : "道号", W - 70, H - 5);
    const axisValue = value => value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 10000) ? value.toExponential(1) : fmt(value, 2);
    const xTicks = [...new Set([0, 1, 2, 3, 4].map(i => Math.round(i * (cols - 1) / 4)))];
    ctx.textAlign = "center";
    for (const c of xTicks) ctx.fillText(meta.traceSpacingM ? axisValue(c * meta.traceSpacingM) : c, 45 + c / (cols - 1) * (W - 57), H - 14);
    ctx.textAlign = "start";
    const yTicks = [...new Set([0, 1, 2, 3, 4].map(i => Math.round(i * (rows - 1) / 4)))];
    const dt = meta.sampleIntervalNs || (meta.timeWindowNs || 0) / (rows - 1);
    for (const rr of yTicks) ctx.fillText(dt ? axisValue(rr * dt) : rr, 4, 15 + rr / (rows - 1) * (H - 38));
    ctx.fillText(
      meta.sampleIntervalNs || meta.timeWindowNs ? "ns" : "采样",
      3,
      H - 6,
    );
    if (overlay) {
      ctx.strokeStyle = "#67e4bd";
      ctx.lineWidth = 1;
      const x = 45 + (Math.min(selectedTrace, cols - 1) / (cols - 1)) * (W - 57);
      ctx.beginPath();
      ctx.moveTo(x, 12);
      ctx.lineTo(x, H - 27);
      ctx.stroke();
    }
    canvas.style.width = selectedZoom * 100 + "%";
  }
  function drawWave(canvas, r, selectedTrace = trace) {
    if (!canvas) return;
    const matrix = r.matrix,
      W = 640,
      H = 120,
      ctx = canvas.getContext("2d");
    canvas.width = W;
    canvas.height = H;
    ctx.fillStyle = "#08121f";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#273d50";
    ctx.beginPath();
    ctx.moveTo(30, H / 2);
    ctx.lineTo(W - 10, H / 2);
    ctx.stroke();
    let vals = matrix.map((row) => row[Math.min(selectedTrace, row.length - 1)]),
      max = Math.max(...vals.map(Math.abs), 0.000001);
    ctx.strokeStyle = "#6adbb6";
    ctx.beginPath();
    vals.forEach((v, i) => {
      let x = 30 + (i / (vals.length - 1)) * (W - 45),
        y = H / 2 - (v / max) * 44;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = "#708ba2";
    ctx.font = "10px sans-serif";
    ctx.fillText("0", 12, 63);
    ctx.fillText(r.metadata?.sampleIntervalNs || r.metadata?.timeWindowNs ? "双程时间 / ns →" : "采样点 →", 540, 114);
  }
  function drawUnfold() {
    const cv = $("#unfold");
    if (!cv) return;
    cv.width = 1000;
    cv.height = 230;
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#0c1724";
    ctx.fillRect(0, 0, 1000, 230);
    ctx.font = "11px Segoe UI";
    ctx.strokeStyle = "#263b50";
    ctx.fillStyle = "#708ca5";
    [0, 90, 180, 270, 360].forEach((a, i) => {
      const y = 20 + i * 43;
      ctx.beginPath();
      ctx.moveTo(55, y);
      ctx.lineTo(980, y);
      ctx.stroke();
      ctx.fillText(a + "°", 10, y + 4);
    });
    [0, 12, 24, 36, 48].forEach((x, i) =>
      ctx.fillText(mile(3128 + x), 40 + i * 228, 219),
    );
    const colors = {
      I: "#ff787f",
      II: "#efb65d",
      III: "#e1ce7a",
      IV: "#56d9b1",
    };
    C.getDefects(state).forEach((d) => {
      const x = 55 + (d.position.x / 48) * 925,
        y = 20 + (d.angle / 360) * 172;
      ctx.fillStyle = colors[d.risk];
      ctx.beginPath();
      ctx.arc(x, y, d.id === state.selectedId ? 8 : 5, 0, Math.PI * 2);
      ctx.fill();
      if (d.id === state.selectedId) {
        ctx.strokeStyle = "#fff";
        ctx.stroke();
        ctx.fillStyle = "#fff";
        ctx.fillText(d.id, x + 10, y - 5);
      }
    });
    cv.onclick = (e) => {
      const rect = cv.getBoundingClientRect(),
        x = ((e.clientX - rect.left) / rect.width) * 1000,
        y = ((e.clientY - rect.top) / rect.height) * 230;
      const d = C.getDefects(state).find(
        (d) =>
          Math.hypot(
            x - (55 + (d.position.x / 48) * 925),
            y - (20 + (d.angle / 360) * 172),
          ) < 15,
      );
      if (d) select(d.id, false);
    };
  }
  function drawCanvases() {
    const r = route === "radar" ? (radarCorrespondenceData() || radarDemoData()) : radarData();
    paintRadar($("#rawRadar"), r.matrix, true, palette, r.metadata);
    if(route === "radar" && radarCorrespondenceData()) window.TunnelEvidenceLink.overlay($("#rawRadar"),r,state,evidenceContext());
    if (r.source === "demo" && overlayRegions && $("#rawRadar"))
      drawRoi($("#rawRadar"), chosen());
    drawWave($("#wave"), r);
    if ($("#processedRadar") && processed)
      paintRadar(
        $("#processedRadar"),
        processed.matrix,
        true,
        palette,
        r.metadata,
      );
    if ($("#detailRadar")) paintRadar($("#detailRadar"), demoMatrix(), false);
    if ($("#simRadar"))
      paintRadar(
        $("#simRadar"),
        C.generateRadar(state.simulation.sample?.params || {}),
        false,
      );
    drawUnfold();
    const cv = $("#rawRadar");
    if (cv)
      cv.onclick = (e) => {
        const box = cv.getBoundingClientRect();
        trace = Math.round(
          Math.max(
            0,
            Math.min(
              1,
              (((e.clientX - box.left) / box.width) * 640 - 45) / 583,
            ),
          ) *
            (r.cols - 1),
        );
        if ($("#trace")) $("#trace").value = trace;
        if ($("#traceNumber")) $("#traceNumber").textContent = trace;
        paintRadar(cv, r.matrix, true, palette, r.metadata);
        if(route === "radar" && radarCorrespondenceData()) window.TunnelEvidenceLink.overlay(cv,r,state,evidenceContext());
        if (r.source === "demo" && overlayRegions) drawRoi(cv, chosen());
        drawWave($("#wave"), r);
      };
  }
  function render() {
    ensureAlerts();
    const routes = {
      overview,
      tasks: tasksPage,
      radar: radarPage,
      processing: processingPage,
      defects: defectsPage,
      twin: twinPage,
      health: healthPage,
      alerts: alertsPage,
      reports: reportsPage,
      simulation: simulationPage,
    };
    const requestedRoute = location.hash.slice(1).split("?")[0] || "overview";
    route = syncRouteTrail(routes[requestedRoute] ? requestedRoute : "overview");
    $("#navigation").innerHTML = groups
      .map(
        (g) =>
          `<div class="nav-section">${g.title}</div>${g.items.map(([id, name]) => `<a href="#${id}" class="nav-item ${route === id ? "active" : ""}" ${route === id ? 'aria-current="page"' : ""}>${icon(id)}<span>${name}</span>${id === "alerts" ? '<span class="count">' + currentAlerts().filter((x) => x.status !== "closed").length + "</span>" : ""}</a>`).join("")}`,
      )
      .join("");
    $("#batch").innerHTML = state.batches
      .map(
        (b) =>
          `<option value="${b.id}" ${b.id === state.batch ? "selected" : ""}>${b.name}</option>`,
      )
      .join("");
    $("#dataBadge").textContent =
      evidenceActive() ? "现场标注 · 相对位置对应" : radarMode === "import" && ["radar", "processing"].includes(route)
        ? "导入矩阵 · 病害仍为演示"
        : "演示数据";
    window.TunnelVideoMonitor?.beforeRender(route);
    window.TunnelRadarAcquisition?.beforeRender(route, radarContext());
    try {
      $("#page").innerHTML = routes[route]();
      if (route === "tasks") {
        const t = task();
        window.TunnelVideoMonitor?.mount($("#liveMonitorMount"), {
          batchId: state.batch,
          batchName: state.batches.find((b) => b.id === state.batch)?.name || state.batch,
          taskId: t.id,
          taskName: t.name,
        });
      }
      if (route === "radar") {
        window.TunnelRadarAcquisition?.mount($("#radarAcquisitionMount"), {
          context: radarContext(),
          selected: () => radarMode === "import" && state.radars[selectedRadar]?.batchId === state.batch ? state.radars[selectedRadar] : null,
          options: radarOptions, directory: radarDirectory, metadataText,
          importMessage: importError || importNotice,
          paint: (data, view) => { paintRadar(view.raw, data.matrix, true, view.gain, data.metadata, view.trace, view.zoom); window.TunnelEvidenceLink.overlay(view.raw,data,state,evidenceContext()); drawWave(view.wave, data, view.trace); },
          saveFrame: (data, association) => { storeRadarRecord(data, association); importNotice="网络接入帧已保存；已选为智能处理输入。"; render(); },
        });
      }
      if($("#evidenceMount")) window.TunnelEvidenceLink.mount($("#evidenceMount"),{
        state, route, context:evidenceContext(), tasks:batchTasks(),save,changed:refreshEvidenceViews,
        setTask:id=>{if(!batchTasks().some(t=>t.id===id))throw Error("任务不存在");state.activeTask=id;save();render();syncScene();},
        select:selectEvidence,
        openVideo:(anchor,id)=>{state.evidenceSelectedId=id;window.TunnelEvidenceCore.setSource(state,evidenceContext(),anchor.sourceId);state.pendingVideoEvidence=anchor;window.TunnelEvidenceCore.setMode(state,evidenceContext(),"evidence");if(route==="tasks")render();else location.hash="tasks";},
        openRadar:(anchor,id)=>{const index=state.radars.findIndex(r=>r.id===anchor.archiveId&&r.batchId===state.batch&&r.taskId===task().id);if(index<0)throw Error("原始雷达快照不存在，请保留原矩阵");state.evidenceSelectedId=id;window.TunnelEvidenceCore.setSource(state,evidenceContext(),anchor.sourceId);selectedRadar=index;radarMode="import";state.pendingRadarEvidence=anchor;window.TunnelEvidenceCore.setMode(state,evidenceContext(),"evidence");if(route==="radar")render();else location.hash="radar";},
        followChanged:on=>{if(on&&taskRunning)toggleTask();},
        follow:(ratio,id)=>{if(taskRunning){clearInterval(timer);timer=null;taskRunning=false;}const t=task();t.progress=Math.max(0,Math.min(1,ratio));t.status="paused";if(id&&state.evidenceSelectedId!==id){state.evidenceSelectedId=id;syncScene();window.TunnelEvidenceLink.refresh();}else syncTask();if($("#taskPercent"))$("#taskPercent").textContent=fmt(t.progress*100)+"%";if($("#taskBar"))$("#taskBar").style.width=t.progress*100+"%";if($("#taskDistance"))$("#taskDistance").textContent="相对对齐位置 "+fmt(t.progress*(t.end-t.start))+" m";if($("#taskStatus"))$("#taskStatus").textContent=t.name+" · 录像进度对应";refreshTaskRow();},
      });
      drawCanvases();
    } catch (e) {
      $("#page").innerHTML =
        heading("当前页面无法计算", escape(e.message), "输入检查") +
        note("请检查参数，或使用“操作指引”恢复默认评估配置。", "warning");
      console.error(e);
    }
    document.title =
      "隧雷智检 · " +
      groups.flatMap((g) => g.items).find((x) => x[0] === route)[1];
  }
  let pendingFocus = null,
    processingToken = 0,
    modelController = null;
  function select(id, fromFrame) {
    if (!all().some((d) => d.id === id)) return;
    state.selectedId = id;
    radarMode = "demo";
    save();
    if (fromFrame && route === "twin") {
      const aside = $("#page .grid-main>aside");
      aside.innerHTML =
        detail() +
        ringSection() +
        panel(
          "空间定位",
          note("原点为 K3+128，X 沿轴线前进，0° 为拱顶，单位 m。", "info"),
        );
      paintRadar($("#detailRadar"), demoMatrix(), false);
      drawUnfold();
      $("#historyComparison").innerHTML = historyTable();
    } else {
      pendingFocus = id;
      if (route !== "twin") navigate("twin");
      else render();
    }
  }
  function showModal(html) {
    $("#modalContent").innerHTML = html;
    const d = $("#modal");
    if (!d.open) d.showModal();
  }
  function closeModal() {
    $("#modal").close();
  }
  function guideDialog() {
    const steps = [
      [
        "tasks",
        "配置检测任务",
        "查看设备、接入现场视频，再设置仿真参数并观察三臂检测车。",
      ],
      [
        "radar",
        "检查雷达数据",
        "先导入外部矩阵或接收网关帧，保存为处理快照；下方保留独立示意信号。",
      ],
      [
        "processing",
        "运行实际预处理",
        "执行减背景和时间增益；查看 RCAN / RTM 的待接入边界。",
      ],
      [
        "defects",
        "病害复核",
        "选择同源演示台账记录，查看证据与置信度，并登记复核。",
      ],
      ["twin", "空间定位", "列表、三维、环向展开图和证据使用同一个病害编号。"],
      [
        "health",
        "调整评估",
        "改变组合权重，检查 CR、SHI、局部严重风险和区段分布。",
      ],
      ["alerts", "完成处置闭环", "确认 → 处理 → 复检 → 关闭，记录完整过程。"],
      [
        "reports",
        "导出当前快照",
        "导出当前筛选 CSV、完整项目 JSON 和含证据图的 HTML 报告。",
      ],
    ];
    const s = steps[guide];
    showModal(
      `<div class="guide-step">演示指引 ${guide + 1} / ${steps.length}</div><h2>${s[1]}</h2><p>${s[2]}</p>${note("四个模块对应计划书的四部分；内置数据与评估阈值均为演示配置。", "info")}<div class="actions">${btn("进入此步骤", "guide-enter", "primary", `data-route="${s[0]}"`)}${btn("下一步", "guide-next", "subtle")}${btn("关闭指引", "close-modal", "subtle")}</div>`,
    );
  }
  function refreshTaskRow() {
    const t = task(),
      row = $$("[data-task-row]").find((el) => el.dataset.taskRow === t.id);
    if (row) {
      row.querySelector("[data-task-progress]").textContent =
        fmt(t.progress * 100) + "%";
      row.querySelector("[data-task-status]").textContent = statusTask(
        t.status,
      );
    }
  }
  function refreshPlayback() {
    refreshTaskRow();
    if ($("#taskLog")) $("#taskLog").innerHTML = timeline();
    syncTask();
    $$('[data-action="scene-run"],[data-action="toggle-task"]').forEach(
      (el) => (el.textContent = taskRunning ? "暂停检测" : "启动 / 继续"),
    );
    if ($("#taskStatus"))
      $("#taskStatus").textContent =
        task().name + " · " + statusTask(task().status);
  }
  function toggleTask() {
    const t = task();
    if (taskRunning) {
      taskRunning = false;
      t.status = "paused";
      clearInterval(timer);
      log("暂停检测任务 " + t.name);
      refreshPlayback();
      return;
    }
    const p = taskParams(),
      r = C.simulateVehicle(p);
    if (r.collision) {
      toast("初始车体与障碍物重叠，无法开始。", true);
      return;
    }
    const max = (r.stopAt - p.start) / (p.end - p.start);
    if (t.progress >= max - 1e-6) {
      toast(
        r.stopped
          ? "已停障，请调整障碍或重置进度。"
          : "任务已完成，可重置后重新演示。",
      );
      return;
    }
    taskRunning = true;
    t.status = "running";
    log("开始检测任务 " + t.name);
    refreshPlayback();
    clearInterval(timer);
    let last = performance.now(),
      ticks = 0;
    timer = setInterval(() => {
      const now = performance.now(),
        dt = (now - last) / 1000;
      last = now;
      t.progress = Math.min(
        max,
        t.progress + (t.speed * dt) / (t.end - t.start),
      );
      syncTask();
      refreshTaskRow();
      if ($("#taskPercent"))
        $("#taskPercent").textContent = fmt(t.progress * 100) + "%";
      if ($("#taskBar")) $("#taskBar").style.width = t.progress * 100 + "%";
      if ($("#taskDistance"))
        $("#taskDistance").textContent =
          "已行驶 " + fmt(t.progress * (t.end - t.start)) + " m";
      if (++ticks % 20 === 0) save();
      if (t.progress >= max - 1e-6) {
        taskRunning = false;
        t.status = r.stopped ? "blocked" : "completed";
        clearInterval(timer);
        log((r.stopped ? "检测车提前停障：" : "检测任务完成：") + t.name);
        refreshPlayback();
      }
    }, 100);
  }
  function haltTask() {
    if (taskRunning) {
      taskRunning = false;
      clearInterval(timer);
      task().status = "paused";
      save();
    }
  }
  function storeRadarRecord(data, association) {
    if (association.batchId !== state.batch || association.taskId !== task().id)
      throw Error("任务或批次在读取期间变更，未保存旧来源，请重新导入。");
    const r=JSON.parse(JSON.stringify(data));
    r.id="R-"+Date.now()+"-"+Math.random().toString(36).slice(2,8);
    r.source="import";r.importedAt=new Date().toISOString();
    r.batchId=association.batchId;r.taskId=association.taskId;
    r.lineId=r.lineId || r.metadata?.lineId || association.lineId;
    state.radars.push(r);selectedRadar=state.radars.length-1;radarMode="import";trace=0;processed=null;
    log("导入外部雷达数据 "+r.name+"，"+r.rows+"×"+r.cols);
    return r;
  }
  async function importCSV() {
    importError="";importNotice="";
    const association=radarContext();
    try {
      const f=$("#csvFile").files[0];
      if(!f)throw Error("请先选择 CSV 或 JSON 雷达文件。");
      if(!/\.(csv|json)$/i.test(f.name))throw Error("暂不支持该设备格式，只支持数值 CSV 或 JSON 接入帧。");
      if(f.size>C.LIMITS.bytes)throw Error("文件超过 5 MB 上限。");
      metadataText=$("#metadata").value;
      const text=await f.text();
      if(route!=="radar")throw Error("已离开雷达页，未导入文件，请返回后重试。");
      let r;
      if(/\.json$/i.test(f.name)) {
        r=window.TunnelRadarAcquisition.parseFrame(text,association);
        r.provenance={transport:"file-json",declaredSource:r.declaredSource,adapterVersion:"radar-frame-1.0"};
      } else {
        r=C.parseCSV(text,metadataText.trim() || {});
        for(const key of ["batchId","taskId"])if(r.metadata[key]!=null && r.metadata[key]!==association[key])throw Error("元数据 "+key+" 与当前任务关联不一致。");
        if(r.metadata.amplitudeUnit!=null && (typeof r.metadata.amplitudeUnit!=="string" || r.metadata.amplitudeUnit.length>128))throw Error("amplitudeUnit 应为不超过 128 字的文本。");
        r.capturedAt=window.TunnelRadarAcquisition.normalizeTime(r.metadata.capturedAt);
        if(!r.capturedAt)r.warnings.push("未提供源端采集时间；本机导入时间不代表现场采集时间。");
        r.provenance={transport:"file-csv",declaredSource:typeof r.metadata.source==="string"?r.metadata.source:"未声明，真实性未核验",adapterVersion:r.version};
      }
      r.name=f.name;r.receivedAt=new Date().toISOString();
      storeRadarRecord(r,association);
      importNotice="已解析 "+r.rows+" × "+r.cols+" 数值矩阵；外部来源待核验，"+(r.warnings.length?"请核对来源与标定提示。":"标定字段完整（未核验）。");
      render();toast(importNotice);
    } catch(e) {
      importError=e.message;
      if($("#importMessage"))$("#importMessage").textContent=importError;
      toast(importError,true);
    }
  }
  function runPreprocess() {
    const r = radarData(),
      gain = Number($("#gain")?.value ?? state.processing.gain ?? 1),
      background = $("#background")?.checked ?? true;
    if (!Number.isFinite(gain) || gain < 0 || gain > 20)
      throw Error("增益须为 0～20。");
    const token = ++processingToken;
    state.processing = {
      ...state.processing,
      status: "running",
      batchId: state.batch,
      gain,
      background,
      inputId: r.id,
      inputName: r.name,
      source: r.source,
      startedAt: new Date().toISOString(),
      message: "预处理运行中",
    };
    save();
    render();
    setTimeout(() => {
      if (token !== processingToken) return;
      try {
        const start = performance.now(),
          matrix = C.preprocess(r.matrix, { gain, background });
        const duration = performance.now() - start;
        processed = { input: r.id, matrix };
        state.processing = {
          ...state.processing,
          status: "completed",
          durationMs: duration,
          version: "background-gain-1.0",
          finishedAt: new Date().toISOString(),
          message: "数值预处理完成；影像裂缝检测为独立流程",
        };
        log("完成数值预处理 " + r.name);
        if (route === "processing") render();
        toast("预处理完成，原始和输出矩阵已并列显示。");
      } catch (e) {
        state.processing.status = "failed";
        state.processing.message = e.message;
        save();
        render();
        toast(e.message, true);
      }
    }, 180);
  }
  function runSimulation() {
    let params = {},
      result;
    const val = (id) => Number($("#" + id).value);
    if (simTab === "sample") {
      [
        "thickness",
        "epsilon",
        "rebarSpacing",
        "depth",
        "diameter",
        "voidPosition",
        "noise",
        "seed",
      ].forEach((k) => (params[k] = val(k)));
      result = {
        rows: 96,
        cols: 160,
        source: "程序生成示意信号",
        labels: {
          type: "示意空洞",
          positionFraction: params.voidPosition,
          diameterParameterM: params.diameter,
          depthParameterM: params.depth,
        },
        matrix: C.generateRadar(params),
      };
    }
    if (simTab === "vehicle") {
      haltTask();
      const t = task();
      Object.assign(t, {
        speed: val("speed"),
        spacing: val("spacing"),
        obstacle: $("#simObstacle").checked,
        obstacleX: val("obstacle"),
        progress: 0,
        status: "ready",
      });
      params = taskParams();
      result = C.simulateVehicle(params);
    }
    if (simTab === "structure") {
      ["thickness", "E", "load", "radius"].forEach((k) => (params[k] = val(k)));
      result = C.simulateStructure(params);
    }
    if (simTab === "plans") {
      params = {
        budget: val("budget"),
        weights: [val("costWeight"), val("timeWeight"), val("riskWeight")],
      };
      result = C.comparePlans(params);
    }
    state.simulation[simTab] = {
      params,
      result,
      createdAt: new Date().toISOString(),
      batch: state.batch,
      source: simTab === "sample" ? "程序示意信号" : "简化演示计算",
    };
    log("保存仿真工况：" + simTab);
    render();
    toast("参数和计算结果已更新。");
  }
  function maintenanceDialog() {
    const d = chosen();
    showModal(
      `<h2>为 ${d.id} 创建治理任务</h2><p>${mile(d.mileage)} · ${C.TYPES[d.type].name}</p><form id="maintenanceForm"><div class="form-grid"><label class="field wide">任务名称<input id="maintenanceName" value="专项复检与治理方案复核" maxlength="80" required></label><label class="field">优先级<select id="maintenancePriority"><option>高</option><option>中</option><option>常规</option></select></label><label class="field">计划时间<input id="maintenanceDate" type="date" value="${new Date().toISOString().slice(0, 10)}" required></label></div><div class="actions"><button type="submit" class="primary">保存治理任务</button></div></form>`,
    );
  }
  function advanceAlert(id) {
    const a = state.alerts.find((x) => x.id === id),
      next = NEXT[a.status];
    if (!next) return;
    if (next === "closed") {
      showModal(
        `<h2>登记复检并关闭 ${a.defectId}</h2><p>复检结论仅作为本地处置记录，不自动改写病害尺寸或 SHI。</p><form id="recheckForm" data-id="${a.id}"><label class="field">复检证据编号 / 处置说明<textarea id="recheckNote" rows="4" required minlength="5" placeholder="例如：演示复检记录 RE-001，已完成专项复核，继续跟踪监测。"></textarea></label><div class="actions"><button class="primary" type="submit">登记证据并关闭</button></div></form>`,
      );
      return;
    }
    a.status = next;
    a.history.push({
      time: new Date().toISOString(),
      status: next,
      note: "用户登记状态变更",
    });
    log(a.defectId + " 预警更新为 " + ALERT_STATES[next]);
    render();
    toast("已保存预警状态。");
  }
  // 折叠和跳转只操作当前 DOM，保留正在播放的视频、摄像头连接与第一人称场景。
  function showTaskPanel(id) {
    const target = $(id); if (!target) return;
    if (target.tagName === "DETAILS") target.open = true;
    target.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
  document.addEventListener("toggle", event => {
    const el = event.target; if (!el.isConnected) return;
    if (el.id === "taskEquipmentDrawer") taskPanels.equipment = el.open;
    if (el.id === "taskEvidenceDrawer") taskPanels.evidence = el.open;
  }, true);
  const actions = {
    "task-evidence": () => showTaskPanel("#taskEvidenceDrawer"),
    "task-parameters": () => showTaskPanel("#taskConfiguration"),
    "task-capture-video": () => {
      const drawer = $("#taskEvidenceDrawer"); if (!drawer) return;
      drawer.open = true;
      drawer.querySelector('[data-evidence-action="capture-video"]')?.click();
      showTaskPanel("#taskEvidenceDrawer");
    },
    "close-modal": closeModal,
    "multiscene-select": (el) => {
      activeMultiscene = el.dataset.scene || "overview";
      render();
    },
    "equipment-select": (el) => {
      activeEquipment = el.dataset.equipment || "vehicle";
      $$(".equipment-card").forEach((card) => {
        const active = card.dataset.equipment === activeEquipment;
        card.classList.toggle("active", active);
        card.setAttribute("aria-pressed", String(active));
      });
      const detail = $("#equipmentDetail");
      if (detail) detail.innerHTML = equipmentDetail(activeEquipment);
    },
    "go-back": (el) => backRoute(el.dataset.fallback),
    "go-overview": () => navigate("overview"),
    "go-image-review": () => {
      scrollToAnchor("image-review");
      if (route !== "defects") navigate("defects");
    },
    "scroll-defects-top": () => {
      if (route !== "defects") navigate("defects");
      requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: "smooth" }));
    },
    "go-tasks": () => navigate("tasks"),
    "go-processing": () => navigate("processing"),
    "go-defects": () => navigate("defects"),
    "go-reports": () => navigate("reports"),
    guide: () => {
      guide = 0;
      guideDialog();
    },
    "guide-next": () => {
      guide = (guide + 1) % 8;
      guideDialog();
    },
    "guide-enter": (el) => {
      closeModal();
      navigate(el.dataset.route);
    },
    "reset-filters": () => {
      state.filters = C.createState().filters;
      save();
      render();
    },
    view: (el) => post({ type: "view", view: el.dataset.view }),
    roam: () => sceneMode("roam"),
    fly: () => sceneMode("fly"),
    rotate: () => post({ type: "rotate" }),
    "iso-cut": () => post({ type: "iso-cut" }),
    "step-defect": (el) => post({ type: "step", dir: Number(el.dataset.dir) }),
    "scene-run": () => toggleTask(),
    "fullscreen-scene": async (el) => {
      const container = el.closest(".panel");
      if (document.fullscreenElement) await document.exitFullscreen();
      else await container.requestFullscreen();
    },
    "restore-background": () => {
      state.scenePrefs.backgroundImage = null;
      state.scenePrefs.stars = true;
      state.scenePrefs.city = true;
      post({ type: "background", url: null });
      post({ type: "layer", name: "stars", visible: true });
      post({ type: "layer", name: "city", visible: true });
      $$('[data-layer="stars"],[data-layer="city"]').forEach(
        (el) => (el.checked = true),
      );
      save();
      toast("原有星空和城市背景已恢复。");
    },
    select: (el) => select(el.dataset.id, false),
    evidence: (el) => {
      state.selectedId = el.dataset.id;
      radarMode = "demo";
      save();
      navigate("radar");
    },
    review: (el) => {
      C.setReview(state, state.selectedId, el.dataset.review);
      ensureAlerts();
      log(state.selectedId + " 复核：" + REVIEW[el.dataset.review]);
      render();
      toast("复核状态已按当前批次保存。");
    },
    segment: (el) => {
      state.filters.start = +el.dataset.start;
      state.filters.end = +el.dataset.end;
      save();
      navigate("twin");
    },
    "toggle-task": toggleTask,
    "reset-task": () => {
      haltTask();
      task().progress = 0;
      task().status = "ready";
      log("重置作业进度");
      render();
    },
    "choose-task": (el) => {
      haltTask();
      state.activeTask = el.dataset.id;
      save();
      render();
    },
    "new-task": () =>
      showModal(
        `<h2>新建检测任务</h2><form id="newTaskForm"><label class="field">任务名称<input id="newTaskName" maxlength="80" placeholder="例如：拱顶加密复检" required></label><div class="form-grid" style="margin-top:16px">${field("起点里程 / m", "newStart", 3128, 3128, 3175.9, 0.1)}${field("终点里程 / m", "newEnd", 3176, 3128.1, 3176, 0.1)}</div><div class="actions"><button type="submit" class="primary">创建任务</button></div></form>`,
      ),
    "import-csv": importCSV,
    "use-radar": (el) => {
      const index=Number(el.dataset.id), r=state.radars[index];
      if(!r || r.batchId!==state.batch)throw Error("该数据不属于当前批次。");
      selectedRadar=index;radarMode="import";trace=0;processed=null;render();
    },
    "sample-csv": () => {
      download(
        "雷达矩阵样例.csv",
        C.generateRadar({ rows: 32, cols: 48, seed: 42 })
          .map((r) => r.map((x) => x.toFixed(5)).join(","))
          .join("\r\n"),
        "text/csv",
      );
    },
    "invalid-demo": () => {
      try {
        C.parseCSV("1,2\n3,invalid", {});
      } catch (e) {
        importError = e.message;
        $("#importMessage").innerHTML = note(
          "错误示例已拒绝：" + escape(e.message),
          "warning",
        );
        toast("非法数据被拒绝，未写入目录。");
      }
    },
    "run-preprocess": runPreprocess,
    "cancel-processing": () => {
      processingToken++;
      if (modelController) modelController.abort();
      state.processing.status = "cancelled";
      state.processing.message = "用户取消；已完成结果保持独立版本";
      log("取消处理");
      render();
    },
    "try-model": async () => {
      const r = radarData(),
        name = "RTM",
        batchId = state.batch,
        token = ++processingToken;
      modelController = new AbortController();
      try {
        const output = await window.TunnelAdapters.run(
          name,
          {
            matrix: r.matrix,
            metadata: r.metadata,
            projectId: state.project.id,
            batchId,
            taskId: task().id,
            lineId: chosen().lineId,
            source: r.source,
            parameters: { gain: state.processing.gain || 1 },
          },
          { signal: modelController.signal },
        );
        if (token !== processingToken || state.batch !== batchId) return;
        state.processing = {
          ...state.processing,
          batchId,
          status: "completed",
          inputName: r.name,
          version: output.version,
          source: output.source,
          finishedAt: new Date().toISOString(),
          message:
            "适配器返回完成状态；输出保存在独立算法记录，未自动覆盖病害台账",
          adapterResult: output,
        };
        log("算法适配器完成 " + name);
        render();
      } catch (e) {
        if (token !== processingToken || state.batch !== batchId) return;
        state.processing.status =
          e.name === "AbortError" ? "cancelled" : "failed";
        state.processing.message = e.message;
        state.processing.batchId = batchId;
        log("模型接口：" + e.message);
        render();
        toast(e.message, true);
      }
    },
    adapter: () =>
      showModal(
        `<h2>真实算法接入契约</h2><p>此处说明 RCAN / RTM 雷达处理接口，不会调用影像检测。当前影像检测采用 algorithm/defect_detect.py 的 crack-seg U-Net 独立流程；算法自动写入病害记录暂未接通。接入雷达后端前需核实代码、许可证、权重或求解器以及适用数据。</p><div class="note info">输入：项目 / 批次 / 测线编号、矩阵、单位和标定；模型名称、版本、参数。<br>输出：状态、数据来源、耗时、证据、坐标 / 单位、置信度、量化依据和失败信息。<br>RTM：介电 / 波速、网格、时间步长、边界条件、稳定性与收敛验证。</div><p>详细说明：<a href="真实算法与数据接入说明.md" target="_blank">真实算法与数据接入说明 ↗</a></p>`,
      ),
    "save-rtm": () => {
      const epsilon = Number($("#rtmEpsilon").value),
        dx = Number($("#rtmDx").value),
        dt = Number($("#rtmDt").value);
      if (
        ![epsilon, dx, dt].every((x) => Number.isFinite(x) && x > 0) ||
        epsilon < 1
      )
        throw Error("RTM 参数必须有效且为正，εr≥1。");
      const limit = dx / ((0.299792458 / Math.sqrt(epsilon)) * Math.sqrt(2));
      if (dt > limit)
        throw Error(
          "时间步长超过二维等距网格参考上限 " + fmt(limit, 5) + " ns。",
        );
      Object.assign(state.processing, {
        epsilon,
        dx,
        dt,
        boundary: "CPML",
        message: "参数参考校核通过；尚无 RTM 求解器，不产生物理成像。",
      });
      log("保存 RTM 接口参数");
      render();
      toast("接口参数已保存，参考稳定性校核通过。");
    },
    "apply-ahp": () => {
      const m = state.ahp.map((r) => r.slice());
      $$(".ahp-cell").forEach((el) => {
        const i = +el.dataset.i,
          j = +el.dataset.j;
        if (j > i) {
          m[i][j] = Number(el.value);
          m[j][i] = 1 / m[i][j];
        }
      });
      C.calculateAHP(m);
      state.ahp = m;
      ensureAlerts();
      log("更新 AHP 判断矩阵");
      render();
      toast("AHP 矩阵、评估和风险已更新。");
    },
    "reset-ahp": () => {
      state.ahp = C.createState().ahp;
      save();
      render();
    },
    "save-thresholds": () => {
      const thresholds = [0, 1, 2].map((i) =>
          Number($("#threshold" + i).value),
        ),
        severeDiameter = Number($("#severe").value);
      C.assess({ ...state, thresholds, severeDiameter });
      Object.assign(state, { thresholds, severeDiameter });
      ensureAlerts();
      log("更新风险阈值与严重规则");
      render();
    },
    "new-alert": () => {
      const d = chosen();
      if (
        currentAlerts().some(
          (a) => a.defectId === d.id && a.status !== "closed",
        )
      )
        return toast("该病害已有未关闭预警。");
      state.alerts.push({
        id: "A-manual-" + Date.now(),
        defectId: d.id,
        batch: state.batch,
        status: "new",
        level: d.risk,
        condition: "人工登记待复核",
        createdAt: new Date().toISOString(),
        history: [
          { time: new Date().toISOString(), status: "new", note: "人工新增" },
        ],
      });
      log("为 " + d.id + " 新建预警");
      render();
    },
    "advance-alert": (el) => advanceAlert(el.dataset.id),
    "alert-history": (el) => {
      const a = state.alerts.find((x) => x.id === el.dataset.id);
      showModal(
        `<h2>${a.defectId} 处置轨迹</h2><ol class="timeline">${a.history.map((h) => `<li><time>${new Date(h.time).toLocaleString("zh-CN")}</time><p>${ALERT_STATES[h.status]} · ${escape(h.note)}</p></li>`).join("")}</ol>`,
      );
    },
    "create-maintenance": maintenanceDialog,
    "maintenance-done": (el) => {
      const m = state.maintenance.find((x) => x.id === el.dataset.id);
      m.status = "已登记执行，待专业复检";
      log(m.defectId + " 治理任务登记执行");
      render();
    },
    "sim-tab": (el) => {
      simTab = el.dataset.tab;
      render();
    },
    "export-simulation": () => {
      const entry = state.simulation[simTab];
      if (!entry) return toast("请先运行并保存当前工况。", true);
      download(
        "仿真_" + simTab + ".json",
        JSON.stringify(entry, null, 2),
        "application/json",
      );
    },
    "use-sample": () => {
      const x = state.simulation.sample;
      if (!x) return toast("请先生成并保存样本。", true);
      showModal(
        `<h2>示意样本预览</h2><img style="width:100%" src="${radarImage(x.result.matrix)}" alt="仿真示意样本"><p>参数和种子已经保存。真实雷达目录仅接收解析的原始 CSV，示意样本在仿真模块单独管理。</p>`,
      );
    },
    "export-csv": csvExport,
    "export-json": () => {
      const s = C.exportSnapshot(state);
      download(
        "项目评估_" + state.batch + ".json",
        JSON.stringify(s, null, 2),
        "application/json",
      );
    },
    "export-html": htmlExport,
    "print-report": () => window.print(),
    "import-fe": async () => {
      const f = $("#feFile").files[0];
      if (!f) throw Error("请选择外部有限元 JSON。");
      if (f.size > 1024 * 1024) throw Error("外部结果上限 1 MB。");
      const x = JSON.parse(await f.text());
      for (const key of [
        "solver",
        "modelId",
        "units",
        "assumptions",
        "results",
      ])
        if (x[key] == null) throw Error("外部结果缺少 " + key);
      if (typeof x.results !== "object" || !Object.keys(x.results).length)
        throw Error("results 应为非空对象或数组。");
      state.simulation.external = {
        ...x,
        source: "外部结果，未经本平台验证",
        importedAt: new Date().toISOString(),
      };
      log("导入外部分析 " + String(x.modelId));
      render();
    },
  };
  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action],[data-nav]");
    if (!el) return;
    try {
      if (el.dataset.nav) {
        navigate(el.dataset.nav);
        return;
      }
      const f = actions[el.dataset.action];
      if (f) await f(el);
    } catch (err) {
      toast(err.message, true);
    }
  });
  document.addEventListener("change", async (e) => {
    const el = e.target;
    try {
      if (el.id === "batch") {
        haltTask();
        processingToken++;
        state.processingByBatch[state.batch] = state.processing;
        state.simulationsByBatch[state.batch] = state.simulation;
        state.batch = el.value;
        state.simulation = state.simulationsByBatch[state.batch] || {};
        state.processing = state.processingByBatch[state.batch] || {
          status: "idle",
          batchId: state.batch,
        };
        radarMode = "demo";
        task();
        ensureAlerts();
        log("切换检测批次 " + state.batch);
        processed = null;
        render();
      }
      if (el.dataset.filter) {
        const key = el.dataset.filter,
          value = ["minConfidence", "start", "end"].includes(key)
            ? Number(el.value)
            : el.value;
        const next = { ...state.filters, [key]: value };
        if (
          next.minConfidence < 0 ||
          next.minConfidence > 1 ||
          !Number.isFinite(next.minConfidence) ||
          next.start < 3128 ||
          next.end > 3176 ||
          next.start >= next.end ||
          !Number.isFinite(next.start) ||
          !Number.isFinite(next.end)
        )
          throw Error(
            "筛选范围无效：里程须在3128～3176且起点小于终点，置信度0～1。",
          );
        state.filters = next;
        const visible = C.getDefects(state);
        if (visible.length && !visible.some((x) => x.id === state.selectedId))
          state.selectedId = visible[0].id;
        save();
        render();
      }
      if (el.id === "radarSource") {
        radarMode = el.value === "demo" ? "demo" : "import";
        selectedRadar = Number(el.value) || 0;
        trace = 0;
        processed = null;
        render();
      }
      if (el.id === "roiOverlay") {
        overlayRegions = el.checked;
        drawCanvases();
      }
      if (el.id === "palette") {
        palette = Number(el.value);
        drawCanvases();
      }
      if (el.id === "radarZoom") {
        zoom = Number(el.value);
        drawCanvases();
      }
      if (el.id === "trace") {
        const n = Number(el.value);
        if (!Number.isInteger(n) || n < 0 || n >= (route === "radar" ? radarDemoData() : radarData()).cols)
          throw Error("道号超出有效范围。");
        trace = n;
        drawCanvases();
        if ($("#traceNumber")) $("#traceNumber").textContent = trace;
      }
      if (el.id === "metadata") {
        metadataText = el.value;
      }
      if (el.id === "metadataFile") {
        const f = el.files[0];
        if (f) {
          if (f.size > 65536) throw Error("元数据应小于64 KB。");
          metadataText = await f.text();
          JSON.parse(metadataText);
          $("#metadata").value = metadataText;
        }
      }
      if (el.id === "alpha") {
        state.alpha = Number(el.value);
        ensureAlerts();
        log("更新组合权重 α=" + state.alpha);
        render();
      }
      if (el.dataset.layer) {
        state.scenePrefs[el.dataset.layer] = el.checked;
        save();
        post({ type: "layer", name: el.dataset.layer, visible: el.checked });
      }
      if (el.id === "backgroundImage") {
        const file = el.files[0];
        if (file) {
          if (
            !/^image\/(png|jpeg|webp)$/.test(file.type) ||
            file.size > 2 * 1024 * 1024
          )
            throw Error("请选择2 MB以内的PNG/JPG/WebP图片。");
          const url = await new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(r.result);
            r.onerror = reject;
            r.readAsDataURL(file);
          });
          state.scenePrefs.backgroundImage = url;
          save();
          post({ type: "background", url });
          toast("本地背景图片已应用。");
        }
      }

    } catch (err) {
      toast(err.message, true);
    }
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "opacity") {
      state.scenePrefs.opacity = +e.target.value;
      post({ type: "opacity", value: +e.target.value });
      save();
    }
    if (e.target.id === "cut") {
      state.scenePrefs.cut = +e.target.value === 48 ? null : +e.target.value;
      post({ type: "cut", value: state.scenePrefs.cut });
      save();
    }
  });
  document.addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      if (form.id === "taskForm") {
        haltTask();
        const t = task(),
          next = {
            ...t,
            start: +$("#taskStart").value,
            end: +$("#taskEnd").value,
            speed: +$("#taskSpeed").value,
            spacing: +$("#taskSpacing").value,
            scanStart: +$("#scanStart").value,
            scanEnd: +$("#scanEnd").value,
            obstacle: $("#taskObstacle").checked,
            obstacleX: +$("#obstacleX").value,
            progress: 0,
            status: "ready",
          };
        C.validateTask(next, state.project);
        window.TunnelEvidenceCore.remapRange(state,evidenceContext(),{...evidenceContext(),start:next.start,end:next.end});
        Object.assign(t, next);
        log("更新作业参数并重置进度");
        render();
        toast("作业参数已同步到三维场景。");
      }
      if (form.id === "newTaskForm") {
        const t = C.validateTask(
          {
            id: "T-" + Date.now(),
            name: $("#newTaskName").value,
            start: +$("#newStart").value,
            end: +$("#newEnd").value,
            speed: 0.6,
            spacing: 0.1,
            scanStart: -90,
            scanEnd: 90,
            status: "ready",
            progress: 0,
            batch: state.batch,
          },
          state.project,
        );
        haltTask();
        state.tasks.push(t);
        state.activeTask = t.id;
        log("新建检测任务 " + t.name);
        closeModal();
        render();
      }
      if (form.id === "simulationForm") runSimulation();
      if (form.id === "maintenanceForm") {
        const name = $("#maintenanceName").value.trim();
        if (!name) throw Error("请输入任务名称。");
        state.maintenance.push({
          id: "M-" + Date.now(),
          batch: state.batch,
          defectId: state.selectedId,
          name,
          priority: $("#maintenancePriority").value,
          date: $("#maintenanceDate").value,
          status: "待执行",
        });
        log("创建治理任务 " + state.selectedId);
        closeModal();
        navigate("alerts");
      }
      if (form.id === "recheckForm") {
        const text = $("#recheckNote").value.trim();
        if (text.length < 5) throw Error("请填写至少5字的复检证据说明。");
        const a = state.alerts.find((x) => x.id === form.dataset.id);
        a.status = "closed";
        a.history.push({
          time: new Date().toISOString(),
          status: "closed",
          note: text,
        });
        log(a.defectId + " 登记复检并关闭预警");
        closeModal();
        render();
      }
    } catch (err) {
      toast(err.message, true);
    }
  });
  window.addEventListener("message", (e) => {
    const frame = $("#twinFrame");
    if (
      !frame ||
      e.source !== frame.contentWindow ||
      (location.protocol === "file:"
        ? !["null", "file://"].includes(e.origin)
        : e.origin !== location.origin)
    )
      return;
    const m = e.data;
    if (!m || m.channel !== "slzj") return;
    if (m.type === "ready") {
      syncScene();
      if (pendingFocus) {
        post({ type: "select", id: pendingFocus });
        pendingFocus = null;
      }
    }
    if (m.type === "toggle-task") toggleTask();
    if (m.type === "selected" && evidenceActive()) selectEvidence(m.id,false);
    else if (m.type === "selected" && m.id !== state.selectedId) select(m.id, true);
  });
  window.addEventListener("keydown", (e) => {
    if (
      e.ctrlKey ||
      e.metaKey ||
      e.altKey ||
      e.repeat ||
      e.target.closest("input,textarea,select") ||
      !$("#twinFrame") ||
      $("#modal").open
    )
      return;
    const key = e.key.toLowerCase();
    if (key === "p") {
      e.preventDefault();
      toggleTask();
    }
    if (key === "f") {
      e.preventDefault();
      sceneMode("roam");
    }
    if (key === "g") {
      e.preventDefault();
      sceneMode("fly");
    }
  });
  window.addEventListener("hashchange", () => {
    render();
    if (pendingAnchor) {
      const id = pendingAnchor;
      requestAnimationFrame(() => {
        document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
        pendingAnchor = "";
      });
    } else window.scrollTo(0, 0);
  });
  document.addEventListener("tunnel-monitor-state", (event) => {
    const capture = $("#taskCaptureVideo");
    if (capture) { const identity = window.TunnelVideoMonitor.evidenceIdentity(); capture.disabled = !(identity.ready && identity.sourceId); }
    if (route === "tasks") $("#dataBadge").textContent = evidenceActive() ? "现场标注 · 相对位置对应" : event.detail.active
      ? "外部视频 · 任务仍为仿真" : "演示数据";
  });
  const syncTopbarState = () =>
    document.body.classList.toggle("is-scrolled", window.scrollY > 8);
  window.addEventListener("scroll", syncTopbarState, { passive: true });
  syncTopbarState();
  window.addEventListener("beforeunload", () => {
    if (taskRunning) {
      task().status = "paused";
      save();
    }
  });
  function setupMobileNavigation() {
    const sidebar = $(".sidebar");
    if (!sidebar || $("#sidebarToggle")) return;
    const toggle = document.createElement("button");
    toggle.id = "sidebarToggle";
    toggle.className = "sidebar-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-label", "打开导航");
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = "<span></span><span></span><span></span>";
    toggle.addEventListener("click", () => {
      const open = document.body.classList.toggle("sidebar-open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
    });
    document.body.insertBefore(toggle, document.body.firstChild);
    document.addEventListener("click", (event) => {
      if (window.innerWidth > 767 || !document.body.classList.contains("sidebar-open"))
        return;
      if (event.target.closest(".nav-item")) {
        document.body.classList.remove("sidebar-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "打开导航");
      }
    });
  }
  setupMobileNavigation();
  $("#helpButton").onclick = () => {
    guideDialog();
  };
  ensureAlerts();
  render();
  if (persistenceError) toast(persistenceError, true);
  // 只读验收出口：没有网络调用；导出的业务快照和页面使用同一计算核心。
  window.SLZJ = {
    snapshot: () => {
      const s = C.exportSnapshot(state);
      s.evidenceRecords=JSON.parse(JSON.stringify((state.evidenceRecords||[]).filter(r=>r.batchId===state.batch)));
      s.evidenceSourceFilter=state.evidenceSourceFilters[window.TunnelEvidenceCore.key(evidenceContext())]||"all";s.evidenceContext=evidenceContext();s.evidenceMode=window.TunnelEvidenceCore.mode(state,evidenceContext());
      return s;
    },
    version: "2.0",
  };
  function alertsPage() {
    const ads = currentAlerts(),
      ds = all();
    return (
      heading(
        "预警与治理",
        "预警确认、治理处置、复检和关闭形成可追踪的本地闭环。",
        "03 / 对应计划第 3 部分",
        btn("为选中病害创建预警", "new-alert", "primary"),
      ) +
      moduleTabs() +
      `<div class="metrics">${metric("待确认", ads.filter((a) => a.status === "new").length, "条", "规则触发或人工新增", "alerts", "amber")}${metric("处置中", ads.filter((a) => ["confirmed", "processing"].includes(a.status)).length, "条", "确认后纳入治理", "alerts")}${metric("待复检", ads.filter((a) => a.status === "recheck").length, "条", "补录复检证据后关闭", "alerts", "blue")}${metric("已关闭", ads.filter((a) => a.status === "closed").length, "条", "保留完整操作轨迹", "alerts", "accent")}</div>${panel(
        "当前批次预警",
        ads.length
          ? `<div class="table-wrap"><table><thead><tr><th>预警 / 关联病害</th><th>位置</th><th>触发依据</th><th>风险 / 状态</th><th>处置</th></tr></thead><tbody>${ads
              .map((a) => {
                const d = ds.find((d) => d.id === a.defectId);
                return `<tr><td>${a.defectId}<br><small class="muted">${escape(a.id)}</small></td><td>${d ? mile(d.mileage) : "—"}</td><td>${escape(a.condition)}<br><small class="muted">${a.ruleActive ? "当前规则仍触发" : "当前规则未触发 · 保留历史"}</small></td><td>${risk(a.level)}<br>${ALERT_STATES[a.status]}</td><td>${a.status !== "closed" ? btn(ALERT_STATES[NEXT[a.status]], "advance-alert", "small primary", `data-id="${a.id}"`) : '<span class="accent">闭环完成</span>'} ${btn("轨迹", "alert-history", "small subtle", `data-id="${a.id}"`)} ${btn("定位", "select", "small subtle", `data-id="${a.defectId}"`)}</td></tr>`;
              })
              .join("")}</tbody></table></div>`
          : '<div class="empty">当前批次暂无预警。</div>',
        '<span class="badge">本地持久化</span>',
        false,
      )}<div class="grid-2">${panel(
        "治理任务",
        state.maintenance.filter((x) => x.batch === state.batch).length
          ? state.maintenance
              .filter((x) => x.batch === state.batch)
              .map(
                (m) =>
                  `<div class="alert-row"><div><b>${m.defectId} · ${escape(m.name)}</b><p>${escape(m.priority)} · 计划 ${escape(m.date)} · ${escape(m.status)}</p></div>${btn("登记执行", "maintenance-done", "small", `data-id="${m.id}"`)}</div>`,
              )
              .join("")
          : '<div class="empty">在病害详情中点击“加入治理”，设置优先级和计划日期。</div>',
      )}${panel("处置原则", note("建议先专项复检，核实空洞范围与衬砌状态，再确定注浆、修复或持续监测方案。涉及停运、限速等重大措施须由专业人员复核。", "warning") + `<div class="actions"><a href="#simulation">比较运维方案 →</a><a href="#reports">生成处置报告 →</a></div><p class="muted" style="font-size:11px">演示评分不会因“点击完成”直接变好。复检结果应作为独立证据记录，方案预测效果单独展示。</p>`)}</div>`
    );
  }
  function simulationPage() {
    const conf = state.simulation[simTab]?.params || {};
    const tabsHtml = `<div class="pill-tabs">${[
      ["sample", "01 雷达样本"],
      ["vehicle", "02 作业验证"],
      ["structure", "03 结构响应"],
      ["plans", "04 运维方案"],
    ]
      .map(([k, n]) =>
        btn(
          n,
          "sim-tab",
          simTab === k ? "active" : "subtle",
          `data-tab="${k}"`,
        ),
      )
      .join("")}</div>`;
    let content = "";
    if (simTab === "sample") {
      content = `<div class="grid-main"><div>${panel("可复现雷达样本", `<div class="radar-wrap"><canvas id="simRadar" style="height:350px" aria-label="仿真示意雷达样本"></canvas></div><div class="radar-caption">程序示意信号 · 高斯调制正弦 / 抛物线纹理 · 非电磁求解器结果</div>${note("保存参数、种子和示意标签，支持复现实验。介电与几何参数仅控制纹理变化，不可据此反演工程尺度。", "info")}<div class="actions">${btn("导出样本与参数 JSON", "export-simulation", "primary")}${btn("作为示意输入预览", "use-sample", "subtle")}</div>`)} </div><aside>${panel("生成参数", `<form id="simulationForm"><div class="form-grid">${field("衬砌厚度 / m", "thickness", conf.thickness ?? 0.35, 0.05, 1, 0.01)}${field("介电参数 εr", "epsilon", conf.epsilon ?? 6, 1, 30, 0.5)}${field("钢筋间距 / m", "rebarSpacing", conf.rebarSpacing ?? 0.2, 0.02, 1, 0.02)}${field("示意埋深 / m", "depth", conf.depth ?? 0.2, 0.01, 0.8, 0.01)}${field("示意直径 / m", "diameter", conf.diameter ?? 0.3, 0.01, 2, 0.01)}${field("横向位置 / 比例", "voidPosition", conf.voidPosition ?? 0.55, 0.05, 0.95, 0.05)}${field("噪声幅度", "noise", conf.noise ?? 0.15, 0, 1, 0.05)}${field("随机种子", "seed", conf.seed ?? 20260928, 0, 4294967295, 1)}</div><div class="actions"><button class="primary" type="submit">生成 / 更新样本</button></div></form>`)} </aside></div>`;
    } else if (simTab === "vehicle") {
      const v =
        state.simulation.vehicle?.result || C.simulateVehicle(taskParams());
      content = `<div class="grid-main"><div>${panel("车辆与作业验证", scene(true), '<span class="badge">与检测任务共用场景</span>', false)}${panel("验证摘要", `<div class="grid-3"><div><div class="stat-label">所选环向覆盖率</div><div class="big-number">${fmt(v.coverage)}<small>%</small></div></div><div><div class="stat-label">有效作业时间</div><div class="big-number">${fmt(v.duration)}<small>s</small></div></div><div><div class="stat-label">实际碰撞事件</div><div class="big-number">${v.collision ? 1 : 0}<small>次</small></div></div></div><p class="muted">${escape(v.formula)}</p>${note(escape(v.assumptions), "info")}<p>${v.missed.length ? v.missed.map((m) => "漏检 " + fmt(m.start) + "—" + fmt(m.end) + " m：" + m.reason).join("<br>") : "规划范围内无采样空隙（简化几何假设）。"}</p>${btn("导出验证摘要", "export-simulation", "small")}`)}</div><aside>${panel("验证工况", `<form id="simulationForm"><div class="form-grid">${field("速度 / m·s⁻¹", "speed", task().speed, 0.05, 5, 0.05)}${field("扫描间距 / m", "spacing", task().spacing, 0.01, 2, 0.01)}${field("障碍位置 / m", "obstacle", task().obstacleX ?? 24, 2, 46, 0.5)}<label class="field">中心线障碍<span><input type="checkbox" id="simObstacle" ${task().obstacle ? "checked" : ""}> 加入障碍物</span></label></div><div class="actions"><button class="primary" type="submit">计算并应用到场景</button>${btn("播放 / 暂停", "toggle-task", "subtle")}</div></form><p class="muted" style="font-size:11px">车辆 2.4 × 1.4 m，障碍 1 × 1 m，安全距离 0.3 m。计算后同步到检测任务；直行并提前停止。</p>`)}</aside></div>`;
    } else if (simTab === "structure") {
      const r = state.simulation.structure?.result || C.simulateStructure({});
      content = `<div class="grid-main"><div>${panel("结构响应参数敏感性", `<div class="grid-2"><div><div class="stat-label">径向收缩位移</div><div class="big-number accent">${fmt(r.displacement, 4)}<small>mm</small></div></div><div><div class="stat-label">环向压应力</div><div class="big-number">${fmt(r.stress, 4)}<small>MPa</small></div></div></div><svg viewBox="0 0 620 230" width="100%" height="230" role="img" aria-label="均匀径向压力作用下闭合圆环示意"><circle cx="280" cy="115" r="78" fill="none" stroke="#314b64" stroke-width="18"/><circle cx="280" cy="115" r="74" fill="none" stroke="#56d9b1" stroke-dasharray="5 5" stroke-width="2"/><path d="M280 8v22m-5-6 5 6 5-6 M280 222v-22m-5 6 5-6 5 6 M170 115h24m-6-5 6 5-6 5 M390 115h-24m6-5-6 5 6 5" stroke="#72afff" fill="none" stroke-width="2"/><text x="410" y="110" fill="#9ab3c7" font-size="12">连续闭合圆环</text><text x="410" y="135" fill="#6c91a2" font-size="11">虚线仅示意径向收缩</text></svg><p class="code">${escape(r.formula)}</p>${note(escape(r.assumptions), "warning")}${r.warning ? note(r.warning, "warning") : ""}<div class="actions">${btn("导出分析摘要", "export-simulation", "small")}</div>`)}${panel("外部有限元结果", `<p class="muted">导入 JSON：必须含 solver、modelId、units、assumptions、results，平台只展示外部结果，不替代复核。</p><input type="file" id="feFile" accept=".json" aria-label="导入外部有限元结果">${btn("解析外部结果", "import-fe", "small")}<div class="code" id="feStatus">${state.simulation.external ? escape(JSON.stringify(state.simulation.external, null, 2)) : "尚未导入"}</div>`)}</div><aside>${panel("材料、几何与荷载", `<form id="simulationForm"><div class="form-grid">${field("衬砌厚度 / m", "thickness", conf.thickness ?? 0.35, 0.05, 2, 0.01)}${field("弹性模量 / GPa", "E", conf.E ?? 30, 1, 200, 1)}${field("均匀外压 / kPa", "load", conf.load ?? 100, 0, 10000, 10)}${field("平均半径 / m", "radius", conf.radius ?? 2.7, 0.5, 30, 0.1)}</div><div class="actions"><button class="primary" type="submit">重新计算</button></div></form><p class="muted" style="font-size:11px">连续圆环、均匀径向外压、自由径向收缩、线弹性小变形。未计接缝、土体和局部病害，不给出承载力鉴定。</p>`)}</aside></div>`;
    } else {
      const ps = state.simulation.plans?.result || C.comparePlans({}),
        a = C.assess(state);
      content = `<div class="grid-main"><div>${panel("运维方案比较", `<div class="table-wrap"><table><thead><tr><th>排序 / 方案</th><th>预算 / 万元</th><th>工期 / 天</th><th>预期降险</th><th>综合分</th></tr></thead><tbody>${ps.map((p) => `<tr><td>${p.rank}. ${p.name}<br><small class="${p.recommended ? "accent" : "muted"}">${p.recommended ? "预算内首选 · " : ""}${p.reason}</small></td><td>${p.cost}</td><td>${p.duration}</td><td>${p.reduction}%</td><td>${p.score}</td></tr>`).join("")}</tbody></table></div><div class="panel-body">${note("成本、工期及降险比例均为可比较的演示假设，未基于实际报价和工程效果标定。推荐需专业复核。", "warning")}<p class="muted">${ps[0].affordable ? "首选方案交通影响：" + ps[0].traffic : "当前预算内没有可推荐方案。"}</p><p class="code">当前风险负担 R=100−SHI=${fmt(100 - a.shi)}；假设方案后 R′=R×(1−预期降险比例)。</p>${ps[0].affordable ? `<p>方案前 / 后示意风险负担：<b>${fmt(100 - a.shi)} → ${fmt((100 - a.shi) * (1 - ps[0].reduction / 100))}</b>，实际 SHI 台账不因预测被改写。</p>` : ""}${btn("导出方案比较", "export-simulation", "small")}</div>`, "", false)}</div><aside>${panel("预算与评价偏好", `<form id="simulationForm"><div class="form-grid">${field("预算上限 / 万元", "budget", conf.budget ?? 60, 0, 100000, 1)}${field("成本权重", "costWeight", conf.weights?.[0] ?? 0.3, 0, 1, 0.05)}${field("工期权重", "timeWeight", conf.weights?.[1] ?? 0.2, 0, 1, 0.05)}${field("降险权重", "riskWeight", conf.weights?.[2] ?? 0.5, 0, 1, 0.05)}</div><div class="actions"><button class="primary" type="submit">更新排序</button></div></form><p class="muted" style="font-size:11px">权重自动归一化。先按预算可行性，再按组合得分排序；至少一个权重大于 0。</p>`)}</aside></div>`;
    }
    return (
      heading(
        "仿真验证工作台",
        "在明确假设下调整参数，比较可复现结果，为后续实测与工程验证准备依据。",
        "04 / 对应计划第 4 部分",
      ) +
      tabsHtml +
      content
    );
  }
})();
