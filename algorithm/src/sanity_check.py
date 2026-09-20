"""抽检数据集：把标注框画到图上拼成一张 montage，用于人工确认标注是否对齐。

类别名从数据集的 data.yaml 读取，因此对任意数据集通用。
用法：
  python src/sanity_check.py --root data/processed/ttd_det --split val
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np
import yaml

# 各类别的框颜色（BGR），超出长度时循环取用
PALETTE = [(0, 0, 255), (255, 176, 32), (0, 200, 0), (200, 0, 200), (255, 255, 0), (0, 128, 255)]


def load_names(root: Path) -> list[str]:
    """从 data.yaml 读类别名，顺序即 class id。"""
    cfg = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))
    names = cfg["names"]
    if isinstance(names, dict):
        return [names[i] for i in sorted(names)]
    return list(names)


def draw(img_path: Path, lbl_path: Path, names: list[str]) -> np.ndarray:
    """把标注画到图上。自动识别两种格式：
      检测   `cls cx cy w h`          （5 列）
      分割   `cls x1 y1 x2 y2 ...`    （>=7 列，偶数个坐标）
    """
    img = cv2.imread(str(img_path))
    if img is None:
        raise SystemExit(f"读图失败: {img_path}")
    h, w = img.shape[:2]
    txt = lbl_path.read_text(encoding="utf-8").strip() if lbl_path.exists() else ""
    rows = [r.split() for r in txt.splitlines() if r.strip()]
    if not rows:
        cv2.putText(img, "negative", (4, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
    seg_mode = any(len(r) >= 7 and (len(r) - 1) % 2 == 0 for r in rows)
    for r in rows:
        cid = int(r[0])
        color = PALETTE[cid % len(PALETTE)]
        if seg_mode:
            coords = [float(v) for v in r[1:]]
            pts = np.array([[coords[i] * w, coords[i + 1] * h] for i in range(0, len(coords), 2)], np.int32)
            cv2.polylines(img, [pts], True, color, 1)
            x1, y1 = int(pts[:, 0].min()), int(pts[:, 1].min())
            cv2.putText(img, names[cid], (x1, max(y1 - 4, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        else:
            cx, cy, bw, bh = (float(v) for v in r[1:5])
            x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
            x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 1)
            cv2.putText(
                img,
                names[cid] if cid < len(names) else str(cid),
                (x1, max(y1 - 4, 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                color,
                1,
            )
    return img


def montage(imgs: list[np.ndarray], cols: int, cell: int = 224) -> np.ndarray:
    rows = (len(imgs) + cols - 1) // cols
    canvas = np.zeros((rows * cell, cols * cell, 3), dtype=np.uint8)
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        canvas[r * cell : (r + 1) * cell, c * cell : (c + 1) * cell] = cv2.resize(
            im, (cell, cell), interpolation=cv2.INTER_AREA
        )
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(r"f:\tmp\gpr-dl\data\processed\gpr_det"))
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--out", type=Path, default=Path(r"f:\tmp\gpr-dl\reports\sanity_labels.png")
    )
    args = ap.parse_args()

    names = load_names(args.root)
    print(f"类别（按 class id 顺序）: {names}")
    imgs = sorted((args.root / "images" / args.split).iterdir())
    rng = random.Random(args.seed)
    pick = rng.sample(imgs, min(args.n, len(imgs)))
    canvas = montage(
        [draw(p, args.root / "labels" / args.split / f"{p.stem}.txt", names) for p in pick],
        cols=args.cols,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), canvas)
    print(f"抽检 {len(pick)} 张 -> {args.out}")


if __name__ == "__main__":
    main()