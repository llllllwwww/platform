"""用真实实测 GPR 剖面对合成"扫描视频"，模拟检测车车载雷达的实时滚动画面。

为什么要这么做：本任务最终要迁移到检测设备上，设备端拿到的是连续的雷达资料流，
而不是单张图片。公开的真实 GPR 视频几乎没有带标注的，所以这里用**真实实测剖面**
（Mendeley 数据集里带标注的 224x224 剖面）按里程拼接成长条雷达图，
再用滑动窗口逐帧裁切，得到：

  - 视觉上是连续的滚动雷达图（和现场采集软件的画面一致）
  - 每一帧都有由原始标注平移得到的真值框，可定量核验视频推理的连续性

产物：
  data/video/scan_scroll.mp4     扫描视频（原始数据，无叠加）
  data/video/scan_scroll_gt.json 逐帧真值框（像素坐标，归一化）
  reports/video_frames_preview.png 抽帧预览（含真值框，供人工确认）
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CLASS_NAMES = ["cavity", "utility"]


def read_label(path: Path) -> list[tuple[int, float, float, float, float]]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) == 5:
            out.append((int(p[0]), *(float(v) for v in p[1:])))
    return out


def pick_patches(images_dir: Path, labels_dir: Path, n_per_class: dict[str, int], rng: random.Random):
    """按类别挑选剖面，返回 (图片路径, 该类别的框列表)。"""
    by_cls: dict[str, list[tuple[Path, str]]] = {c: [] for c in CLASS_NAMES + ["intact"]}
    for img in sorted(images_dir.iterdir()):
        cls = img.stem.split("_", 1)[0]  # 构建数据集时加了 "cavity_"/"utility_"/"intact_" 前缀
        if cls in by_cls:
            by_cls[cls].append((img, cls))

    picked = []
    for cls, k in n_per_class.items():
        pool = by_cls.get(cls, [])
        if not pool:
            continue
        take = min(k, len(pool))
        for img, c in rng.sample(pool, take):
            boxes = read_label(labels_dir / f"{img.stem}.txt")
            picked.append((img, c, boxes))
    rng.shuffle(picked)
    return picked


def build_strip(picked, target_h: int = 224):
    """把剖面统一缩放到同一高度后横向拼成长条雷达图。

    注意：负样本（intact）的原始尺寸不是 224x224（约 540x260），
    直接拼接会因高度不一致失败，所以要按目标高度等比缩放。
    真值框在缩放之后按新的宽高换算，保证框与图对齐。
    """
    tiles = []
    boxes_global = []  # (x1, y1, x2, y2, cls)
    x_cursor = 0
    for img_path, _cls, boxes in picked:
        img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        h0, w0 = img.shape[:2]
        w = max(1, round(w0 * target_h / h0))
        img = cv2.resize(img, (w, target_h), interpolation=cv2.INTER_AREA)
        tiles.append(img)
        for cid, cx, cy, bw, bh in boxes:
            boxes_global.append(
                (
                    x_cursor + (cx - bw / 2) * w,
                    (cy - bh / 2) * target_h,
                    x_cursor + (cx + bw / 2) * w,
                    (cy + bh / 2) * target_h,
                    cid,
                )
            )
        x_cursor += w
    strip = np.hstack(tiles) if tiles else np.zeros((target_h, 1), np.uint8)
    return strip, boxes_global


def main() -> None:
    ap = argparse.ArgumentParser(description="用真实实测剖面对合成扫描视频")
    ap.add_argument("--root", type=Path, default=ROOT / "data" / "processed" / "gpr_det")
    ap.add_argument("--split", default="test", help="用哪个切分的剖面来拼（默认 test，避免与训练重叠）")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "video")
    ap.add_argument("--frames", type=int, default=600, help="总帧数")
    ap.add_argument("--frame-w", type=int, default=640)
    ap.add_argument("--stride", type=int, default=16, help="每帧窗口前进的像素数")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--n-cavity", type=int, default=40)
    ap.add_argument("--n-utility", type=int, default=40)
    ap.add_argument("--n-intact", type=int, default=60)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    images_dir = args.root / "images" / args.split
    labels_dir = args.root / "labels" / args.split
    picked = pick_patches(
        images_dir,
        labels_dir,
        {"cavity": args.n_cavity, "utility": args.n_utility, "intact": args.n_intact},
        rng,
    )
    counts = {c: sum(1 for _, cc, _ in picked if cc == c) for c in CLASS_NAMES + ["intact"]}
    print(f"选用剖面 {len(picked)} 张，类别分布 {counts}")

    strip, boxes_global = build_strip(picked)
    h, strip_w = strip.shape[:2]
    print(f"长条雷达图: {strip_w}x{h}，真值框 {len(boxes_global)} 个")

    # 长度不够时继续拼接：正向接一段、再镜像接一段（镜像段的框要水平翻转）
    need = args.frames * args.stride + args.frame_w
    parts, boxes_ext = [strip], list(boxes_global)
    mirrored = False
    while sum(p.shape[1] for p in parts) < need:
        base = sum(p.shape[1] for p in parts)
        seg_w = strip.shape[1]
        if mirrored:
            parts.append(cv2.flip(strip, 1))
            boxes_ext += [(base + seg_w - x2, y1, base + seg_w - x1, y2, cid) for x1, y1, x2, y2, cid in boxes_global]
        else:
            parts.append(strip)
            boxes_ext += [(x1 + base, y1, x2 + base, y2, cid) for x1, y1, x2, y2, cid in boxes_global]
        mirrored = not mirrored
    strip, boxes_global = np.hstack(parts), boxes_ext
    print(f"扩展后长条雷达图: {strip.shape[1]}x{h}，真值框 {len(boxes_global)} 个")

    args.out.mkdir(parents=True, exist_ok=True)
    video_path = args.out / f"scan_scroll_{args.split}.mp4"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (args.frame_w, h)
    )
    if not writer.isOpened():
        raise SystemExit("无法创建视频文件（检查 opencv 的 mp4v 编码器）")

    gt: dict[str, list] = {}
    preview_frames = []
    for i in range(args.frames):
        x0 = i * args.stride
        frame = strip[:, x0 : x0 + args.frame_w]
        writer.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))

        rows = []
        for x1, y1, x2, y2, cid in boxes_global:
            if x2 <= x0 or x1 >= x0 + args.frame_w:  # 不在窗口内
                continue
            nx1, nx2 = max(x1 - x0, 0), min(x2 - x0, args.frame_w)
            if nx2 - nx1 < 2:  # 几乎被裁掉的残缺框
                continue
            rows.append(
                {
                    "cls": int(cid),
                    "name": CLASS_NAMES[int(cid)] if int(cid) < len(CLASS_NAMES) else str(cid),
                    "xyxy": [round(nx1, 2), round(y1, 2), round(nx2, 2), round(y2, 2)],
                    "xyxyn": [
                        round(nx1 / args.frame_w, 6),
                        round(y1 / h, 6),
                        round(nx2 / args.frame_w, 6),
                        round(y2 / h, 6),
                    ],
                }
            )
        gt[str(i)] = rows

        if i % max(1, args.frames // 8) == 0:
            vis = cv2.cvtColor(frame.copy(), cv2.COLOR_GRAY2BGR)
            for r in rows:
                x1, y1, x2, y2 = (int(v) for v in r["xyxy"])
                color = (0, 0, 255) if r["cls"] == 0 else (255, 176, 32)
                cv2.rectangle(vis, (x1, y1), (x2, y2), color, 1)
                cv2.putText(vis, r["name"], (x1, max(y1 - 3, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
            preview_frames.append(vis)

    writer.release()

    (args.out / f"scan_scroll_{args.split}_gt.json").write_text(
        json.dumps(
            {
                "video": video_path.name,
                "frame_width": args.frame_w,
                "frame_height": h,
                "fps": args.fps,
                "stride": args.stride,
                "split": args.split,
                "class_names": CLASS_NAMES,
                "frames": gt,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if preview_frames:
        preview = np.vstack(preview_frames)
        p = ROOT / "reports" / "video_frames_preview.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), preview)
        print(f"抽帧预览（含真值框）-> {p}")

    with_boxes = sum(1 for v in gt.values() if v)
    print(f"视频: {video_path}  {args.frames} 帧 / {args.fps}fps")
    print(f"其中 {with_boxes} 帧含目标（{with_boxes / args.frames:.1%}）")


if __name__ == "__main__":
    main()