"""构建"隧道病害分割"数据集，用于训练伪标注器。

为什么需要这一步
----------------
真实隧道照片（17338 张）只有文件夹级的类别标签，没有框/掩码。
要训检测器就得先有个"标注器"来生成框，而这个标注器需要有像素级监督。
本脚本把两份**带像素掩码**的数据整合成 YOLO-seg 格式：

  1. TTD (TACK Tunnel Data, arXiv:2512.14477)
     真实隧道衬砌，掩码像素值即类别：40=Crack, 160=Water, 200=Leaching
  2. CTCD (Complex Tunnel Crack Dataset, Structural Control and Health Monitoring 2025)
     52 座公路隧道巡检车采集的隧道裂缝掩码

输出 YOLO-seg：labels 每行 `cls x1 y1 x2 y2 ... xn yn`（归一化多边形）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

CLASS_NAMES = ["crack", "water", "leaching"]
CLASS_CN = {"crack": "裂缝", "water": "渗水", "leaching": "析出物"}
# TTD 掩码像素值 -> class id
TTD_MASK_VALUES = {40: 0, 160: 1, 200: 2}


def ttd_mask_name(img_name: str) -> str:
    """TTD 的掩码名与图片名不同：A.png -> _fuse_A_1band.png（全量已验证一一对应）。"""
    stem, ext = img_name.rsplit(".", 1)
    head, tail = stem.rsplit("_", 1)
    return f"{head}_fuse_{tail}_1band.{ext}"


def mask_to_polygons(
    binary: np.ndarray, min_area: int, max_polys: int, eps_ratio: float = 0.002
) -> list[np.ndarray]:
    """二值掩码 -> 多边形列表。

    细长裂缝经 approxPolyDP 后可能退化成少于 3 个点（YOLO-seg 会判为非法），
    这类目标用外接矩形兜底，保证几何信息不丢。
    """
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for c in contours:
        if cv2.contourArea(c) < min_area:
            # 退化细线：面积小但外接框够大时仍保留
            x, y, w, h = cv2.boundingRect(c)
            if max(w, h) < 12 or min(w, h) < 1:
                continue
        approx = cv2.approxPolyDP(c, eps_ratio * cv2.arcLength(c, True), True).reshape(-1, 2)
        if len(approx) < 3:
            x, y, w, h = cv2.boundingRect(c)
            approx = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.int32)
        polys.append(approx)
    if len(polys) > max_polys:
        polys.sort(key=lambda p: cv2.contourArea(p.reshape(-1, 1, 2)), reverse=True)
        polys = polys[:max_polys]
    return polys


def label_lines(polys: list[np.ndarray], cid: int, w: int, h: int) -> list[str]:
    rows = []
    for p in polys:
        pts = []
        for x, y in p:
            pts.append(f"{min(max(x / w, 0.0), 1.0):.6f} {min(max(y / h, 0.0), 1.0):.6f}")
        if len(pts) >= 3:
            rows.append(f"{cid} " + " ".join(pts))
    return rows


def collect_ttd(root: Path) -> list[dict]:
    """TTD：图片 + 同名规则掩码。按原始帧（去掉末尾视角字母）分组，避免同帧多视角跨集合。"""
    img_dir, mask_dir = root / "3_img", root / "3_mask"
    if not img_dir.is_dir() or not mask_dir.is_dir():
        print(f"  [跳过] TTD 目录不完整: {img_dir}")
        return []
    items = []
    miss = 0
    for p in sorted(img_dir.glob("*.png")):
        mp = mask_dir / ttd_mask_name(p.name)
        if not mp.exists():
            miss += 1
            continue
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        mask = cv2.imread(str(mp), cv2.IMREAD_GRAYSCALE)
        if img is None or mask is None or img.shape[:2] != mask.shape[:2]:
            miss += 1
            continue
        stem = p.stem
        group = stem.rsplit("_", 1)[0]  # TA_Camera3_000001_A -> TA_Camera3_000001
        items.append({"src": "ttd", "img": img, "mask": mask, "group": group, "name": f"ttd_{stem}"})
    print(f"  TTD: {len(items)} 张可用（缺掩码/尺寸不符 {miss}）")
    return items


def collect_ctcd(root: Path) -> list[dict]:
    """CTCD：train/trainannot、val/valannot 结构，掩码为二值裂缝图，全部归到 crack。"""
    base = root / "Complex Tunnel Crack Dataset (CTCD)"
    if not base.is_dir():
        print(f"  [跳过] CTCD 目录不存在: {base}")
        return []
    items = []
    for img_sub, msk_sub in [("train", "trainannot"), ("val", "valannot")]:
        idir, mdir = base / img_sub, base / msk_sub
        if not idir.is_dir() or not mdir.is_dir():
            continue
        for p in sorted(idir.iterdir()):
            mp = mdir / p.name
            if not mp.exists():
                continue
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            mask = cv2.imread(str(mp), cv2.IMREAD_GRAYSCALE)
            if img is None or mask is None or img.shape[:2] != mask.shape[:2]:
                continue
            items.append({"src": "ctcd", "img": img, "mask": mask, "group": f"ctcd_{p.stem}", "name": f"ctcd_{p.stem}"})
    print(f"  CTCD: {len(items)} 张可用")
    return items


def items_to_labels(item: dict, min_area: int, max_polys: int) -> list[str]:
    """按数据来源把掩码拆成类别多边形。"""
    mask = item["mask"]
    h, w = mask.shape[:2]
    rows: list[str] = []
    if item["src"] == "ttd":
        for val, cid in TTD_MASK_VALUES.items():
            binary = (mask == val).astype(np.uint8)
            if binary.max() == 0:
                continue
            rows += label_lines(mask_to_polygons(binary, min_area, max_polys), cid, w, h)
    else:  # ctcd：单类二值裂缝
        binary = (mask > 127).astype(np.uint8)
        if binary.max() > 0:
            rows += label_lines(mask_to_polygons(binary, min_area, max_polys), 0, w, h)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="构建隧道病害分割数据集（伪标注器训练用）")
    ap.add_argument("--ttd", type=Path, default=Path(r"f:\tmp\gpr-dl\data\raw\ttd"))
    ap.add_argument("--ctcd", type=Path, default=Path(r"f:\tmp\gpr-dl\data\raw\ctcd"))
    ap.add_argument("--out", type=Path, default=Path(r"f:\tmp\gpr-dl\data\processed\ttd_seg"))
    ap.add_argument("--min-area", type=int, default=40, help="连通域最小像素面积")
    ap.add_argument("--max-polys", type=int, default=40, help="单类单图最多保留的多边形数")
    ap.add_argument("--neg-keep-prob", type=float, default=0.5, help="无病害图的保留概率（作为负样本）")
    ap.add_argument("--ratios", default="0.8,0.1,0.1")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    items = collect_ttd(args.ttd) + collect_ctcd(args.ctcd)
    if not items:
        raise SystemExit("没有可用数据，检查 TTD / CTCD 是否下载完整")

    # ---- 逐张解析标签 ----
    kept, dropped_empty = [], 0
    for it in items:
        rows = items_to_labels(it, args.min_area, args.max_polys)
        if not rows:
            if rng.random() > args.neg_keep_prob:
                dropped_empty += 1
                continue
        it["rows"] = rows
        kept.append(it)
    print(f"\n保留 {len(kept)} 张（丢弃 {dropped_empty} 张无病害图）")

    # ---- 按源分组切分（避免同一原始帧/同序列跨集合）----
    by_group: dict[str, list[dict]] = defaultdict(list)
    for it in kept:
        by_group[it["group"]].append(it)
    groups = sorted(by_group)
    rng.shuffle(groups)
    ratios = [float(x) for x in args.ratios.split(",")]
    n = len(groups)
    n_train = int(round(n * ratios[0]))
    n_val = int(round(n * ratios[1]))
    buckets = {
        "train": groups[:n_train],
        "val": groups[n_train : n_train + n_val],
        "test": groups[n_train + n_val :],
    }

    if args.out.exists():
        shutil.rmtree(args.out)
    for name in buckets:
        (args.out / "images" / name).mkdir(parents=True, exist_ok=True)
        (args.out / "labels" / name).mkdir(parents=True, exist_ok=True)

    manifest, counts, poly_total = {}, defaultdict(lambda: Counter()), 0
    for name, gs in buckets.items():
        n_polys = 0
        for g in gs:
            for it in by_group[g]:
                cv2.imwrite(str(args.out / "images" / name / f"{it['name']}.jpg"), it["img"], [cv2.IMWRITE_JPEG_QUALITY, 95])
                (args.out / "labels" / name / f"{it['name']}.txt").write_text(
                    "\n".join(it["rows"]) + ("\n" if it["rows"] else ""), encoding="utf-8"
                )
                for r in it["rows"]:
                    counts[name][CLASS_NAMES[int(r.split()[0])]] += 1
                    n_polys += 1
        manifest[name] = {"groups": len(gs), "images": sum(len(by_group[g]) for g in gs), "polygons": n_polys, "per_class": dict(counts[name])}
        poly_total += n_polys
        print(f"  {name:5s} 源组 {manifest[name]['groups']:4d}  图像 {manifest[name]['images']:5d}  多边形 {n_polys:6d}  {dict(counts[name])}")

    (args.out / "split_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    names = "".join(f"\n  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    (args.out / "data.yaml").write_text(
        "# 由 src/build_dataset_seg.py 自动生成，用于训练伪标注器\n"
        "# 数据来源：TTD (arXiv:2512.14477) + CTCD (Struct Control Health Monit, 2025)\n"
        f"path: {args.out.as_posix()}\n"
        "train: images/train\nval: images/val\ntest: images/test\n"
        f"nc: {len(CLASS_NAMES)}\nnames:{names}\n",
        encoding="utf-8",
    )
    print(f"\n共 {poly_total} 个多边形 -> {args.out}")

    if not verify(args.out):
        raise SystemExit("数据集自检未通过")


def verify(out: Path) -> bool:
    """YOLO-seg 自检：每个多边形点数 >= 3、坐标为 [0,1]、类别合法、图片标签一一对应。"""
    ok = True
    for name in ("train", "val", "test"):
        imgs = {p.stem for p in (out / "images" / name).iterdir()}
        lbls = {p.stem for p in (out / "labels" / name).iterdir()}
        if imgs != lbls:
            ok = False
            print(f"  [错误] {name}: 图片与标签不匹配 {sorted(imgs ^ lbls)[:5]}")
        bad = 0
        for stem in lbls:
            for line in (out / "labels" / name / f"{stem}.txt").read_text().splitlines():
                if not line.strip():
                    continue
                parts = line.split()
                cid, coords = int(parts[0]), [float(v) for v in parts[1:]]
                if cid not in range(len(CLASS_NAMES)) or len(coords) < 6 or len(coords) % 2 or any(v < 0 or v > 1 for v in coords):
                    bad += 1
        if bad:
            ok = False
            print(f"  [错误] {name}: {bad} 条非法分割标注")
    print("自检通过：分割标注合法、图片标签一一对应" if ok else "自检未通过")
    return ok


if __name__ == "__main__":
    main()