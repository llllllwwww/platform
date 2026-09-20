# 隧道病害智能检测 —— 底层算法

本目录是"隧雷智检 · 隧道结构三维数字孪生平台"的**底层深度学习算法**部分。
前端（仓库根目录的单文件 HTML）只做可视化演示，其中的病害台账是手工构造的演示数据；
真正的"检测数据 → 病害识别结果"这一段在这里实现。

项目计划书的解译算法链是 **RCAN + RTM + YOLOv7-AFPN**，本目录是它的工程实现与实验场。

---

## 目录结构

```
algorithm/
├── README.md                        # 本文件：数据来源、环境、训练验证全流程
├── requirements.txt
├── configs/
│   ├── yolo11s-tsd-afpn.yaml        # 隧道表观病害检测（Detect 头）
│   ├── yolo11s-tsd-afpn-seg.yaml    # 隧道表观病害分割（Segment 头，伪标注器）
│   ├── yolo11s-gpr-afpn.yaml        # GPR 分支检测（保留）
│   ├── yolo11n-gpr-afpn.yaml        # GPR 分支轻量版
│   └── constraints.txt              # 锁定 torch 版本，防止被换成 CPU 版
├── docs/
│   └── REFERENCES.md                # 算法链对应论文 + 同类工作指标对照 + 数据集索引
├── scripts/
│   ├── download_data.py             # 一键下载全部数据集（含来源链接与校验）
│   └── run_pipeline.ps1             # 从下载到训练验证的一键流程
└── src/
    ├── afpn.py                      # AFPN 模块（官方实现等价移植，纯 PyTorch）
    ├── detector.py                  # 模型接线 / COCO 主干迁移 / parse_model 包装
    ├── train.py                     # 训练 + 验证 + 测试（detect / segment 双任务）
    ├── infer.py                     # 推理：单图 / 图片目录 / 视频
    ├── build_scene_split.py         # 真实隧道照片切分清单（按帧块分组，防泄漏）
    ├── build_dataset_seg.py         # TTD/CTCD 像素掩码 -> YOLO-seg 数据集
    ├── pseudo_label.py              # 分割器伪标注 -> 检测数据集
    ├── sanity_check.py              # 标注可视化抽检（自动识别检测/分割格式）
    ├── build_dataset.py             # GPR 分支：B-scan 数据集构建
    └── make_video.py                # GPR 分支：真实剖面合成滚动雷达视频
```

---

## 一、环境准备

已在 Windows + Python 3.13 + RTX 5060(8GB) 上验证。

