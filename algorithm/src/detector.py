"""把 AFPN 颈部接入 Ultralytics 的模型图，并提供构建 / 载入 / 迁移预训练权重的入口。

背景（为什么需要下面这段 parse_model 包装）
------------------------------------------------
Ultralytics 的 `parse_model` 对"非内置模块"统一走 `c2 = ch[f]` 这一分支来推断输出通道，
当 `f` 是多输入列表时 `ch[f]` 会直接抛 TypeError，所以官方模型图里多输入层
（如 Concat / CBFuse）都是白名单硬编码的，第三方模块无法直接写"多输入"。

这里不改 Ultralytics 源码，只在调用前后各做一次改写：
  解析前：把 AFPN 那一行的 f=[4,6,10] 临时改成 f=10（整数），使原函数能正常跑通；
  解析后：把 f 还原成 [4,6,10]，并把 4/6/10 补进 model.save，
          否则前向时这几层不会被缓存，AFPN 取不到输入。
AFPN 输出是 3 元 tuple，紧接着的 3 个 Index 层各取一路送进 Detect 头。
"""

from __future__ import annotations

from pathlib import Path

import torch

from afpn import AFPN

# 允许"多输入"写法的第三方模块：模块名 -> 类
MULTI_INPUT_MODULES: dict[str, type] = {"AFPN": AFPN}


def install() -> None:
    """注册自定义模块并安装 parse_model 包装（幂等）。"""
    import ultralytics.nn.tasks as tasks

    # parse_model 通过 globals()[名字] 查找模块，因此注入到 tasks 的命名空间即可生效
    tasks.AFPN = AFPN

    if getattr(tasks.parse_model, "_gpr_dl_patched", False):
        return

    original = tasks.parse_model

    def parse_model(d, ch, verbose=True):
        rows = d.get("backbone", []) + d.get("head", [])
        pending: dict[int, list[int]] = {}  # 层号 -> 原始多输入列表
        for i, row in enumerate(rows):
            f, _n, name, _args = row[0], row[1], row[2], row[3]
            if isinstance(f, list) and name in MULTI_INPUT_MODULES:
                pending[i] = list(f)
                row[0] = f[-1]  # 临时改成单输入，让原函数能推断出一个合法通道数

        model, save = original(d, ch, verbose=verbose)

        if pending:
            extra: set[int] = set()
            for i, f in pending.items():
                model[i].f = f  # 还原真实的多输入
                extra |= {x for x in f if x != -1}
            save = sorted(set(save) | extra)
        return model, save

    parse_model._gpr_dl_patched = True
    parse_model._gpr_dl_original = original
    tasks.parse_model = parse_model


def build_model(
    cfg: str | Path,
    nc: int | None = None,
    ch: int = 3,
    verbose: bool = True,
    pretrained: str | Path | None = None,
):
    """按 YAML 构建检测模型；pretrained 给定时迁移主干权重（COCO 预训练）。"""
    install()
    from ultralytics.nn.tasks import DetectionModel

    config = cfg if isinstance(cfg, dict) else str(cfg)
    model = DetectionModel(config, ch=ch, nc=nc, verbose=verbose)

    if pretrained is not None:
        stats = transfer_backbone(model, pretrained, verbose=verbose)
        if verbose:
            print(
                f"[pretrained] 从 {Path(pretrained).name} 迁移 {stats['matched']} 个张量"
                f"（主干），跳过 {stats['skipped']} 个（形状不匹配，如检测头类别数不同）"
            )
    return model


def transfer_backbone(model: torch.nn.Module, ckpt_path: str | Path, verbose: bool = True) -> dict:
    """只迁移形状能对上的权重（主干 + 颈部中同名同形者）。

    检测头不能迁移：COCO 是 80 类，本任务是 2 类，输出层形状不同。
    """
    ckpt = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    src = ckpt.get("ema") or ckpt.get("model") if isinstance(ckpt, dict) else ckpt
    src_sd = src.state_dict()

    dst_sd = model.state_dict()
    matched, skipped = {}, 0
    for k, v in src_sd.items():
        if k in dst_sd and dst_sd[k].shape == v.shape:
            matched[k] = v
        else:
            skipped += 1
    missing = [k for k in dst_sd if k not in matched]
    model.load_state_dict(matched, strict=False)
    if verbose:
        print(f"[pretrained] 待训练参数共 {len(dst_sd)} 个张量，其中 {len(missing)} 个为新随机初始化")
    return {"matched": len(matched), "skipped": skipped, "missing": missing}


def load_detector(weights: str | Path, device: str | torch.device = "cuda"):
    """载入训练好的权重用于推理（保留 Ultralytics 的预处理/后处理能力）。"""
    install()
    from ultralytics import YOLO

    return YOLO(str(weights))


def afpn_layer_indices(model) -> list[int]:
    """返回模型中所有 AFPN 层的层号，便于检查接线。"""
    return [m.i for m in model.model if isinstance(m, AFPN)]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="构建 AFPN 检测模型并打印结构")
    ap.add_argument("--cfg", default="configs/yolo11s-gpr-afpn.yaml")
    ap.add_argument("--nc", type=int, default=2)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--pretrained", default=None)
    args = ap.parse_args()

    net = build_model(args.cfg, nc=args.nc, verbose=True, pretrained=args.pretrained)
    idx = afpn_layer_indices(net)
    print(f"\nAFPN 层号: {idx}")
    for i in idx:
        layer = net.model[i]
        print(f"  layer[{i}].f = {layer.f}（输入层号）  in_channels={layer.in_channels} out={layer.out_channels}")

    net.eval()
    with torch.no_grad():
        out = net(torch.zeros(1, 3, args.imgsz, args.imgsz))
    print(f"\n前向通过：输入 1x3x{args.imgsz}x{args.imgsz}，stride={net.stride.tolist()}")
    if isinstance(out, tuple):
        for k, child in enumerate(out):
            if torch.is_tensor(child):
                print(f"  输出[{k}] 形状 {tuple(child.shape)}")
            elif isinstance(child, (list, tuple)):
                shapes = [tuple(t.shape) for t in child if torch.is_tensor(t)]
                print(f"  输出[{k}] 张量列表 {shapes}")
            elif isinstance(child, dict):
                print(f"  输出[{k}] 附带信息 {list(child)}")