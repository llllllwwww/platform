# 文献与开源资源索引

按项目算法链（RCAN + RTM + YOLOv7-AFPN）与数据需求整理。
标注 ⭐ 的是与本项目最直接相关、建议优先精读的。

---

## 一、算法链对应文献

### 1.1 RCAN —— 双层钢筋网杂波抑制

| # | 文献 | 说明 |
|---|---|---|
| ⭐ | Wang X., Liu H., Meng X., Cui J., Du Y. **Enhanced imaging of concealed defects behind concrete linings using Residual Channel attention network for rebar clutter suppression.** *Automation in Construction*, 166: 105574, 2024. doi:10.1016/j.autcon.2024.105574 | **平台顶栏标注的 RCAN 就是这一篇**。双层钢筋网场景下抑制钢筋杂波、增强壁后病害回波。未见官方开源代码 |
| | Wang Y., Qin H., Tang Y., et al. **RCE-GAN: A Rebar Clutter Elimination Network to Improve Tunnel Lining Void Detection from GPR Images.** *Remote Sensing*, 14(2): 251, 2022. doi:10.3390/rs14020251 | 生成对抗方式去钢筋杂波，gprMax 仿真+实测验证 |
| | Wang J., Chen K., Liu H., et al. **Deep Learning-Based Rebar Clutters Removal and Defect Echoes Enhancement in GPR Images.** *IEEE Access*, 9: 87207-87218, 2021 | 早期 CNN 去杂波工作 |
| | Wang Z., Wang J., Chen K., et al. **Unsupervised Learning Method for Rebar Signal Suppression and Defect Signal Reconstruction and Detection in GPR Images.** *Measurement*, 211: 112652, 2023 | 无监督路线 |
| | Fan P., Shen S., Wang G. **Clutter Suppression in GPR Imaging Using an Improved Robust Convolutional Autoencoder.** *Electronics Letters*, 61(1): e70467, 2025 | 卷积自编码 + 低秩稀疏分解 |
| ⭐ | 雷文太, 王以明, 钟继卫, 等. **探地雷达杂波抑制技术研究综述：机理、方法与挑战.** *电子与信息学报*, 47(11): 4079-4095, 2025. doi:10.11999/JEIT250524 | **综述，含方法对照表**，建立全局地图的入口 |

### 1.2 RTM —— 逆时偏移成像

逆时偏移是物理成像方法（非深度学习），GPR 领域通常与 FWI / 偏移成像一起出现。

| # | 文献 | 说明 |
|---|---|---|
| | Zhao Q., Ma Y., Boufounos P., Nabi S., Mansour H. **Deep Born Operator Learning for Reflection Tomographic Imaging.** *ICASSP*, 2023 | 学习 Born 算子做反射层析成像，附 MERL-GPR 仿真数据集与代码 `merlresearch/DeepBornFNO` |
| | MERL Ground Penetrating Radar Dataset (MERL-GPR), Zenodo 8145084 | gprMax 生成的 400 组二维地下结构全波场数据，1.6 GB，适合做成像/反演实验 |

> ⚠️ RTM 在本项目中的定位需要与导师确认：是实现经典 RTM 算法用于成像，
> 还是用网络加速 RTM（如 DeepBornFNO 路线）。

### 1.3 YOLO + AFPN —— 病害检测

| # | 文献 / 代码 | 说明 |
|---|---|---|
| ⭐ | Yang G., Lei J., Zhu Z., et al. **AFPN: Asymptotic Feature Pyramid Network for Object Detection.** arXiv:2306.15988 | **颈部模块来源**。代码 <https://github.com/gyyang23/AFPN>（OpenMMLab 体系，本仓库已用纯 PyTorch 等价移植，见 `src/afpn.py`） |
| | Wang C.-Y., Bochkovskiy A., Liao H.-Y. **YOLOv7: Trainable bag-of-freebies sets new state-of-the-art for real-time object detectors.** CVPR 2023 | 计划书指定的检测器。代码 <https://github.com/WongKinYiu/yolov7> |
| | Ultralytics YOLO11 | 本仓库实际使用的检测框架（维护中、预训练权重可用） |

### 1.4 隧道病害检测/分割的同类工作（可直接对照指标）

