"""AFPN（Asymptotic Feature Pyramid Network）检测器颈部模块。

论文：Yang et al., "AFPN: Asymptotic Feature Pyramid Network for Object Detection"
      arXiv:2306.15988；官方代码 https://github.com/gyyang23/AFPN

本文件是官方 mmyolo 实现（mmyolo/models/necks/yolov5_afpn.py）的纯 PyTorch 移植，
去掉了 OpenMMLab 依赖 —— 官方仓库依赖 mmcv/mmdet，本机没有 CUDA toolkit，
mmcv 无法从源码编译，因此按参考实现等价重写，数学结构与官方一致：

    P3,P4,P5 --> 1x1 降到 C/4 --> ScaleBlockBody --> 1x1 升到 out_channels
    ScaleBlockBody:
      第一级  ASFF_2：仅融合相邻的低层特征 (P3,P4)
      第二级  ASFF_3：渐进地把高层特征 P5 纳入融合，三个尺度相互对齐
      第三级  各尺度叠加若干残差块
    ASFF 用 1x1 卷积生成逐尺度权重再 softmax，即"自适应空间融合"。

与论文的差异（可配置）：
  - num_blocks 控制每个尺度每级的残差块数量，官方为 3。
"""

from __future__ import annotations

from collections import OrderedDict

import torch
import torch.nn as nn
import torch.nn.functional as F


def BasicConv(c_in: int, c_out: int, k: int, s: int = 1) -> nn.Sequential:
    pad = (k - 1) // 2 if k else 0
    return nn.Sequential(
        OrderedDict(
            [
                ("conv", nn.Conv2d(c_in, c_out, k, s, pad, bias=False)),
                ("bn", nn.BatchNorm2d(c_out)),
                ("silu", nn.SiLU(inplace=True)),
            ]
        )
    )


def Conv(c_in: int, c_out: int, k: int, s: int = 1, pad: int = 0) -> nn.Sequential:
    return nn.Sequential(
        OrderedDict(
            [
                ("conv", nn.Conv2d(c_in, c_out, k, s, pad, bias=False)),
                ("bn", nn.BatchNorm2d(c_out)),
                ("silu", nn.SiLU(inplace=True)),
            ]
        )
    )


