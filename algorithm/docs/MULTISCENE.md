# 多相机视频建模与裂缝候选识别：实测与复现

本轮处理的是实体小车所需的相机数据链路。使用四组真实素材检验不同成像与运动条件，完成选帧、相机恢复、稠密表面、裂缝候选、三维定位及邻帧关联。实际输出的统一入口为 [离线结果页](../web/multiscene/index.html)，数值记录由 [汇总脚本](../summarize_multiscene.py) 从产物生成，见 [实测 JSON](multiscene_metrics.json)。图像、视频、权重与大型重建产物留在本机，源码仓库不包含这些文件。

## 1. 素材与适用边界

| 素材 | 相机/场景条件 | 本次输入 | 相机处理 |
|---|---|---|---|
| DVP 人行隧道 | Pixel 2 手持、明暗变化、涂写 | 1080×1920，19.89 s | SIMPLE_RADIAL，自估内参 |
| 铁路长片 | 前向运动、灯光与设备边缘 | 1280×720，27.73 s | SIMPLE_RADIAL，自估内参；硬件未知 |
| 铁路短片 | 更短的前向行进片段、相邻帧重叠困难 | 1280×720，13.17 s | SIMPLE_RADIAL，自估内参；硬件未知 |
| TUM VI corridor4 | 已标定单色鱼眼、低纹理走廊 | cam0，512×512，原序列 35–65 s，600 帧 | 固定 OPENCV_FISHEYE，原始标定主点加 0.5 |

这是四组素材、三类采集条件，不是四种已知独立相机。铁路两段的设备、拍摄独立性及媒体授权范围没有完整记录；`rail_train` / `rail_test` 沿用文件名，两段均用于视频链路测试，不是本轮裂缝模型的训练/测试集。

