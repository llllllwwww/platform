"""为 17338 张真实隧道衬砌照片建立切分清单（供伪标注与检测器训练共用）。

数据：cuijingqi/Tunnel_lining_multi-category_defect_segmentation_detection
      dataset/{crack, leakage, no defects, defects}/images/*.jpg
      只有文件夹级标签（裂缝 / 渗漏 / 无病害），没有框和掩码 —— 框由伪标注器生成。

切分的关键点
------------
文件名形如 `1_0116-2.jpg`、`5_n99.jpg`、`0_image-13-144.jpg`，
前缀是采集批次，尾部数字是序列内帧号。同一序列的相邻帧高度重复，
若随机切分会让近乎相同的图同时出现在训练集和验证集，指标虚高。
因此这里按 **(批次前缀, 帧号 // 块大小)** 分组切分，让同序列的连续帧留在同一集合。
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

# 文件夹标签 -> 语义
FOLDER_LABEL = {
    "crack": "crack",
    "leakage": "leakage",
    "no defects": "none",
    "defects": "mixed",
}


def frame_index(stem: str) -> int:
    """取文件名中最后一个整数作为帧号；取不到时返回 0。"""
    nums = re.findall(r"\d+", stem)
    return int(nums[-1]) if nums else 0


def prefix_of(stem: str) -> str:
    return stem.split("_")[0]


def scan(root: Path) -> list[dict]:
    items = []
    for folder, label in FOLDER_LABEL.items():
        d = root / folder / "images"
        if not d.is_dir():
            d = root / folder
        if not d.is_dir():
            print(f"  [跳过] 不存在: {root / folder}")
            continue
        files = sorted(p for p in d.rglob("*") if p.suffix.lower() in IMG_EXT)
        for p in files:
            items.append(
                {
                    "path": str(p),
                    "stem": p.stem,
                    "folder": folder,
                    "label": label,
                    "prefix": prefix_of(p.stem),
                    "frame": frame_index(p.stem),
                }
            )
        print(f"  {folder:12s} {len(files):6d} 张  -> 标签 {label}")
    return items


def default_scene_root() -> Path:
    """定位下载脚本解出来的 dataset 目录。

    download_data.py 解压后目录名带分支后缀（-master / -main），这里用通配匹配，
    避免把某台机器的绝对盘符路径写死（原先是 f:\\tmp\\gpr-dl\\...）。
    """
    base = ROOT / "data" / "raw" / "tunnel_lining" / "extracted" / "extracted"
    for d in sorted(base.glob("*/dataset")):
        return d
    return base


def main() -> None:
    ap = argparse.ArgumentParser(description="建立隧道场景照片的切分清单")
    ap.add_argument("--root", type=Path, default=default_scene_root())
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed" / "scene_split.json")
    ap.add_argument("--block", type=int, default=25, help="同序列连续多少帧绑定为一个切分单元")
    ap.add_argument("--ratios", default="0.8,0.1,0.1")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"数据目录不存在: {args.root}")

    items = scan(args.root)
    print(f"\n合计 {len(items)} 张")

    # 每个文件夹标签单独切分，保证三个集合里各类都有
    rng = random.Random(args.seed)
    ratios = [float(x) for x in args.ratios.split(",")]
    for it in items:
        it["block_key"] = f"{it['label']}|{it['prefix']}|{it['frame'] // args.block}"

    by_label: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for it in items:
        by_label[it["label"]][it["block_key"]].append(it)

    for label, blocks in by_label.items():
        keys = sorted(blocks)
        rng.shuffle(keys)
        n = len(keys)
        n_train = int(round(n * ratios[0]))
        n_val = int(round(n * ratios[1]))
        assign = {
            "train": keys[:n_train],
            "val": keys[n_train : n_train + n_val],
            "test": keys[n_train + n_val :],
        }
        for split, ks in assign.items():
            for k in ks:
                for it in blocks[k]:
                    it["split"] = split

    # 统计
    print("\n切分结果（按帧块分组）：")
    table: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        table[it["split"]][it["label"]] += 1
    for split in ("train", "val", "test"):
        total = sum(table[split].values())
        print(f"  {split:5s} {total:6d} 张  {dict(table[split])}")

    # 一致性检查：同一 block_key 不能跨集合
    seen: dict[str, str] = {}
    leak = 0
    for it in items:
        k = it["block_key"]
        if k in seen and seen[k] != it["split"]:
            leak += 1
        seen[k] = it["split"]
    print(f"跨集合泄漏的帧块: {leak} （应为 0）")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "root": str(args.root),
                "block": args.block,
                "ratios": ratios,
                "seed": args.seed,
                "summary": {s: dict(table[s]) for s in ("train", "val", "test")},
                "items": items,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n清单 -> {args.out}")
    if leak:
        raise SystemExit("存在跨集合泄漏，切分逻辑有误")


if __name__ == "__main__":
    main()