class BasicBlock(nn.Module):
    """官方实现中的两卷积残差块（输入输出通道一致）。"""

    def __init__(self, c_in: int, c_out: int):
        super().__init__()
        self.conv1 = nn.Conv2d(c_in, c_out, 3, padding=1)
        self.bn1 = nn.BatchNorm2d(c_out, momentum=0.1)
        self.silu = nn.SiLU(inplace=True)
        self.conv2 = nn.Conv2d(c_out, c_out, 3, padding=1)
        self.bn2 = nn.BatchNorm2d(c_out, momentum=0.1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.silu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return self.silu(out + x)


class Upsample(nn.Module):
    def __init__(self, c_in: int, c_out: int, scale_factor: int = 2):
        super().__init__()
        self.upsample = nn.Sequential(
            BasicConv(c_in, c_out, 1), nn.Upsample(scale_factor=scale_factor, mode="bilinear")
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.upsample(x)


class Downsample_x2(nn.Module):
    def __init__(self, c_in: int, c_out: int):
        super().__init__()
        self.downsample = Conv(c_in, c_out, 2, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.downsample(x)


class Downsample_x4(nn.Module):
    def __init__(self, c_in: int, c_out: int):
        super().__init__()
        self.downsample = Conv(c_in, c_out, 4, 4)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.downsample(x)


class ASFF_2(nn.Module):
    """两层自适应空间融合。两个输入通道数必须相同（上层已对齐）。"""

    def __init__(self, inter_dim: int = 512, compress_c: int = 8):
        super().__init__()
        self.compress_c = compress_c
        self.weight_level_1 = BasicConv(inter_dim, compress_c, 1, 1)
        self.weight_level_2 = BasicConv(inter_dim, compress_c, 1, 1)
        self.weight_levels = nn.Conv2d(compress_c * 2, 2, kernel_size=1, stride=1, padding=0)
        self.conv = BasicConv(inter_dim, inter_dim, 3, 1)

    def forward(self, x1: torch.Tensor, x2: torch.Tensor) -> torch.Tensor:
        w = torch.cat((self.weight_level_1(x1), self.weight_level_2(x2)), 1)
        w = F.softmax(self.weight_levels(w), dim=1)
        fused = x1 * w[:, 0:1] + x2 * w[:, 1:2]
        return self.conv(fused)


class ASFF_3(nn.Module):
    """三层自适应空间融合（渐进融合的核心）。"""

    def __init__(self, inter_dim: int = 512, compress_c: int = 8):
        super().__init__()
        self.compress_c = compress_c
        self.weight_level_1 = BasicConv(inter_dim, compress_c, 1, 1)
        self.weight_level_2 = BasicConv(inter_dim, compress_c, 1, 1)
        self.weight_level_3 = BasicConv(inter_dim, compress_c, 1, 1)
        self.weight_levels = nn.Conv2d(compress_c * 3, 3, kernel_size=1, stride=1, padding=0)
        self.conv = BasicConv(inter_dim, inter_dim, 3, 1)

    def forward(self, x1: torch.Tensor, x2: torch.Tensor, x3: torch.Tensor) -> torch.Tensor:
        w = torch.cat((self.weight_level_1(x1), self.weight_level_2(x2), self.weight_level_3(x3)), 1)
        w = F.softmax(self.weight_levels(w), dim=1)
        fused = x1 * w[:, 0:1] + x2 * w[:, 1:2] + x3 * w[:, 2:3]
        return self.conv(fused)


class ScaleBlockBody(nn.Module):
    """AFPN 主干融合体：先融合相邻低层，再渐进纳入高层。"""

    def __init__(self, channels: list[int], num_blocks: int = 3):
        super().__init__()
        c_top, c_mid, c_bot = channels

        self.blocks_top1 = nn.Sequential(BasicConv(c_top, c_top, 1))
        self.blocks_mid1 = nn.Sequential(BasicConv(c_mid, c_mid, 1))
        self.blocks_bot1 = nn.Sequential(BasicConv(c_bot, c_bot, 1))

        self.downsample_top1_2 = Downsample_x2(c_top, c_mid)
        self.upsample_mid1_2 = Upsample(c_mid, c_top, scale_factor=2)

        self.asff_top1 = ASFF_2(inter_dim=c_top)
        self.asff_mid1 = ASFF_2(inter_dim=c_mid)

        self.blocks_top2 = nn.Sequential(*[BasicBlock(c_top, c_top) for _ in range(num_blocks)])
        self.blocks_mid2 = nn.Sequential(*[BasicBlock(c_mid, c_mid) for _ in range(num_blocks)])

        self.downsample_top2_2 = Downsample_x2(c_top, c_mid)
        self.downsample_top2_4 = Downsample_x4(c_top, c_bot)
        self.downsample_mid2_2 = Downsample_x2(c_mid, c_bot)
        self.upsample_mid2_2 = Upsample(c_mid, c_top, scale_factor=2)
        self.upsample_bot2_2 = Upsample(c_bot, c_mid, scale_factor=2)
        self.upsample_bot2_4 = Upsample(c_bot, c_top, scale_factor=4)

        self.asff_top2 = ASFF_3(inter_dim=c_top)
        self.asff_mid2 = ASFF_3(inter_dim=c_mid)
        self.asff_bot2 = ASFF_3(inter_dim=c_bot)

        self.blocks_top3 = nn.Sequential(*[BasicBlock(c_top, c_top) for _ in range(num_blocks)])
        self.blocks_mid3 = nn.Sequential(*[BasicBlock(c_mid, c_mid) for _ in range(num_blocks)])
        self.blocks_bot3 = nn.Sequential(*[BasicBlock(c_bot, c_bot) for _ in range(num_blocks)])

    def forward(self, x: list[torch.Tensor]) -> tuple[torch.Tensor, ...]:
        x1, x2, x3 = x
        x1, x2, x3 = self.blocks_top1(x1), self.blocks_mid1(x2), self.blocks_bot1(x3)

        # 第一级：只融合相邻的两个低层特征
        top = self.asff_top1(x1, self.upsample_mid1_2(x2))
        mid = self.asff_mid1(self.downsample_top1_2(x1), x2)

        x1, x2 = self.blocks_top2(top), self.blocks_mid2(mid)

        # 第二级：把高层 P5 纳入，三尺度互相融合
        top = self.asff_top2(x1, self.upsample_mid2_2(x2), self.upsample_bot2_4(x3))
        mid = self.asff_mid2(self.downsample_top2_2(x1), x2, self.upsample_bot2_2(x3))
        bot = self.asff_bot2(self.downsample_top2_4(x1), self.downsample_mid2_2(x2), x3)

        return self.blocks_top3(top), self.blocks_mid3(mid), self.blocks_bot3(bot)


class AFPN(nn.Module):
    """AFPN 颈部：输入 P3/P4/P5，输出同尺度的三个融合特征。

    在 Ultralytics 的模型图中，该层以"多输入、多输出（tuple）"的形式出现，
    随后由 3 个 Index 层各取一路送进 Detect 头，详见 src/model.py。
    """

    def __init__(
        self,
        in_channels: list[int],
        out_channels: int,
        num_blocks: int = 3,
        src: list[int] | None = None,
    ):
        super().__init__()
        self.in_channels = list(in_channels)
        self.out_channels = int(out_channels)
        self.src = list(src) if src is not None else [-3, -2, -1]
        # 占位：Ultralytics 解析模型图时会被替换为 src 中记录的真实层号
        self.f_from_backbone = True

        c3, c4, c5 = (c // 4 for c in self.in_channels)
        self.conv1 = BasicConv(self.in_channels[0], c3, 1)
        self.conv2 = BasicConv(self.in_channels[1], c4, 1)
        self.conv3 = BasicConv(self.in_channels[2], c5, 1)

        self.body = nn.Sequential(ScaleBlockBody([c3, c4, c5], num_blocks=num_blocks))

        self.conv11 = BasicConv(c3, self.out_channels, 1)
        self.conv22 = BasicConv(c4, self.out_channels, 1)
        self.conv33 = BasicConv(c5, self.out_channels, 1)

        # 与官方一致：xavier 初始化
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.xavier_normal_(m.weight, gain=0.02)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.normal_(m.weight.data, 1.0, 0.02)
                nn.init.constant_(m.bias.data, 0.0)

    def forward(self, x: list[torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x1, x2, x3 = x
        out1, out2, out3 = self.body([self.conv1(x1), self.conv2(x2), self.conv3(x3)])
        return self.conv11(out1), self.conv22(out2), self.conv33(out3)