DVP 来源为 [Sikander Iqbal / Wikimedia Commons](https://commons.wikimedia.org/wiki/File:DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_(20181008093109).webm)，CC BY-SA 4.0；铁路来源为 [leven87/tunel-abnormal-detection](https://github.com/leven87/tunel-abnormal-detection)；TUM 来源为 [TUM VI](https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset)，CC BY 4.0。原始字节哈希、尺寸及路径见 [素材注册表](../experiments/captures.json)。TUM 官方归档 MD5 为 `2090088d7e636e095fdfd3da2a0c3274`，SHA-256 为 `4faa65e3025ff4ede2f03d6f833b267e6cf289422598231d2486283ea737f39b`。

TUM 仅使用 cam0；没有融合双目、IMU、里程计或运动捕捉真值。16 位灰度用固定 gamma 1/2.2 映射到 8 位，空间尺寸保持 512×512，再编码为 FFV1。源纳秒时间戳单独保留并附回被选择的帧。有效域限制在前向 85° 光锥，识别与特征提取使用相同有效区域。

## 2. 重建框架的实际变化

- [质量选帧](../keyframes.py)：在统一 640 像素评价尺度测清晰度、曝光、对比度和角点，结合光流前后检查去掉低运动重复帧；存储每个候选的选择理由。送入重建的图像保留原始像素几何。
- [几何基准入口](../benchmark_geometry.py)：原视频哈希、标定、有效掩码和时间戳均有追溯；运行前冻结脚本快照，避免长实验中修改源码影响后续子进程。
- 优化模式启用仿射/域尺度池化 SIFT；增量重建不足时使用 [全局重建](../global_reconstruct.py)。模型选择优先看有足够路标支持的输入帧覆盖率，再比较注册率、点数和内部残差。
- [稠密前审计](../audit_geometry.py)：至少 80% 输入帧各有不少于 30 个路标支持，且点轨迹最大三角化角的中位数至少 1.5°。这些是可用性启发式门槛，不是测绘精度保证。
- [自适应表面处理](../refine_surface.py)：断面先验通过直线性、正半径、观测支持和留出径向残差检查时才拟合；否则使用边界固定、位移上限为原局部边长 0.3 倍的 Taubin 去噪，并检查面翻转。只删除极小的独立碎片，不补出未知墙面。
- [网格后处理](../mesh_postprocess.py)：在稠密融合与网格化之间增加确定性清理。前向运动 MVS 会留下沿视线方向的拉丝点——它们通过几何一致性检查却把轴向范围撑大 1.4–2.4 倍并污染网格化。模块按可见图像数（≥3）与相机走廊距离（中位数 ± 2.5×1.4826×MAD 双侧门限）过滤 fused 点，在过滤后的工作区重新执行 advancing front 网格化，再对短（≤32 边）、近平面且不穿过捏点的边界环做质心扇形填充；填充顶点在 `surface.npz` 的 `filled_vertices` 中标记为推断几何，观测顶点坐标不变。

基线采用均匀选帧、SIMPLE_RADIAL 自估内参、普通 SIFT 和增量求解；优化模式加入质量选帧及上述策略，TUM 同时改用提供的鱼眼标定与有效域。特征上限均为 6,000、提取图像最大边长 1,200；2 Hz 使用顺序匹配（邻帧范围基线 8、优化 12），6 Hz 困难案例改用穷举匹配，也增加了匹配预算。

| 素材 | 2 Hz 基线注册帧 | 最终注册帧 | 最终采样 | 稀疏点：基线→最终 | 平均重投影残差 px：基线→最终 |
|---|---:|---:|---:|---:|---:|
| DVP | 40/40 | 37/37 | 2 Hz | 6,699→8,052 | 0.803→0.841 |
| 铁路长片 | 2/56 | 56/56 | 2 Hz | 32→6,602 | 1.083→0.403 |
| 铁路短片 | 0/27，未形成模型 | 79/79 | 6 Hz | 0→8,062 | —→0.421 |
| 鱼眼走廊 | 10/60 | 179/179 | 6 Hz | 293→4,519 | 0.840→0.340 |

DVP 选帧后点数增加，但重投影残差略升，不能宣称所有指标改善。铁路短片与鱼眼结果增加了采样预算，不是固定计算预算下的纯算法胜出；选帧、特征和求解器也没有分别完成完整消融。

同预算尝试保留了关键失败证据：铁路长片的优化增量模型为 10/56，使用同一特征库全局恢复至 56/56；铁路短片在 2 Hz 下仍无法形成有效模型。鱼眼 2 Hz 优化增量结果为 24/60；全局模型虽显示 60/60 注册，但仅 364 点、每帧路标中位数 7、只有 13 帧达到 30 路标门槛，因此被拒绝进入稠密阶段。增至 6 Hz 后，增量为 115/179，全局为 179/179；最终每帧路标最少 64、中位数 151，三角化角中位数约 12.98°。这也是为何框架不能只看注册帧数。

| 素材 | 稠密点：过滤前→后 | 过滤后网格三角面 | 输出三角面 | 采用的方法 | 推断顶点 |
|---|---:|---:|---:|---|---:|
| DVP | 174,732→139,437 | 277,686 | 68,736 | 观测断面拟合 | 8,406 / 34,560 |
| 铁路长片 | 71,463→68,732 | 132,656 | 68,736 | 观测断面拟合 | 10,140 / 34,560 |
| 铁路短片 | 86,844→83,548 | 161,446 | 68,736 | 观测断面拟合 | 4,323 / 34,560 |
| 鱼眼走廊 | 204,929→180,762 | 350,817 | 350,250 | 观测表面去噪 + 小孔填充 | 1,184 |

网格后处理的实测影响（轴向范围 = 过滤后相机主轴上点云跨度）：

| 素材 | 轴向范围收缩 | 长径比（范围/中位半径） | 填充小孔 | 相机间距漂移指示器 |
|---|---:|---:|---:|---:|
| DVP | ×2.35 | 27.2→12.0 | 365 | +0.00 |
| 铁路长片 | ×1.49 | 10.0→6.8 | 513 | −0.56 |
| 铁路短片 | ×1.38 | 9.3→6.9 | 680 | −0.05 |
| 鱼眼走廊 | ×1.37 | 43.8→32.6 | 1,184 | +1.69 |

DVP 与三个断面拟合场景两端默认开口，可选择明确标为人工补面的端盖；端盖并非实测结构。铁路长片在点云清洗后通过了正半径与全部有效性检查，从观测去噪升级为断面拟合；鱼眼按场景配置保持观测几何，小孔填充后边界边从 13,879 降仍保留大块未观测区，当前结果不能视为完整、封闭的走廊模型。轴向范围收缩来自拉丝点被移除，不是新的测量。相机间距漂移指示器只是趋势报告：绝对轴向尺度仍无法在不破坏像素射线一致性的前提下事后修正，需在 SfM 内部引入外部里程或周期结构约束。图像重投影误差、表面平滑与浏览器投影一致性都不等于实际定位精度，所有场景仍使用 `sfm_unit`。

## 3. 裂缝分割域适配

使用 [CTCD](https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset) 的真实裂缝像素标注，CC BY 4.0；数据来源 DOI：[10.1155/stc/6269747](https://doi.org/10.1155/stc/6269747)。下载器逐文件检查 584 个图像/掩码文件的长度与 LFS SHA-256。0/1 掩码按 `>0` 读取，8 位掩码按 `>=128` 读取；3 幅存在抗锯齿中间灰度的标注使用一致中点规则。

基础模型为外部 U-Net + SE 权重。原 250 幅训练图中，30 幅与官方 42 幅保留图共享源图编号前缀，先全部排除；剩余按前缀分组为 176 幅训练、44 幅开发验证。此后在验证集选择第 35 轮、阈值 0.85，再评估保留集。早期未排除这 30 幅的试验只是预试验，不作为最终结果。这 42 幅也曾在预试验中读取，因此属于复用的保留评估集，仍需全新外部测试集复验。

[训练脚本](../segmentation_experiment.py) 使用原生 256×256、ImageNet 归一化、旋转/翻转/亮度/轻微模糊增强、AdamW 2e-5、BCE + 0.5 Dice、40 轮、种子 2026。阈值和轮次由开发验证集选定，保留集不参与该选择。

| 42 幅保留图，像素级 | 阈值 | 精确率 | 召回率 | F1 | IoU | FP 像素 |
|---|---:|---:|---:|---:|---:|---:|
| 原权重，旧固定阈值 | 0.70 | 0.343 | 0.767 | 0.474 | 0.311 | 54,044 |
| 原权重，验证集选阈值 | 0.85 | 0.379 | 0.725 | 0.498 | 0.331 | 43,745 |
| 域适配，验证集选阈值 | 0.85 | 0.680 | 0.748 | 0.712 | 0.553 | 12,991 |

同为 0.85 阈值时，F1 从 0.498 提至 0.712，FP 像素减少约 70.3%。相对旧固定 0.7 阈值基线，召回率从 0.767 略降至 0.748，不能只报告精确率提升。四幅示例按保留集排序的等间隔位置选取，包含仍然漏检/误检的情况；示例概率缓存为 float16，正式数值来自原评测记录。

分组前缀是源图相关性的保守代理，不是已知物理隧道 ID；上游预训练成员也未完全公开。因此这些分数只支持本地域适配的保留图表现，不证明隧道独立、预训练独立或完整视频的探伤准确率。

初始权重 SHA-256：`1797a21e11e811786bde88d6464c0030cca0493d9eeb741e4c436f15bd696f51`。
最终权重 SHA-256：`cad44707520ac4a8c7557ff0acaa5eba7a39216a34b764790fb8f01321ed7583`。
[模型配置打包器](../package_model_profile.py) 把权重哈希、冻结阈值、数据协议与来源绑定，推理加载时核验哈希。外部网络源码与权重不随本仓库再分发，不能把其使用条件改称本项目许可证。

## 4. 三维候选与多帧证据

[检测器](../defect_detect.py) 在原图滑窗推理，保留原像素坐标并应用相机有效域。视频推理使用 448 像素滑窗，与上述原生 256 像素保留图评测的图像上下文不同；没有据此推算视频准确率。[三维定位](../localize_defects.py) 用真实相机模型生成射线，在原观测网格取最近交点；规则化显示关联另有位移限值与原始命中依据。

[多帧关联](../multiview_evidence.py) 在原图使用校准投影，在去畸变 MVS 工作空间检查几何深度；要求至少 1° 视差、有足够可见采样点及邻帧模型响应。同帧的不同候选不合并。遮挡、无深度、弱视差均是无法判定，不作为负例。该过程没有取得真实视频的病害标注。

| 素材 | 原候选观测 | 关联组 | 含多次观测的组 |
|---|---:|---:|---:|
| DVP | 760 | 385 | 155 |
| 铁路长片 | 1,759 | 1,069 | 236 |
| 铁路短片 | 2,484 | 1,332 | 318 |
| 鱼眼走廊 | 2,259 | 1,308 | 242 |

组数不是确诊病害数。实际目检可见施工缝、电缆和门框也会触发候选，且可能跨帧重复；多帧证据不能解决这些持续存在的语义误报。当前页面提供复核、筛选和关联帧跳转。

## 5. 离线查看器验收

四个场景均通过无服务器的 Edge/WebGL 检查：原始/优化切换、端盖可用性、时间轴末帧、候选选择、多帧筛选和原图像素投影回查。数值验收通过后仍进行了截图目检，修复了 16384 像素纹理超出设备上限的问题，以及非线性镜头直接投影跨近裁剪面三角形造成的伪影。

最终三维窗口采用明确标注的**虚拟透视**，用真实相机位姿和内参构造虚拟相机；它不是鱼眼原图的逐像素渲染。原始影像、探伤射线、邻帧投影和原像素回查仍使用真实镜头畸变模型。纹理在必要时按设备上限缩放，原几何与相机数据保留。TUM 的 FFV1 录像不放入浏览器播放器，改用完整的已选帧时间轴。

## 6. 从源码复现

以下命令从模块根目录运行。Windows 原生 COLMAP 的工作目录参数使用 ASCII 相对路径；不要把带中文的绝对数据路径直接传给原生 COLMAP。

几何环境使用 [requirements.txt](../requirements.txt)（PyCOLMAP 3.13）；另建全局求解/推理环境使用 [requirements-global.txt](../requirements-global.txt)（PyCOLMAP 4.2）。本次 GPU 为 RTX 5060 8 GB、PyTorch 2.9.1+cu128，稠密阶段为 COLMAP 4.2 CUDA。两个 Python 入口不能因升级 PyCOLMAP 而混用未经验证的 API。

```powershell
python -m venv .venv-global
.venv-global/Scripts/python.exe -m pip install -r requirements-global.txt
.venv-global/Scripts/python.exe -m pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
$globalPython = ".venv-global/Scripts/python.exe"
$colmapExe = "C:/tools/colmap/colmap.exe" # 改为实际 COLMAP 4.2 路径
$modelRepo = "third_party/crack-seg"
```

准备公开视频。下载器拒绝覆盖已有文件，因此有缓存时跳过相应下载，或直接将包含同名文件的既有缓存根目录交给导入器。导入器校验本次三段公开视频的固定哈希。

```powershell
python download_file.py "https://upload.wikimedia.org/wikipedia/commons/4/41/DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_%2820181008093109%29.webm" data/dvp_tunnel/source.webm
python download_file.py "https://raw.githubusercontent.com/leven87/tunel-abnormal-detection/master/Trainingvideos_2_1280_720.mp4" data/videos/Trainingvideos_2_1280_720.mp4
python download_file.py "https://raw.githubusercontent.com/leven87/tunel-abnormal-detection/master/Test-2_1280_720.mp4" data/videos/Test-2_1280_720.mp4
python prepare_cached_videos.py --source data
python download_file.py "https://cdn2.vision.in.tum.de/tumvi/exported/euroc/512_16/dataset-corridor4_512_16.tar" data/tumvi/dataset-corridor4_512_16.tar
python download_file.py "https://cdn2.vision.in.tum.de/tumvi/exported/euroc/512_16/dataset-corridor4_512_16.tar.md5" data/tumvi/dataset-corridor4_512_16.tar.md5
python prepare_tumvi.py
python tumvi_capture.py
```

[鱼眼标定样例](../experiments/tumvi_cam0_calibration.json)仅适用于这些 TUM 512×512 帧，主点已转换，不能再次加 0.5 或用于另一台相机。新相机应在 [素材注册表](../experiments/captures.json) 添加自己的视频、哈希、相机模型及可选标定/有效掩码，并验证原始图像尺寸。

运行基准时使用新实验名；已有目录会被保护。下列输出路径不同于本次历史记录，完成后需在 [选定运行表](../experiments/selected_runs.json) 填入新产生的实际最优模型目录。

```powershell
python benchmark_geometry.py --experiment repro_2hz --global-python $globalPython --sample-hz 2 --max-frames 150
python benchmark_geometry.py --experiment repro_6hz --cases rail_test tumvi_fisheye --modes optimized --global-python $globalPython --sample-hz 6 --max-frames 180 --matching exhaustive
```

[基准入口](../benchmark_geometry.py)会记录增量、全局及选择结果；不要仅按注册帧数人工指定未通过支持检查的模型。未知内参的可选视图图自标定会记录前后参数，本次铁路长片的该步骤并未改变初始焦距，不能把全局恢复收益归因于它。

准备标注、模型源码与初始权重，再按分组协议训练。已有最终评测目录时使用新目录，脚本拒绝覆写保留集评测。

```powershell
python download_labeled.py
python fetch_sources.py crack-seg
python download_file.py "https://huggingface.co/ishaan1402/crack-seg/resolve/main/unet_v3.pth" data/crack_model/unet_v3.pth
& $globalPython segmentation_experiment.py train --repo $modelRepo --checkpoint data/crack_model/unet_v3.pth --out results/ctcd_adaptation_grouped_v2
& $globalPython segmentation_experiment.py evaluate --repo $modelRepo --checkpoint data/crack_model/unet_v3.pth --out results/ctcd_adaptation_grouped_v2
python package_model_profile.py --experiment results/ctcd_adaptation_grouped_v2
```

对选中的每个场景运行 [稠密重建](../dense_reconstruct.py)，`--images` 指向其抽帧图像，`--model` 指向选定模型；本次 PatchMatch 最大图像边长：DVP 与铁路长片 960、铁路短片 800、TUM 640，均为 3 轮；去畸变工作图的上限另为 1600。示例：

```powershell
python dense_reconstruct.py --colmap $colmapExe --images results/repro_6hz/tumvi_fisheye__optimized/frames/images --model results/repro_6hz/tumvi_fisheye__optimized/global_reconstruction/model --out results/repro_6hz/tumvi_fisheye__optimized/global_reconstruction/dense --max-image-size 640 --iterations 3
python build_multiscene_outputs.py --stage all --colmap $colmapExe --gpu-python $globalPython --repo $modelRepo
python -m unittest discover -s tests -v
foreach ($captureName in @('dvp_handheld','rail_train','rail_test','tumvi_fisheye')) {
  node tests/verify_browser.cjs --regularized --web-dir "web/multiscene/$captureName" --out "results/browser_multiscene/$captureName"
}
python summarize_multiscene.py --baseline-experiment repro_2hz
```

汇总脚本默认读取本次历史基线目录；复跑使用新实验名后，通过 `--baseline-experiment` 指定新的 2 Hz 基线，通过 `--selected` 指定选定运行表，通过 `--recognition-experiment` 指定新的识别实验目录，避免把两次实验混成一份报告。浏览器检查需要 Node.js 22+；可用环境变量 `BROWSER_EXECUTABLE` 指定浏览器。所有分组记录、模型快照、命令日志、图像与完整三维证据保存在各运行目录中。

后续改善的重点是低纹理墙面的深度约束，以及施工缝、门框、电缆等真实视频负例；米制定位还需要小车标定与实际尺度/里程计约束。当前成果提供了可复核的数据链路与明确失败边界，没有将相对坐标或未确认候选转写为工程检测结论。
