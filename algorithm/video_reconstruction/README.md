# 真实录像三维重建、网格优化与缺陷定位

此模块保存已实际跑通的独立视频处理流程：原始录像 → 抽帧 → 相机位姿和三维表面 → 裂缝候选分割 → 射线/网格定位 → 隧道规则化补全 → 原始/优化模型对比页面。

它与仓库现有训练模块并列，当前没有自动接入主平台的业务台账。生成的复核页面使用本地 WebGL，不依赖 CDN；源码模板在 [web_template](web_template)，运行后生成 `web/index.html`。

## 环境

推荐 Python 3.11。几何阶段使用 PyCOLMAP 3.13.0，稠密重建使用独立安装的 **COLMAP 4.2 CUDA**，因为流程调用其 `advancing_front_mesher` 和 `mesh_texturer`。安装二进制后确认这些命令可用。

```bash
cd algorithm/video_reconstruction
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
# 以下是本次 NVIDIA 环境对应的 PyTorch 版本；其他显卡应按 PyTorch 官方指南选择。
python -m pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
```

本次完整 GPU 流程在 Windows、RTX 5060 8 GB 上验证。Python 入口接受可执行文件路径，未写死实验机器盘符；其他操作系统仍须具备匹配的 COLMAP CUDA 构建。`--device cpu` 只切换检测与射线求交，稠密 PatchMatch 仍需要 CUDA。

## 运行公开视频案例

先查看将执行的命令，不会下载或运行计算：

```bash
python run_dvp.py --colmap colmap --plan
```

将 `--colmap` 指向已安装的 COLMAP 4.2 可执行文件，或将其加入 PATH 后执行：

```bash
python run_dvp.py --colmap colmap
```

也支持环境变量 `COLMAP_BIN`。如果神经网络在另一个 Python 环境，传入 `--gpu-python /path/to/python`。入口按需下载公开录像、模型源码和权重，逐阶段检查退出码；输出目录已有内容时拒绝混用，可用 `--run-name another_run` 创建新运行。默认生成 `results/dvp_tunnel/` 和 `web/`，视频与帧写入 `data/`。上述目录均由 Git 忽略。

源数据是 [Sikander Iqbal 的 DVP Tunnel 录像](https://commons.wikimedia.org/wiki/File:DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_(20181008093109).webm)，许可 CC BY-SA 4.0。作者和许可元数据保存在 [dvp_metadata.json](research_sources/dvp_metadata.json)。

探伤适配器使用 [crack-seg](https://github.com/Ishaan1402/crack-seg) 与 [公开权重](https://huggingface.co/ishaan1402/crack-seg)。源码和权重不随本仓库分发；运行前应核对上游使用条件。下载器记录 SHA-256 与来源。其他官方算法源码可通过 [fetch_sources.py](fetch_sources.py) 按需获取。

## 模块

| 文件 | 用途 |
|---|---|
| [reconstruct.py](reconstruct.py) | 原始帧时间戳、质量诊断、SIFT/SfM、相机参数与场景 ID |
| [dense_reconstruct.py](dense_reconstruct.py) | 去畸变、GPU 深度、融合、网格化、纹理 |
| [orient_surface.py](orient_surface.py) | 根据 MVS 法向修正网格朝向，保持几何不变 |
| [defect_detect.py](defect_detect.py) | 原图滑窗分割，输出掩码与采样像素 |
| [localize_defects.py](localize_defects.py)、[intersect_gpu.py](intersect_gpu.py) | 校准相机射线与最近三角面求交，支持 CPU/GPU |
| [regularize_tunnel.py](regularize_tunnel.py) | B 样条＋周期断面稳健拟合，规则网格与封闭体导出 |
| [relocalize_regularized.py](relocalize_regularized.py) | 根据原始命中依据与位移限值关联优化表面 |
| [export_web.py](export_web.py) | 打包原图、掩码、纹理、两种网格及定位结果 |
| [run_dvp.py](run_dvp.py) | 无实验机器绝对路径的完整案例入口 |

视频路径、场景、网格、检测文件可通过各脚本的 `--help` 查看接口。自定义视频应保留原始像素几何、同一场景 ID 与统一坐标尺度；默认案例导出的作者信息对应 DVP 视频，处理自有录像时应相应更新来源信息。

## 已验证结果

DVP 录像共 40 帧，40 帧注册成功，9,828 个稀疏点，平均重投影误差 0.748 px。原始网格 379,967 面；优化内壁 68,736 面，仅保留区段两端的开口；加人工端盖后 69,120 面、零边界边。

原始候选观测 694 个，572 个得到原网格命中；规则化关联保留 432 个观测、26,838 个采样点。候选未经人工确认为病害，施工缝和涂写可能误报。单目尺度仍为 `sfm_unit`，不代表米制尺寸；重投影残差与内部回投误差不是实际定位精度。

[质量摘要](docs/dvp_quality.json)、[网格与定位验证摘要](docs/regularization_verification.json)是先前真实实验的记录，完整数据产物没有上传。方法、限制与数据哈希见 [验证说明](docs/VALIDATION.md)。

## 验证命令

不依赖视频和权重的 CPU 几何检查：

```bash
python tests/verify_intersect_equivalence.py
```

默认 DVP 案例生成后，可运行：

```bash
python tests/verify_pipeline.py
python tests/verify_regularization.py
node tests/verify_browser.cjs --regularized
```

浏览器检查需要 Node.js 22+，Windows 默认查找 Edge，其他平台默认 `chromium`；可通过 `BROWSER_EXECUTABLE` 指定浏览器路径。截图与浏览器配置均写入被忽略的 `results/`。GPU 等价性检查接受网格路径：`python tests/verify_intersect_gpu.py results/dvp_tunnel/regularization/surface_regularized.npz`。

## 提交范围

仓库包含本项目流程代码、前端模板、依赖说明、下载入口和小型验证记录。视频、图像、模型权重、第三方源码副本、CUDA 二进制、虚拟环境、浏览器配置以及大型重建产物不提交。克隆代码后需要准备环境并运行流程，GitHub Pages 主工作台不会因此自动载入本地实验结果。