```powershell
# 1) 创建虚拟环境（也可复用已有环境）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 先装 GPU 版 torch（必须走官方 CUDA 源）
pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128

# 3) 再装其余依赖，并用 constraints 锁住 torch
pip install -r requirements.txt -c configs/constraints.txt

# 4) 自检：确认能用 GPU
python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

> ⚠️ `constraints.txt` 不能省。否则 ultralytics 的依赖解析会把 GPU 版 torch
> 换成 CPU 版轮子，训练**静默掉到 CPU**，速度差几十倍且不报错。

> ⚠️ 训练/推理要在普通终端里跑（不要放在受限沙箱环境）：ultralytics 需要写
> 自己的配置目录与字节码缓存，权限被拒会中断。

---

## 二、数据集

全部数据由 `scripts/download_data.py` 一键拉取（约 2.6 GB，可重复执行、自动跳过已下载）：

```powershell
python scripts\download_data.py            # 下载全部
python scripts\download_data.py --list      # 只打印来源清单
python scripts\download_data.py --verify    # 校验文件数
```

### 2.1 主线数据：真实隧道衬砌现场照片（无框）

| 项 | 内容 |
|---|---|
| 名称 | Tunnel_lining_multi-category_defect_segmentation_detection |
| 页面 | https://github.com/cuijingqi/Tunnel_lining_multi-category_defect_segmentation_detection |
| 论文 | *The hybrid segmentation method and application of KNet-UperNet-SwinL in multi-category defect segmentation detection of tunnel lining* |
| 体积 | 约 1.8 GB |
| 内容 | **真实隧道衬砌现场彩色照片 17338 张**：裂缝 11375 / 渗漏 3980 / 无病害 1958 / 混合样例 25 |
| 特点 | 隧道内封闭空间、低照度；含管片接缝、螺栓盒、电缆桥架、钢筋外露，部分带卷尺作尺度参照 |
| ⚠️ 限制 | **只有文件夹级标签，没有检测框和掩码** |

### 2.2 伪标注器训练数据：带像素级掩码

| 数据集 | 页面 | 内容 | 许可 |
|---|---|---|---|
| TACK Tunnel Data (TTD) | https://huggingface.co/datasets/TACK-project/TACK_Tunnel_Data | 3774 张 512×512 真实隧道衬砌图 + 像素掩码（40=Crack / 160=Water / 200=Leaching）；三座隧道：硬岩公路隧道 / 预制混凝土 / 钻爆法喷混 | CC BY-NC-ND 4.0 |
| CTCD | https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset | 52 座公路隧道巡检车采集的裂缝图 + 二值掩码（train 250 / val 42） | 见数据集说明 |

> TTD 的图是 512×512 近景切片，**不适合当最终训练集**（看不出隧道场景），
> 但它带像素级标注，正好用来训练"标注器"——这是它在本项目中的正确用法。

### 2.3 GPR 分支数据（保留，非当前主线）

| 数据集 | 页面 | 内容 | 许可 |
|---|---|---|---|
| Mendeley GPR 实测数据 | https://data.mendeley.com/datasets/ww7fd9t325/1 | 2239 张 GPR 实测 B-scan（空洞 / 管线，含 YOLO 与 VOC 标注）+ 285 条原始剖面，GSSI 200/400 MHz | CC BY-NC |

### 2.4 其他可备选数据源（需账号或需向作者索取）

| 数据 | 说明 |
|---|---|
| Roboflow Universe `tunnel crack` / `tunnel defect` 系列 | 大量手机拍摄的隧道病害场景图，带框；下载需免费账号的 API Key |
| Kaggle `gpr-normal-and-tunnel-anomaly-dataset` | 19743 条正常剖面 + 1600 个标注窗口 |
| RTSD（铁路隧道剥落） | 8092 张激光强度/深度图，*Sensors* 21(17):5725；需向作者索取 |
| 国家基础学科公共科学数据中心「路桥隧」 | 216 项资源，需申请 |
| MERL-GPR（Zenodo 8145084） | 1.6 GB gprMax 全波场仿真数据，用于成像/反演实验 |

---

## 三、为什么需要"伪标注"这一环

主线数据有 17338 张真实照片，但**没有框**；而公开可直链下载的、带框的整场景隧道病害
数据集基本不存在（已在 Hugging Face 65 个候选、GitHub 50 个仓库中逐个核查）。
所以采用两段式：

```
带像素掩码的数据（TTD + CTCD）
        │  训练
        ▼
   AFPN-Seg 分割器  ──推理──▶  17338 张真实照片  ──▶  伪检测框
                                                        │  训练
                                                        ▼
                                                  AFPN 检测器
