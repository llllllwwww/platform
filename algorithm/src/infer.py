"""GPR 病害检测推理：同时支持单图、图片目录、视频三种输入。

之所以要支持视频：这套算法最终要装到检测设备上，设备端拿到的是连续的雷达资料流，
而不是单张图片。视频推理除了画框，还会做"同一病害在连续帧中是否被稳定检出"的统计，
这是图片级 mAP 看不出来的指标。

用法：
  python src/infer.py --weights runs/detect/yolo11s-afpn-e150/weights/best.pt --source data/processed/gpr_det/images/test
  python src/infer.py --weights ... --source data/video/scan_scroll_test.mp4
  python src/infer.py --weights ... --source a.jpg --save-dir reports/pred

视频若提供同名 *_gt.json（由 src/make_video.py 生成），会额外给出逐帧查全/查准。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from detector import install  # noqa: E402

CLASS_CN = {"cavity": "空洞", "utility": "管线"}
COLORS = [(0, 0, 255), (255, 176, 32), (0, 200, 0), (200, 0, 200)]  # BGR，按 class id
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
VID_EXT = {".mp4", ".avi", ".mkv", ".mov", ".webm"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="GPR 病害检测推理（图片/视频）")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--source", required=True, help="图片 / 图片目录 / 视频文件")
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--device", default="0")
    ap.add_argument("--save-dir", default=str(ROOT / "reports" / "predictions"))
    ap.add_argument("--max-images", type=int, default=16, help="图片目录模式下最多处理多少张")
    ap.add_argument("--montage", action="store_true", help="图片目录模式下额外输出拼图")
    return ap.parse_args()


def draw(img: np.ndarray, boxes, names: dict) -> np.ndarray:
    out = img.copy()
    for box in boxes:
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
        cid = int(box.cls[0].item())
        conf = float(box.conf[0].item())
        name = names.get(cid, str(cid))
        color = COLORS[cid % len(COLORS)]
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"{name} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x1, max(y1 - th - 6, 0)), (x1 + tw + 4, y1), color, -1)
        cv2.putText(out, label, (x1 + 2, max(y1 - 4, th)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return out


def match_gt(pred_boxes, gt_rows, iou_thr: float = 0.3) -> tuple[int, int, int]:
    """按类别做贪心 IoU 匹配，返回 (命中, 预测数, 真值数)。IoU 阈值放宽到 0.3：
    GPR 病害边界本就模糊，沿用 COCO 的 0.5 会低估实际可用性。"""
    preds = [(int(b.cls[0].item()), b.xyxy[0].tolist()) for b in pred_boxes]
    gts = [(int(r["cls"]), r["xyxy"]) for r in gt_rows]
    used = set()
    hit = 0
    for pc, pb in preds:
        best_i, best_iou = -1, iou_thr
        for i, (gc, gb) in enumerate(gts):
            if i in used or gc != pc:
                continue
            iou = _iou(pb, gb)
            if iou >= best_iou:
                best_i, best_iou = i, iou
        if best_i >= 0:
            used.add(best_i)
            hit += 1
    return hit, len(preds), len(gts)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(ix2 - ix1, 0), max(iy2 - iy1, 0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def run_images(model, files: list[Path], args, save_dir: Path) -> dict:
    counts: Counter = Counter()
    saved = []
    tiles = []
    for p in files[: args.max_images]:
        frame = cv2.imread(str(p))
        if frame is None:
            print(f"  [跳过] 无法读取 {p}")
            continue
        res = model.predict(frame, imgsz=args.imgsz, conf=args.conf, iou=args.iou, device=args.device, verbose=False)[0]
        vis = draw(frame, res.boxes, res.names)
        dst = save_dir / f"{p.stem}_pred.jpg"
        cv2.imwrite(str(dst), vis)
        saved.append(str(dst))
        for b in res.boxes:
            counts[res.names[int(b.cls[0].item())]] += 1
        if args.montage:
            tiles.append(cv2.resize(vis, (320, 224), interpolation=cv2.INTER_AREA))
        print(f"  {p.name}: 检出 {len(res.boxes)} 个目标")

    if tiles:
        cols = 4
        rows = (len(tiles) + cols - 1) // cols
        canvas = np.zeros((rows * 224, cols * 320, 3), np.uint8)
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            canvas[r * 224 : (r + 1) * 224, c * 320 : (c + 1) * 320] = t
        mp = save_dir / "montage_predictions.png"
        cv2.imwrite(str(mp), canvas)
        print(f"  拼图 -> {mp}")

    return {"mode": "images", "n_images": len(saved), "per_class_detections": dict(counts), "saved": saved}


def run_video(model, path: Path, args, save_dir: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"视频 {path.name}: {w}x{h} @ {fps:.0f}fps, {total} 帧")

    gt_path = path.with_name(path.stem + "_gt.json")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))["frames"] if gt_path.exists() else None
    if gt:
        print(f"找到逐帧真值: {gt_path.name}，将统计逐帧查全/查准")

    out_path = save_dir / f"{path.stem}_pred.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    counts: Counter = Counter()
    per_frame = []
    hit_sum = pred_sum = gt_sum = 0
    frames_with_det = 0
    preview = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        res = model.predict(frame, imgsz=args.imgsz, conf=args.conf, iou=args.iou, device=args.device, verbose=False)[0]
        vis = draw(frame, res.boxes, res.names)
        cv2.putText(vis, f"frame {idx}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        writer.write(vis)

        n_det = len(res.boxes)
        if n_det:
            frames_with_det += 1
            for b in res.boxes:
                counts[res.names[int(b.cls[0].item())]] += 1
        row = {"frame": idx, "n_pred": n_det}
        if gt is not None:
            rows = gt.get(str(idx), [])
            hit, np_, ng = match_gt(res.boxes, rows)
            hit_sum += hit
            pred_sum += np_
            gt_sum += ng
            row.update({"n_gt": ng, "hit": hit})
        per_frame.append(row)

        if idx % max(1, total // 6) == 0:
            preview.append(vis.copy())
        idx += 1

    cap.release()
    writer.release()

    if preview:
        cv2.imwrite(str(save_dir / f"{path.stem}_preview.png"), np.vstack(preview))

    summary = {
        "mode": "video",
        "video": str(path),
        "frames": idx,
        "frames_with_detection": frames_with_det,
        "per_class_detections": dict(counts),
        "output_video": str(out_path),
    }
    if gt is not None and gt_sum > 0:
        summary["frame_level"] = {
            "iou_threshold": 0.3,
            "recall": round(hit_sum / gt_sum, 4),
            "precision": round(hit_sum / pred_sum, 4) if pred_sum else 0.0,
            "gt_instances": gt_sum,
            "pred_instances": pred_sum,
            "matched": hit_sum,
        }
        print(
            f"逐帧统计(IoU>=0.3): 真值 {gt_sum} / 预测 {pred_sum} / 命中 {hit_sum} -> "
            f"查全 {summary['frame_level']['recall']:.3f}，查准 {summary['frame_level']['precision']:.3f}"
        )
    print(f"检出分布: {dict(counts)}")
    print(f"标注视频 -> {out_path}")
    return summary


def main() -> None:
    args = parse_args()
    install()
    from ultralytics import YOLO

    src = Path(args.source)
    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(args.weights)
    print(f"模型: {args.weights}  类别: {model.names}")

    if src.is_dir():
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXT)
        if not files:
            raise SystemExit(f"目录中没有图片: {src}")
        print(f"图片目录模式：{len(files)} 张，处理前 {args.max_images} 张")
        summary = run_images(model, files, args, save_dir)
    elif src.suffix.lower() in VID_EXT:
        summary = run_video(model, src, args, save_dir)
    elif src.suffix.lower() in IMG_EXT:
        summary = run_images(model, [src], args, save_dir)
    else:
        raise SystemExit(f"无法识别的输入类型: {src}")

    out = save_dir / "infer_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"汇总 -> {out}")


if __name__ == "__main__":
    main()