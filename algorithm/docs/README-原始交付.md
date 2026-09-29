# 隧道录像 → 三维表面 → 疑似缺陷位置

本轮已实际运行公开隧道录像的完整离线处理流程。入口为 [三维复核页面](web/index.html)，直接用 Edge / Chrome 打开即可；不要只复制 HTML，分享时保留整个 `web` 目录。

页面支持旋转/缩放真实重建网格、切换恢复出的相机视角、按录像时间切换帧、显示原始画面与分割掩码、选择疑似缺陷并查看网格坐标。页面不依赖 CDN，也不需要启动替代服务器。

## 最新网格优化

页面现已默认显示点云断面拟合后的连续表面，支持“原始重建／规则化表面”同视角对照、补全区域着色和人工端盖。优化内壁为 68,736 个三角面，仅有区段两端边界；69,120 面的端盖版为完全封闭网格。原始重建及其缺陷位置仍保留。

算法、量化对比、实际截图与导出模型见 [网格优化说明](网格优化说明.md)。下文的原始重建数字作为第一阶段基线，不能与规则化后的测量范围或候选关联数量混用。

## 使用的真实视频

- 视频：[DVP Tunnel at Moccasin Trail Park in Toronto](https://commons.wikimedia.org/wiki/File:DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_(20181008093109).webm)。作者 **Sikander Iqbal**，Google Pixel 2 拍摄，2018-10-08；许可 **CC BY-SA 4.0**。这是公开的人行隧道录像，并非用户小车的实测数据。
- 下载副本：[source.webm](data/dvp_tunnel/source.webm)；[下载地址、字节数与 SHA-256](data/dvp_tunnel/source.webm.source.json)；[原站作者与许可元数据](research_sources/dvp_metadata.json)。视频及其抽帧、掩码叠加和纹理衍生成果按原许可归属作者并保留相同许可说明。
- 以 2 Hz 抽得 **40 帧**。保留原始 1080×1920 像素几何，没有先做裁剪防抖。录像中的视角抖动由 SfM 位姿估计处理，运动模糊和滚动快门没有被宣称已完全消除。

## 本轮结果与边界

| 项目 | 实际结果 | 含义 |
|---|---:|---|
| 相机注册 | 40 / 40 | 这些帧进入同一个重建分量 |
| 稀疏三维点 | 9,828 | 来自特征对应和三角化 |
| 点均值重投影误差 | 0.748 px | 内部图像拟合残差，**不是毫米级定位精度** |
| 重建表面 | 191,529 顶点 / 379,967 三角面 | 多视图深度融合与网格化的结果 |
| 纹理覆盖 | 319,394 / 379,967 面，约 84.1% | 未分配纹理的几何以灰色显示；缺口不补造 |
| 模型候选观测 | 694 | 逐帧连通域数量，未跨帧去重，不是 694 处真实病害 |
| 有三维位置的候选观测 | 572 | 至少一个采样像素命中网格 |
| 候选采样像素命中 | 37,488 / 47,871，约 78.3% | 未命中的像素保留明确状态，不伪造坐标 |
| 坐标一致性抽查 | 400 / 400 通过 | 同一掩码、相机模型、三角面之间的接口检查 |
| 绝对尺度 | 未标定，`sfm_unit` | 没有使用假定隧道直径换算成米 |

**这一版证明全流程能够运行，并不证明缺陷判别已经可靠。** 目检可见模型会响应施工接缝、路面边缘、涂写等。前端因此统一显示“疑似裂缝 / 待复核”，模型分数不被解释为准确率。没有该视频的人工病害标注和测量真值，不能给出真实探伤准确率、裂缝宽度或厘米级坐标误差。

这也是下一阶段的明确研究重点：在隧道内壁分割与构件排除之后，用“真实裂缝 / 施工缝 / 电缆 / 涂写”标注微调并验证缺陷模型；同时引入多帧一致性和跨帧去重。普通 RGB 录像只能识别可见表面异常，内部空洞等不可见缺陷需要其他传感器。

## 已采用的开源实现

| 实现 | 当前用途 | 当前状态 |
|---|---|---|
| [COLMAP](https://github.com/colmap/colmap) | 相机参数、SfM、Bundle Adjustment、PatchMatch、深度融合和纹理 | 本次实际运行；PyCOLMAP 3.13.0 做稀疏，官方 COLMAP 4.2.0 CUDA 做稠密 |
| [hloc](https://github.com/cvg/Hierarchical-Localization) + [LightGlue](https://github.com/cvg/LightGlue) | 更强的匹配后端、重定位研究 | 源码已获取，本次隧道结果没有使用其推理 |
| [MASt3R-SLAM](https://github.com/rmurai0610/MASt3R-SLAM) | 学习式稠密匹配和视频 SLAM 对照 | 源码已获取，本次没有运行其权重；8 GB 显存需单独验证 |
| [crack-seg](https://github.com/Ishaan1402/crack-seg) / [公开权重](https://huggingface.co/ishaan1402/crack-seg) | U-Net + SE + 深监督，原图滑窗裂缝分割 | 本次实际使用，7.9M 参数；跨域模型、许可条件仍需核对，不作为已验证的商用组件 |
| [CrackSeg9k](https://github.com/Dhananjay42/crackseg9k)、[DeepCrack](https://github.com/yhlleo/DeepCrack) | 后续训练与评测参考 | 获取代码不等于已下载全部数据或已完成训练 |

主要论文起点：Schönberger & Frahm, *Structure-from-Motion Revisited*, CVPR 2016；Schönberger et al., *Pixelwise View Selection for Unstructured Multi-View Stereo*, ECCV 2016（见 [COLMAP 文献](https://colmap.github.io/bibliography.html)）；Lindenberger et al., [LightGlue](https://arxiv.org/abs/2306.13643), ICCV 2023；Murai et al., [MASt3R-SLAM](https://arxiv.org/abs/2412.12392), CVPR 2025。

选择显式网格主线，是因为缺陷需要落到可求交的表面；NeRF / 3D Gaussian Splatting 的新视角画质不能直接替代几何精度验证。相机晃动本身不要求预先固定姿态，但低纹理、模糊、反光和纯前向小视差仍会使恢复失败。DVP 本次增量 SfM 成功，不能把“隧道增量 SfM 必然失败”当成通用结论；GLOMAP 可作为失败片段的另一个基线。

## 算法接口

1. [reconstruct.py](reconstruct.py)：抽帧、记录视频时间与图像质量；SIFT + 几何验证 + SfM 导出相机内外参、稀疏点和稳定场景 ID。Windows 中文目录通过模块内 ASCII 相对路径适配。
2. [dense_reconstruct.py](dense_reconstruct.py)：去畸变、GPU PatchMatch、融合、Advancing Front 网格化、纹理映射；每步检查退出码并保留日志。
3. [orient_surface.py](orient_surface.py)：利用实际 MVS 点法向校正三角面的绕序。本次发现 363,462 个三角面背向观测相机，校正后纹理覆盖由 2.94% 提升至 84.1%。**顶点、三角形集合与坐标不变**。
4. [defect_detect.py](defect_detect.py)：原图滑窗推理，保存完整二值掩码及叠加图；按连通域抽样像素，转换为 COLMAP 的像素中心约定 `x+0.5, y+0.5`。
5. [mesh_io.py](mesh_io.py) + [localize_defects.py](localize_defects.py)：网格接口；像素反畸变后通过相机位姿发射射线，取最近的正向三角面交点。处理遮挡、无交点、不可反畸变与未注册帧。
6. [export_web.py](export_web.py)：打包实际表面、纹理、时间轴、原图和掩码；三维命中点与渲染几何保持同一坐标系。纹理以数据 URL 封装，双击文件也能读取，不触发 WebGL 文件跨域问题。

原有 [地形 WebGL 绘制代码](../06_可视化/view3dgl.js) 的缓冲区和着色器校验模式有复用价值，但它使用高程场和固定深度，不能直接承载隧道内壁。新页面在相同原生 WebGL 技术上实现了透视、真实深度缓冲和任意三角网格。

## 复现

现有页面和结果已生成，无需重新计算即可查看。[reproduce_dvp.ps1](reproduce_dvp.ps1) 保存完整复现命令，默认写入新的运行目录，防止混用不同相机坐标系。

本机实测环境：工作区独立 Python 3.11 + PyCOLMAP 3.13.0；稠密阶段使用 `F:/data/toolchains/colmap420/bin/colmap.exe`；神经网络及 GPU 射线阶段复用 `F:/data/WB/venvs/tunnel/Scripts/python.exe`（PyTorch 2.9.1+cu128，RTX 5060 8 GB）。这些外部环境已经存在，本轮结果写在当前工作区。跨机器运行时需要改为当地对应路径。

当前运行数据：[质量报告](results/dvp_tunnel/quality.json)、[稠密阶段命令与耗时](results/dvp_tunnel/dense/run.json)、[几何验证报告](results/dvp_tunnel/verification.json)、[页面打包摘要](web/package_report.json)。三维模型与完整掩码、未命中记录均保留，前端不是手绘隧道动画。

## 验证范围

[verify_pipeline.py](tests/verify_pipeline.py) 从结果回读，抽查实际输出的 400 个命中点，检查：原始模型掩码内、相机前方、投影回原像素、位于所记录三角面，且纹理模型与定位模型只有绕序差异。最大投影回误差约 `1.26e-8 px`，仅说明坐标变换自洽，**绝不是实际定位精度**。

[浏览器检查](tests/verify_browser.cjs) 用 Edge 打开实际本地文件，检查 WebGL、纹理载入、帧切换、相机视角和候选选择，并输出截图和机器可读报告。浏览器截图用于发现并修复了上面提到的表面朝向问题。
