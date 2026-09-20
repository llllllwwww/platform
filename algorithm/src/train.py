"""GPR 病害检测训练入口（YOLO11 + AFPN 颈部）。

流程：构建 AFPN 模型 -> 迁移 COCO 预训练主干 -> Ultralytics 训练器训练
      -> 验证集评估 -> 可选测试集评估。

用法示例：
  python src/train.py --epochs 100 --batch 16 --imgsz 320
  python src/train.py --epochs 3 --name smoke        # 快速自检

说明：训练必须在允许写入 venv / 项目目录的环境下运行（沙箱内 ultralytics 会写缓存而失败）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402

from detector import build_model, install  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="训练 GPR/隧道病害检测或分割模型（YOLO11-AFPN）")
    ap.add_argument("--task", choices=["detect", "segment"], default="detect")
    ap.add_argument("--model", default=None, help="模型 YAML；默认按 task 选用 configs/ 下的配置")
    ap.add_argument("--data", default=str(ROOT / "data" / "processed" / "gpr_det" / "data.yaml"))
    ap.add_argument("--pretrained", default="yolo11s.pt", help="COCO 预训练主干；给 none 表示从头训练")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--lr0", type=float, default=0.01)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--project", default=None, help="输出根目录；默认 runs/<task>")
    ap.add_argument("--name", default=None)
    ap.add_argument("--test", action="store_true", help="训练结束后在 test 集上评估")
    ap.add_argument("--cache", action="store_true", help="把图片缓存到内存/磁盘，加速小数据集训练")
    args = ap.parse_args()
    if args.model is None:
        args.model = str(
            ROOT / "configs" / ("yolo11s-tsd-afpn-seg.yaml" if args.task == "segment" else "yolo11s-tsd-afpn.yaml")
        )
    if args.name is None:
        args.name = "afpn-seg" if args.task == "segment" else "afpn-det"
    if args.project is None:
        # 统一约定 runs/<task>/<name>，pseudo_label.py 与 run_pipeline.ps1 依赖该路径
        args.project = str(ROOT / "runs" / args.task)
    return args


def read_nc(data_yaml: Path) -> int:
    """从 data.yaml 读类别数。

    必须用数据集的 nc，不能用模型 YAML 里写死的值：
    train.py 是直接给 trainer 预置模型对象（跳过 ultralytics 的 setup_model），
    那条路径不会用数据集的 nc 覆盖模型，写死就会导致类别数不匹配。
    """
    import yaml

    cfg = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    names = cfg.get("names")
    if isinstance(names, dict):
        return len(names)
    if names:
        return len(names)
    return int(cfg.get("nc", 1))


def main() -> None:
    args = parse_args()
    install()

    nc = read_nc(Path(args.data))
    print(f"[data] {args.data}  类别数 nc={nc}")

    if args.task == "segment":
        from ultralytics.models.yolo.segment import SegmentationTrainer as Trainer
    else:
        from ultralytics.models.yolo.detect import DetectionTrainer as Trainer

    # --- 1. 解析预训练主干 ---
    pretrained_path = None
    if args.pretrained and args.pretrained.lower() != "none":
        from ultralytics.utils.downloads import attempt_download_asset

        pretrained_path = attempt_download_asset(args.pretrained)
        print(f"[pretrained] 使用 {pretrained_path}")

    # --- 2. 构建 AFPN 模型并迁移主干权重 ---
    model = build_model(args.model, nc=nc, pretrained=pretrained_path, verbose=True)
    print(f"[model] 参数量 {sum(p.numel() for p in model.parameters()):,}")

    # --- 3. 交给 Ultralytics 训练器 ---
    trainer = Trainer(
        overrides={
            "model": args.model,
            "data": args.data,
            "epochs": args.epochs,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "device": args.device,
            "workers": args.workers,
            "seed": args.seed,
            "lr0": args.lr0,
            "patience": args.patience,
            "project": args.project,
            "name": args.name,
            "pretrained": False,
            "cache": args.cache,
            "exist_ok": True,
            "plots": True,
            "val": True,
            "amp": True,
        }
    )
    trainer.model = model  # 预置模型：trainer.setup_model() 会因此跳过重建
    model.args = trainer.args  # 训练器下游会读这个属性

    trainer.train()

    save_dir = Path(trainer.save_dir)
    best = save_dir / "weights" / "best.pt"
    print(f"\n[完成] 最优权重: {best}")

    # --- 4. 用最优权重在 val / test 上评估并汇总指标 ---
    summary = {"save_dir": str(save_dir), "best": str(best), "args": vars(args)}
    if best.exists():
        from ultralytics import YOLO

        yolo = YOLO(str(best))
        for split in ["val"] + (["test"] if args.test else []):
            res = yolo.val(data=args.data, split=split, imgsz=args.imgsz, device=args.device, plots=True)
            summary[split] = {
                "images": int(res.nt_per_class.sum()) if hasattr(res, "nt_per_class") else None,
                "mAP50": float(res.box.map50),
                "mAP50-95": float(res.box.map),
                "precision": float(res.box.mp),
                "recall": float(res.box.mr),
                "per_class_mAP50-95": [float(x) for x in res.box.maps],
                "class_names": res.names,
            }
            print(f"[{split}] mAP50={res.box.map50:.4f}  mAP50-95={res.box.map:.4f}")

    (ROOT / "reports").mkdir(parents=True, exist_ok=True)
    out = ROOT / "reports" / f"metrics_{args.name}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[指标] 已写入 {out}")


if __name__ == "__main__":
    main()