| # | 文献 | 数据规模 | 指标 |
|---|---|---|---|
| ⭐ | Li J., Zhu H., Yin M. **Deep learning-based segmentation and detection of tunnel lining defects and components from GPR images using T-GPRMask.** *Underground Space*, 2025. doi:10.1016/j.undsp.2025.07.001 | 领域预训练 + 隧道微调 | 不密实 83.18% / 空洞 88.24% / 钢拱架 92.84% / 初支厚度 91.56% |
| ⭐ | 刘勇, 王亚琼, 王志丰. **基于 YOLO 框架的隧道衬砌表观病害智能识别.** *清华大学学报*, 2026, 66(6): 1224-1234. doi:10.16511/j.cnki.qhdxxb.2026.27.014 | 陕西公路隧道现场采集 3861 张（裂缝 1789 / 剥落 / 渗漏水） | F1 0.831，mAP@0.5 0.848，mAP@0.5:0.95 0.595 |
| | 宋娟, 贺龙喜, 龙会平. **基于深度学习的隧道衬砌多病害检测算法.** *浙江大学学报(工学版)*, 2024, 58(6): 1161-1173 | 手机/相机采集 1400+ 张（裂缝、渗漏水、衬砌脱落） | F1 77.43%，mAP 77.52% |
| | 王宝坤, 王如路, 陈锦剑, 等. **基于深度学习的盾构隧道表观病害自动检测方法.** *上海交通大学学报*, 2024, 58(11): 1716-1723 | 上海地铁盾构隧道 4500 张（裂缝/渗漏水/剥落，各 1500） | 语义分割 SU-ResNet++ |
| | Wu Y., Xu F., Zhou L., et al. **A GPR Imagery-Based Real-Time Algorithm for Tunnel Lining Void Identification Using Improved YOLOv8.** *Buildings*, 15(18): 3323, 2025 | 重载铁路隧道 624 张空洞 | 识别 94.1%，分割 94.4%；GFLOPs −11.57% |
| | Chen D., Xiong S., Guo L. **Research on Detection Method for Tunnel Lining Defects Based on DCAM-YOLOv5 in GPR B-Scan.** *Radioengineering*, 32(3): 299, 2023 | gprMax 合成 2400 张 | 前向建模代码 <https://github.com/Crystal33-all/GPR> |
| | Pan Z., Zhang X., Jiang Y., et al. **High-precision segmentation and quantification of tunnel lining crack using an improved DeepLabV3+.** 2024 | 地铁隧道裂缝 | mIoU 84.77% |

---

## 二、数据集

### 2.1 已在本仓库使用

| 数据集 | 内容 | 许可 | 用途 |
|---|---|---|---|
| [TACK Tunnel Data (TTD)](https://huggingface.co/datasets/TACK-project/TACK_Tunnel_Data) | 3774 张真实隧道衬砌 512×512 + 像素掩码（Crack/Water/Leaching）；三座隧道：TA 硬岩公路、TB 预制混凝土、TC 钻爆法喷混 | CC BY-NC-ND 4.0 | 训练伪标注器 |
| [CTCD](https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset) | 52 座公路隧道巡检车采集的裂缝图 + 二值掩码（源自 CraSAM 论文，*Struct Control Health Monit* 2025） | — | 补充裂缝细节 |
| [Tunnel Lining Multi-Category Defect](https://github.com/cuijingqi/Tunnel_lining_multi-category_defect_segmentation_detection) | **17338 张真实隧道衬砌现场彩色照片**（裂缝 11375 / 渗漏 3980 / 无病害 1958） | 见仓库 | 主线训练数据（文件夹级标签） |
| [Mendeley ww7fd9t325](https://data.mendeley.com/datasets/ww7fd9t325/1) | 2239 张 GPR 实测 B-scan（空洞/管线）+ 285 条原始剖面，GSSI 200/400 MHz | CC BY-NC | GPR 分支 |
| [CMU-GPR](https://github.com/rpl-cmu/CMU-GPR-Dataset) | 15 条轨迹的 GPR + 相机 + IMU 序列 | — | 多模态定位（暂未用） |

### 2.2 可备选（需账号或未验证）

| 数据集 | 说明 |
|---|---|
| Roboflow Universe `tunnel crack` / `tunnel defect` 系列 | 大量手机拍摄的隧道病害场景图，带框；**下载需免费账号的 API Key** |
| Kaggle `gpr-normal-and-tunnel-anomaly-dataset` | 19743 条正常剖面 + 1600 个标注窗口，需 Kaggle 账号 |
| RTSD（Railway Tunnel Spalling Defects） | 8092 张铁路隧道剥落激光强度/深度图，Tongji，*Sensors* 21(17):5725；需向作者索取 |
| 国家基础学科公共科学数据中心「路桥隧」 | 216 项资源，含无人机裂缝检测数据集，需申请 |
| MERL-GPR (Zenodo 8145084) | 1.6 GB gprMax 全波场仿真数据，用于成像/反演 |

---

## 三、开源工具

| 工具 | 用途 |
|---|---|
| [gprMax](https://github.com/gprMax/gprMax) v4.0 | FDTD 电磁仿真，**PyPI 有 cp313-win_amd64 预编译轮子**，无需 MSVC；支持 CUDA/OpenCL GPU 求解器。用于生成隧道衬砌 6 类病害仿真数据 |
| [Ultralytics](https://github.com/ultralytics/ultralytics) | 训练/验证/推理框架 |
| [gyyang23/AFPN](https://github.com/gyyang23/AFPN) | AFPN 官方实现（OpenMMLab 体系，参考用） |
| [Crystal33-all/GPR](https://github.com/Crystal33-all/GPR) | 隧道衬砌病害 gprMax 正演建模代码 |
| [merlresearch/DeepBornFNO](https://github.com/merlresearch/DeepBornFNO) | Born 算子学习成像 |

---

## 四、建议的精读顺序

```
1. AFPN 论文 + 本仓库 src/afpn.py            （已实现，需吃透以支撑后续改进）
2. 隧道表观病害检测三篇中文（清华/浙大/上交）  （同类任务，指标可比）
3. RCAN（Automation in Construction 2024）    （杂波抑制，链条下一环）
4. GPR 杂波抑制综述（电子与信息学报 2025）    （建立方法全图）
5. gprMax 官方文档 + 隧道衬砌建模示例          （仿真数据生成）
6. 逆时偏移 / FWI 成像                        （成像环节，需先与导师确认定位）
```