```

**关键工程决策**：伪标注取**框头**输出而不是掩码头。
实测中裂缝是 1~2 像素宽的细长结构，掩码 IoU 对半个像素偏移就崩掉，
`mask mAP50` 长期接近 0，而 `box mAP50` 正常上升——伪标注只需要"病害在哪"，框足够。

---

## 四、切分规则（防数据泄漏）

隧道巡检数据多是**连续帧**，相邻帧几乎相同；TTD 也是同一原始帧切出的多张视角图。
若随机切分，近乎重复的图会同时进入训练集和验证集，指标虚高。因此：

- **场景照片**：按 `(采集批次前缀, 帧号 // 25)` 分块切分，同序列连续帧留在同一集合
- **TTD / CTCD**：按 **源帧 / 源图** 分组切分

`build_scene_split.py` 结束时会自检"跨集合泄漏的帧块数"，不为 0 直接报错退出。

---

## 五、训练与验证

### 5.1 一键流程

```powershell
.\scripts\run_pipeline.ps1                      # 全流程
.\scripts\run_pipeline.ps1 -SkipDownload        # 跳过数据下载
.\scripts\run_pipeline.ps1 -Stage seg           # 只训练伪标注器
```

### 5.2 分步执行

```powershell
# ① 下载数据（约 2.6 GB）
python scripts\download_data.py

# ② 建立场景照片切分清单（按帧块分组 + 泄漏自检）
python src\build_scene_split.py

# ③ 构建分割数据集（TTD+CTCD 掩码 -> YOLO-seg，含自检）
python src\build_dataset_seg.py

# ④ 训练伪标注器（分割）
python src\train.py --task segment --data data\processed\ttd_seg\data.yaml `
                    --imgsz 512 --batch 16 --epochs 150 --workers 4 --device 0

# ⑤ 伪标注：给 17338 张真实照片生成检测框（conf 放低以保召回）
python src\pseudo_label.py --weights runs\segment\afpn-seg\weights\best.pt `
                           --imgsz 640 --conf 0.15 --device 0

# ⑥ 训练检测器 + 验证 + 测试集评估
python src\train.py --task detect --data data\processed\scene_det\data.yaml `
                    --imgsz 640 --batch 16 --epochs 150 --workers 4 --device 0 --test

# ⑦ 推理（单图 / 图片目录 / 视频三种输入都支持）
python src\infer.py --weights runs\detect\afpn-det\weights\best.pt `
                    --source data\processed\scene_det\images\val --montage
python src\infer.py --weights runs\detect\afpn-det\weights\best.pt --source <视频文件.mp4>
```

### 5.3 产物位置

| 产物 | 位置 |
|---|---|
| 指标汇总（json） | `reports/metrics_<name>.json` |
| PR 曲线 / 混淆矩阵 / 训练曲线 | `runs/<task>/<name>/` |
| 标注与伪标注抽检图 | `reports/sanity_labels*.png`、`reports/pseudo_label_preview.png` |
| 最优权重 | `runs/<task>/<name>/weights/best.pt` |

---

## 六、模型说明

### 6.1 AFPN（颈部创新点）

论文：Yang et al., *AFPN: Asymptotic Feature Pyramid Network for Object Detection*,
arXiv:2306.15988。官方代码 <https://github.com/gyyang23/AFPN>。

相对常规 FPN/PAN 的两点不同：

- **渐进融合**：先从两个相邻的低层特征开始融合，再逐步把高层特征纳入，
  避免非相邻层级之间语义差距过大导致的信息丢失
- **自适应空间融合（ASFF）**：用 1×1 卷积为每个尺度生成逐像素权重再 softmax，
  缓解多目标在同一空间位置上的信息冲突

`src/afpn.py` 是官方 mmyolo 实现的**纯 PyTorch 等价移植**（数学结构一致，去掉了
OpenMMLab 依赖）。不用官方仓库的原因：它依赖 mmcv/mmdet，而 mmcv 没有
Python 3.13 + torch 2.11 的预编译轮子，本机也没有 CUDA toolkit，无法从源码编译。

### 6.2 与项目计划书的差异

| 项 | 计划书 | 本实现 | 说明 |
|---|---|---|---|
| 检测器 | YOLOv7-AFPN | **YOLO11-AFPN** | 官方 yolov7 停留在 torch 1.x 时代（`torch.load` 默认 `weights_only` 变更、numpy 2 不兼容），在当前环境没有可用的预训练权重与稳定训练路径。**AFPN 这个创新点不变**；`src/afpn.py` 与检测器解耦，后续可整体移植到 YOLOv7 |
| 主干预训练 | — | COCO 预训练权重迁移 | 小数据集上收敛快得多 |

### 6.3 模型结构与接线

AFPN 是"多输入、多输出"层（吃 P3/P4/P5，吐出三路融合特征），而 Ultralytics 的
`parse_model` 对未知模块只支持单输入（`f` 写成列表时直接抛 TypeError）。
`src/detector.py` **不修改 Ultralytics 源码**，只在 `parse_model` 调用前后各改写一次：
解析前把多输入列表临时改成最后一个输入，解析后还原真实的多输入，
并把被引用的前置层补进 `save`（否则前向时这些层不会被缓存，AFPN 取不到输入）。

模型图（`configs/yolo11s-tsd-afpn.yaml`）：

```
backbone 0..10   (官方 YOLO11s 主干)
  4  -> P3  256ch
  6  -> P4  256ch
  10 -> P5  512ch
head
  11  [[4,6,10], 1, AFPN, [[256,256,512], 256, 3]]
  12  [11, 1, Index, [256, 0]]   -> P3 融合特征
  13  [11, 1, Index, [256, 1]]   -> P4 融合特征
  14  [11, 1, Index, [256, 2]]   -> P5 融合特征
  15  [[12,13,14], 1, Detect, [nc]]
```

| 配置 | 头 | 类别 | 参数量 | GFLOPs |
|---|---|---|---|---|
| `yolo11s-tsd-afpn.yaml` | Detect | crack / water / leaching | 8.92 M | 28.0 (320²) |
| `yolo11s-tsd-afpn-seg.yaml` | Segment | 同上 | 9.99 M | 43.6 (512²) |
| `yolo11s-gpr-afpn.yaml` | Detect | cavity / utility（GPR 分支） | 8.92 M | 28.0 (320²) |

> ⚠️ YAML 文件名必须以 `yolo11s` / `yolo11n` 结尾（`yolo11` + 尺度字母）。
> Ultralytics 靠文件名推断 scale，scale 决定主干真实通道数；
> AFPN 那行写死的通道数（256/256/512）必须与之匹配，否则前向直接报错。

### 6.4 类别定义

| class id | 名称 | 对应平台病害类型 | 来源 |
|---|---|---|---|
| 0 | crack | 裂缝 | TTD 掩码值 40 + CTCD |
| 1 | water | 充水 / 富水（表观为湿渍、渗水） | TTD 掩码值 160 |
| 2 | leaching | 渗漏析出物（泛碱） | TTD 掩码值 200 |

主线照片的 `leakage` 文件夹是"渗漏"大类，会被伪标注器细分成 `water` 与 `leaching`。

---

## 七、指标

### 7.1 隧道表观病害（主线）

| 模型 | 输入 | epoch | val mAP50 | val mAP50-95 | recall | 备注 |
|---|---|---|---|---|---|---|
| AFPN-Seg（伪标注器） | 512 | 101/150 | 0.198 (box) | 0.088 (box) | 0.309 | 训练进行中；mask mAP50 ≈ 0.008（细裂缝 IoU 敏感） |
| AFPN-Det（最终检测器） | 640 | 待跑 | | | | 训练在伪标注框上 |

### 7.2 GPR 分支

| 模型 | 输入 | epoch | val mAP50 | val mAP50-95 | test mAP50 |
|---|---|---|---|---|---|
| YOLO11s-AFPN | 320 | 2（冒烟自检） | 0.225 | 0.071 | 0.155 |
| YOLO11s-AFPN | 320 | 59（中断，数据集方向调整） | 0.666 | 0.329 | — |

---

## 八、已知限制与后续工作

1. **检测器是在伪标注框上训练的**，其 mAP 衡量的是"与伪标注器的一致性"，
   **不等于与人工标注的一致性**。上线前必须人工抽检（`reports/pseudo_label_preview.png`
   提供抽样可视化），建议人工修正 200~300 张作为可信验证集。
2. **裂缝是细长结构**，虽然检测框可用，但要做病害几何量化（长度/宽度/面积）
   必须改用分割头——TTD 掩码已就位，可直接训练。
3. **场景级标注缺失**：当前真实照片多为近景/中景。设备端实际采集的是整幅隧道场景，
   建议后续补采现场数据，用 `src/infer.py` 直接推理验证域迁移效果。
4. 后续阶段：RCAN 双层钢筋网杂波抑制、RTM 逆时偏移成像（对应文献见 `docs/REFERENCES.md`）。

---

## 九、引用

```bibtex
@article{yang2023afpn,
  title={AFPN: Asymptotic Feature Pyramid Network for Object Detection},
  author={Yang, Guoyu and Lei, Jie and Zhu, Zhikuan and Cheng, Siyu and Feng, Zunlei and Liang, Ronghua},
  journal={arXiv preprint arXiv:2306.15988},
  year={2023}
}

@article{sjolander2025ttd,
  title={TACK Tunnel Data (TTD): A Benchmark Dataset for Deep Learning-Based Defect Detection in Tunnels},
  author={Sj{\"o}lander, Andreas and Belloni, Valeria and Fekadu, Robel and Nascetti, Andrea},
  journal={arXiv preprint arXiv:2512.14477},
  year={2025}
}

@article{mojahid2024intelligent,
  title={Intelligent recognition of subsurface utilities and voids: A ground penetrating radar dataset for deep learning applications},
  author={Mojahid, Abdelaziz and El Ouai, Driss and El Amraoui, Khalid and El-Hami, Khalil and Aitbenamer, Hamou},
  journal={Data in Brief},
  year={2025},
  doi={10.17632/ww7fd9t325.1}
}
```

论文与同类工作的完整对照（含 RCAN、RTM、隧道病害检测各方法的指标）见
[`docs/REFERENCES.md`](docs/REFERENCES.md)。