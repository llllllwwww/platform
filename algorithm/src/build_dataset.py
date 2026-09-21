"""把 Mendeley GPR 实测数据集整理成 YOLO 检测格式。

数据来源：Mojahid et al., Data in Brief 2025, doi:10.17632/ww7fd9t325.1
  - augmented_cavities  553 张 224x224，类别 cavities（地下空洞）
  - augmented_utilities 786 张 224x224，类别 Utility（地下管线）
  - augmented_intact    900 张无病害剖面，作为负样本（抑制误报）
  - 原始剖面 cavities/Utilities/intact 保留，供视频流与可视化使用

切分要点：增强样本共享同一源剖面，必须按源分组切分，否则验证集指标虚高。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 类别顺序即 YOLO 的 class id，必须与 data.yaml 的 names 完全一致
CLASS_NAMES = ["cavity", "utility"]
CLASS_CN = {"cavity": "空洞", "utility": "管线"}

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def source_key(stem: str) -> str:
    """从增强文件名还原源剖面 ID：10_aug_10 -> 10，001_aug_6 -> 001。"""
    return re.split(r"_aug_", stem, maxsplit=1)[0]


def collect(root: Path) -> list[dict]:
    """收集全部有标注的样本，返回 (图片, 标签, 类别, 源ID) 列表。"""
    items: list[dict] = []
    subdirs = {
        "augmented_cavities": "cavity",
        "augmented_utilities": "utility",
    }
    for sub, cls in subdirs.items():
        img_dir = root / sub
        lbl_dir = img_dir / "annotations" / "Yolo_format"
        if not lbl_dir.is_dir():
            raise FileNotFoundError(f"缺少标注目录: {lbl_dir}")
        labels = {p.stem: p for p in lbl_dir.glob("*.txt")}
        for img in sorted(img_dir.iterdir()):
            if img.suffix.lower() not in IMG_EXT:
                continue
            lbl = labels.get(img.stem)
            if lbl is None:
                continue
            items.append(
                {
                    "image": img,
                    "label": lbl,
                    "cls": cls,
                    "src": source_key(img.stem),
                }
            )
    return items


def collect_negatives(root: Path) -> list[dict]:
    """无病害剖面作为负样本：只给图，不给任何框。"""
    img_dir = root / "augmented_intact"
    if not img_dir.is_dir():
        return []
    return [
        {"image": p, "label": None, "cls": "intact", "src": source_key(p.stem)}
        for p in sorted(img_dir.iterdir())
        if p.suffix.lower() in IMG_EXT
    ]


def group_split(items: list[dict], ratios=(0.7, 0.15, 0.15), seed: int = 42) -> dict[str, list[dict]]:
    """按源剖面分组切分，同一源的全部增强样本只进同一个集合。

    对每个类别分别按源分组随机打乱后再分配，保证类别比例与集合比例一致。
    """
    rng = random.Random(seed)
    by_cls: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for it in items:
        by_cls[it["cls"]][it["src"]].append(it)

    splits: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    names = ["train", "val", "test"]
    for cls, groups in by_cls.items():
        keys = sorted(groups)
        rng.shuffle(keys)
        n = len(keys)
        n_train = int(round(n * ratios[0]))
        n_val = int(round(n * ratios[1]))
        # 至少给每个非空集合留一个源，避免小类别验证集为空
        n_train = min(max(n_train, 1), max(n - 2, 1))
        n_val = min(max(n_val, 1), n - n_train)
        buckets = {
            "train": keys[:n_train],
            "val": keys[n_train : n_train + n_val],
            "test": keys[n_train + n_val :],
        }
        for name in names:
            for k in buckets[name]:
                splits[name].extend(groups[k])
        print(
            f"  [{cls:8s}] 源剖面 {n} 个 -> train {len(buckets['train'])} / "
            f"val {len(buckets['val'])} / test {len(buckets['test'])}"
        )
    for name in names:
        rng.shuffle(splits[name])
    return splits


def write_split(splits: dict[str, list[dict]], out: Path) -> None:
    for name in ("train", "val", "test"):
        (out / "images" / name).mkdir(parents=True, exist_ok=True)
        (out / "labels" / name).mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {}
    for name, items in splits.items():
        cls_counter: Counter = Counter()
        boxes = 0
        dropped = 0
        for it in items:
            # 文件名必须以类别为前缀：三个子目录的源剖面编号会重名
            # （augmented_cavities 的 "1" 与 augmented_intact 的 "1"），
            # 直接合并会让负样本和病害样本互相覆盖、标签串味。
            stem = f"{it['cls']}_{it['image'].stem}"
            shutil.copy2(it["image"], out / "images" / name / f"{stem}{it['image'].suffix}")
            dst_lbl = out / "labels" / name / f"{stem}.txt"
            if it["label"] is None:
                # 负样本：创建空标签文件，YOLO 会把它当作"无目标"图像
                dst_lbl.write_text("", encoding="utf-8")
            else:
                rows = []
                cid = CLASS_NAMES.index(it["cls"])
                for line in it["label"].read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) != 5:
                        dropped += 1
                        continue
                    cx, cy, bw, bh = (float(v) for v in parts[1:])
                    # 原始数据集里存在高度为 0 的退化框，直接丢弃
                    if bw <= 0 or bh <= 0:
                        dropped += 1
                        continue
                    cx = min(max(cx, 0.0), 1.0)
                    cy = min(max(cy, 0.0), 1.0)
                    bw = min(bw, 1.0)
                    bh = min(bh, 1.0)
                    rows.append(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                boxes += len(rows)
                dst_lbl.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
            cls_counter[it["cls"]] += 1
        manifest[name] = {
            "images": len(items),
            "boxes": boxes,
            "dropped_boxes": dropped,
            "per_class": dict(cls_counter),
        }
    (out / "split_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n切分结果：")
    for name, m in manifest.items():
        extra = f"  丢弃退化框 {m['dropped_boxes']}" if m["dropped_boxes"] else ""
        print(f"  {name:5s} 图像 {m['images']:5d}  标注框 {m['boxes']:5d}  {m['per_class']}{extra}")


def verify(out: Path) -> bool:
    """自检：图片与标签一一对应、标签合法。写盘后立刻验证，避免各类静默错误。"""
    ok = True
    for name in ("train", "val", "test"):
        imgs = {p.stem for p in (out / "images" / name).iterdir()}
        lbls = {p.stem for p in (out / "labels" / name).iterdir()}
        if imgs != lbls:
            ok = False
            print(f"  [错误] {name}: 图片与标签不匹配，缺标签 {sorted(imgs - lbls)[:5]}，缺图片 {sorted(lbls - imgs)[:5]}")
        bad = []
        for stem in lbls:
            for ln, line in enumerate((out / "labels" / name / f"{stem}.txt").read_text().splitlines(), 1):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) != 5 or int(parts[0]) not in range(len(CLASS_NAMES)):
                    bad.append((stem, ln, line))
                    continue
                vals = [float(v) for v in parts[1:]]
                if any(v < 0 or v > 1 for v in vals) or vals[2] <= 0 or vals[3] <= 0:
                    bad.append((stem, ln, line))
        if bad:
            ok = False
            print(f"  [错误] {name}: {len(bad)} 条非法标签，例如 {bad[:3]}")
    print("自检通过：图片/标签一一对应，标注范围合法" if ok else "自检未通过，见上方错误")
    return ok


def write_yaml(out: Path) -> None:
    names = "".join(f"\n  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    text = (
        "# 由 src/build_dataset.py 自动生成，请勿手工编辑\n"
        f"path: {out.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        f"nc: {len(CLASS_NAMES)}\n"
        f"names:{names}\n"
    )
    (out / "data.yaml").write_text(text, encoding="utf-8")
    print(f"\n已写入 {out / 'data.yaml'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="构建 YOLO 格式 GPR 检测数据集")
    ap.add_argument(
        "--raw",
        type=Path,
        default=ROOT / "data" / "raw" / "GPR_data_extracted" / "GPR_data",
        help="解压后的原始数据集根目录",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "processed" / "gpr_det",
        help="输出目录",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-negative", action="store_true", help="不纳入 intact 负样本")
    args = ap.parse_args()

    if not args.raw.is_dir():
        raise SystemExit(f"原始数据目录不存在: {args.raw}")
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    items = collect(args.raw)
    print(f"收集到有标注样本 {len(items)} 张（空洞 + 管线）")
    if not args.no_negative:
        neg = collect_negatives(args.raw)
        print(f"收集到负样本 {len(neg)} 张（intact，无框）")
        items = items + neg

    splits = group_split(items, seed=args.seed)
    write_split(splits, args.out)
    if not verify(args.out):
        raise SystemExit("数据集自检未通过")
    write_yaml(args.out)


if __name__ == "__main__":
    main()