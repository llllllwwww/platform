"""用训练好的 AFPN-Seg 给 17338 张真实隧道照片做伪标注，生成检测框。

流程
----
1. 读 src/build_scene_split.py 产出的切分清单（按帧块分组，无泄漏）
2. 对每张图跑推理，取**框头**输出作为伪标注
3. 文件夹标签为 `no defects` 的图强制写成空标签（干净的负样本，抑制误报）
4. 图片用**硬链接**接入新数据集，不额外占用磁盘
5. 输出 YOLO 检测格式 + 抽样可视化，供人工确认伪标注质量

为什么默认用框头而不是掩码头
--------------------------
裂缝是 1~2 像素宽的细长结构，掩码 IoU 对半个像素的偏移就崩掉，
实测训练中 mask mAP50 长期接近 0（box mAP50 却正常上升）。
伪标注只需要"病害在哪"，框足够；因此默认取框头（--from boxes）。
若掩码质量足够好，可用 --from masks 改用实例多边形外接框。

注意：伪标注的三类来自分割器（crack / water / leaching）；文件夹里的 `leakage`
是"渗漏"这个大类，会被细分成 water 和 leaching。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from detector import install  # noqa: E402

CLASS_NAMES = ["crack", "water", "leaching"]
PALETTE = [(0, 0, 255), (255, 176, 32), (0, 200, 0)]


def link_or_copy(src: Path, dst: Path) -> None:
    """优先硬链接（同卷、零拷贝），失败则回退到复制。"""
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description="用 AFPN-Seg 给真实隧道照片生成伪检测框")
    ap.add_argument(
        "--weights",
        type=Path,
        default=ROOT / "runs" / "segment" / "afpn-seg" / "weights" / "best.pt",
    )
    ap.add_argument("--manifest", type=Path, default=ROOT / "data" / "processed" / "scene_split.json")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed" / "scene_det")
    ap.add_argument("--from", dest="source", choices=["boxes", "masks"], default="boxes", help="伪标注取自框头还是掩码头")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--iou", type=float, default=0.6)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--min-side", type=int, default=12, help="框最短边小于该值则丢弃（像素）")
    ap.add_argument("--max-area-frac", type=float, default=0.85, help="框面积超过整图该比例则丢弃")
    ap.add_argument("--device", default="0")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 张（调试用，0 表示全部）")
    ap.add_argument("--preview", type=int, default=24, help="抽样可视化多少张")
    args = ap.parse_args()

    if not args.weights.exists():
        raise SystemExit(f"分割权重不存在: {args.weights}（先训练 src/train.py --task segment）")
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = data["items"]
    if args.limit:
        items = items[: args.limit]
    print(f"待伪标注 {len(items)} 张，权重 {args.weights}")

    install()
    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    print(f"分割器类别: {model.names}")

    if args.out.exists():
        for split in ("train", "val", "test"):
            shutil.rmtree(args.out / "images" / split, ignore_errors=True)
            shutil.rmtree(args.out / "labels" / split, ignore_errors=True)
    for split in ("train", "val", "test"):
        (args.out / "images" / split).mkdir(parents=True, exist_ok=True)
        (args.out / "labels" / split).mkdir(parents=True, exist_ok=True)

    stats: dict[str, Counter] = defaultdict(Counter)
    per_folder: dict[str, Counter] = defaultdict(Counter)
    previews: list[np.ndarray] = []
    rng = np.random.default_rng(0)
    preview_idx = set(rng.choice(len(items), size=min(args.preview, len(items)), replace=False).tolist())

    for start in range(0, len(items), args.batch):
        chunk = items[start : start + args.batch]
        paths = [it["path"] for it in chunk]
        results = model.predict(
            paths, imgsz=args.imgsz, conf=args.conf, iou=args.iou, device=args.device, verbose=False, stream=False
        )
        for it, r in zip(chunk, results):
            src = Path(it["path"])
            h, w = r.orig_shape
            rows: list[str] = []
            if it["label"] != "none" and len(r.boxes):
                # 统一取出"实例框"：框头直接给 xyxy，掩码头则取多边形外接框
                if args.source == "masks" and r.masks is not None and len(r.masks.xy):
                    inst = []
                    for cls_id, poly in zip(r.boxes.cls.tolist(), r.masks.xy):
                        if len(poly) < 3:
                            continue
                        inst.append(
                            (
                                int(cls_id),
                                float(poly[:, 0].min()),
                                float(poly[:, 1].min()),
                                float(poly[:, 0].max()),
                                float(poly[:, 1].max()),
                            )
                        )
                else:
                    inst = [
                        (int(c), *[float(v) for v in box])
                        for c, box in zip(r.boxes.cls.tolist(), r.boxes.xyxy.tolist())
                    ]

                for cid, x1, y1, x2, y2 in inst:
                    if min(x2 - x1, y2 - y1) < args.min_side:
                        continue
                    if (x2 - x1) * (y2 - y1) > args.max_area_frac * w * h:
                        continue
                    cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
                    bw, bh = (x2 - x1) / w, (y2 - y1) / h
                    rows.append(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                    stats[it["split"]][CLASS_NAMES[cid]] += 1
            per_folder[it["label"]]["有框" if rows else "无框"] += 1

            stem = it["stem"]
            link_or_copy(src, args.out / "images" / it["split"] / f"{stem}{src.suffix}")
            (args.out / "labels" / it["split"] / f"{stem}.txt").write_text(
                "\n".join(rows) + ("\n" if rows else ""), encoding="utf-8"
            )

            if (start + chunk.index(it)) in preview_idx:
                img = cv2.imread(str(src))
                for row in rows:
                    p = row.split()
                    cid = int(p[0])
                    cx, cy, bw, bh = (float(v) for v in p[1:])
                    x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
                    x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
                    cv2.rectangle(img, (x1, y1), (x2, y2), PALETTE[cid % 3], 2)
                previews.append(cv2.resize(img, (320, 320)))

        done = min(start + args.batch, len(items))
        if done % 800 < args.batch:
            print(f"  进度 {done}/{len(items)}")

    print("\n各类别伪框数量：")
    for split in ("train", "val", "test"):
        print(f"  {split:5s} {dict(stats[split])}")
    print("\n按文件夹标签统计有无框：")
    for k, v in per_folder.items():
        tot = sum(v.values())
        print(f"  {k:10s} {dict(v)}  （有框率 {v['有框'] / max(tot, 1):.1%}）")

    if previews:
        cols = 6
        rows_n = (len(previews) + cols - 1) // cols
        canvas = np.zeros((rows_n * 320, cols * 320, 3), np.uint8)
        for i, t in enumerate(previews):
            rr, cc = divmod(i, cols)
            canvas[rr * 320 : (rr + 1) * 320, cc * 320 : (cc + 1) * 320] = t
        p = ROOT / "reports" / "pseudo_label_preview.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), canvas)
        print(f"\n伪标注抽样预览 -> {p}")

    names = "".join(f"\n  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    (args.out / "data.yaml").write_text(
        "# 由 src/pseudo_label.py 自动生成\n"
        "# 图像：17338 张真实隧道衬砌照片（cuijingqi/Tunnel_lining_multi-category_...）\n"
        "# 框：由 AFPN-Seg 伪标注器生成，非人工标注\n"
        f"path: {args.out.as_posix()}\n"
        "train: images/train\nval: images/val\ntest: images/test\n"
        f"nc: {len(CLASS_NAMES)}\nnames:{names}\n",
        encoding="utf-8",
    )
    (args.out / "pseudo_stats.json").write_text(
        json.dumps(
            {"per_split": {k: dict(v) for k, v in stats.items()}, "per_folder": {k: dict(v) for k, v in per_folder.items()}},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"数据集 -> {args.out / 'data.yaml'}")


if __name__ == "__main__":
    main()