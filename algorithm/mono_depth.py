"""Monocular metric-depth in SfM scale with Depth Anything V2 (Apache-2.0 Small).

Why: COLMAP patch-match MVS dies on weak-textured tunnels (axial streaks, holes);
a pretrained monocular depth network carries a learned shape prior that
generalises to any new tunnel/corridor video with zero per-scene tuning.

Pipeline (deep-learning CV feeding the graphics stack):
1. read the undistorted COLMAP workspace (pinhole poses + sparse points),
2. Depth-Anything-V2-Small infers relative depth per frame (GPU),
3. metric anchoring per frame: sparse 3D points projected into the frame give
   reference depths; the median ratio (reference / predicted) converts the
   network's relative output into SfM-scale depth,
4. back-project (subsampled) pixels through K and the pose into a fused point
   cloud usable by dense_tsdf.py (mono depth maps are written as .npz).

Honesty: relative-depth anchoring assumes the network's depth ORDERING is right
and the SfM sparse points are the metric reference; accuracy claims stay
internal (median |log ratio| spread is reported, not survey error).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from mesh_io import write_ply
from dense_tsdf import read_frames

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = r"F:\data\WB\models\hf\hub\models--depth-anything--Depth-Anything-V2-Small-hf\snapshots\5426e4f0f36572d16453bbda7a8389317b1bef99"


DA2_SIZE = 518
DA2_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DA2_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def load_model(model_id: str, device: str):
    """Depth-Anything-V2 via transformers, manual preprocessing (no torchvision)."""
    from transformers import AutoModelForDepthEstimation

    model = AutoModelForDepthEstimation.from_pretrained(model_id, torch_dtype=torch.float16).to(device).eval()
    return model


def infer_depth(model, image_rgb: np.ndarray, device: str) -> np.ndarray:
    """DA2 inference with the documented preprocessing: width-518 aspect-preserving
    resize rounded to multiples of 14, rescale 1/255, ImageNet normalisation.
    Returns depth interpolated back to the input resolution."""
    import torch.nn.functional as F

    h, w = image_rgb.shape[:2]
    scale = DA2_SIZE / max(h, w)
    nh = max(14, int(round(h * scale / 14)) * 14)
    nw = max(14, int(round(w * scale / 14)) * 14)
    img = cv2.resize(image_rgb, (nw, nh), interpolation=cv2.INTER_CUBIC)
    x = img.astype(np.float32) / 255.0
    x = (x - DA2_MEAN) / DA2_STD
    tensor = torch.from_numpy(x.transpose(2, 0, 1))[None].half().to(device)
    with torch.no_grad():
        pred = model(pixel_values=tensor).predicted_depth
    pred = F.interpolate(pred[None].float(), size=(h, w), mode="bilinear", align_corners=False)[0, 0]
    return pred.cpu().numpy()


def frame_points(rec, name: str):
    """Sparse 3D points visible in one undistorted frame -> (N,2) pixels, (N,) depths."""
    image = next(img for img in rec.images.values() if img.name == name)
    cam = rec.cameras[image.camera_id]
    pose = image.cam_from_world()
    R = np.asarray(pose.rotation.matrix())
    t = np.asarray(pose.translation).reshape(3)
    kmat = np.asarray(cam.calibration_matrix())
    pts, pix, depths = [], [], []
    for p2 in image.points2D:
        if not p2.has_point3D():
            continue
        xyz = rec.points3D[p2.point3D_id].xyz
        cam_pt = R @ np.asarray(xyz) + t
        if cam_pt[2] <= 1e-6:
            continue
        uv = kmat @ cam_pt
        u, v = uv[0] / uv[2], uv[1] / uv[2]
        if 0 <= u < cam.width and 0 <= v < cam.height:
            pix.append((u, v))
            depths.append(cam_pt[2])
    return np.asarray(pix), np.asarray(depths)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dense", required=True, help="COLMAP dense workspace (sparse/ + images/)")
    ap.add_argument("--out", required=True, help="output directory for depth npz + fused ply")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-frames", type=int, default=0, help="0 = all")
    ap.add_argument("--stride", type=int, default=4, help="backproject every Nth pixel")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    import pycolmap

    dense = Path(args.dense)
    out = Path(args.out)
    (out / "depth").mkdir(parents=True, exist_ok=True)
    started = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_model(args.model, device)
    print(f"[mono] model on {device}")

    frames = read_frames(dense / "sparse")
    if args.max_frames > 0:
        frames = frames[: args.max_frames]
    rec = pycolmap.Reconstruction(str(dense / "sparse"))

    fused_pts, ratios_all = [], []
    depth_dir = out / "depth"
    for idx, fr in enumerate(frames):
        img_path = dense / "images" / fr["name"]
        if not img_path.exists():
            continue
        rgb = cv2.cvtColor(cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        pred = infer_depth(model, rgb, device)
        pix, ref_depth = frame_points(rec, fr["name"])
        if len(ref_depth) >= 8:
            ui = np.clip(pix[:, 0].round().astype(int), 0, pred.shape[1] - 1)
            vi = np.clip(pix[:, 1].round().astype(int), 0, pred.shape[0] - 1)
            sampled = pred[vi, ui]
            ok = sampled > 1e-6
            # DA2 relative output is inverse-depth (measured: corr(pred,ref) < 0,
            # corr(1/pred,ref) > 0), so metric depth = pred * scale with
            # scale = median(ref * pred).
            scales = ref_depth[ok] * sampled[ok]
            med = float(np.median(scales))
            ratios_all.append(float(np.median(np.abs(np.log((ref_depth[ok] * sampled[ok]) / max(med, 1e-9))))))
        else:
            med = 1.0
        depth_metric = pred * med  # network depth -> SfM-scale depth
        np.savez_compressed(depth_dir / (fr["name"] + ".depth.npz"),
                            depth=depth_metric.astype(np.float32), scale=med)
        # backproject subsampled grid
        h, w = depth_metric.shape
        vv, uu = np.mgrid[0:h:args.stride, 0:w:args.stride]
        d = depth_metric[::args.stride, ::args.stride]
        ok = d > 1e-6
        cam_xy = np.stack([(uu[ok] - fr["K"][0, 2]) / fr["K"][0, 0] * d[ok],
                           (vv[ok] - fr["K"][1, 2]) / fr["K"][1, 1] * d[ok],
                           d[ok]], axis=-1)
        world = (fr["R"].T @ (cam_xy - fr["t"]).T).T
        fused_pts.append(world.astype(np.float64))
        if (idx + 1) % 20 == 0:
            print(f"[mono] {idx + 1}/{len(frames)} frames")
    pts = np.concatenate(fused_pts, axis=0) if fused_pts else np.zeros((0, 3))
    if len(pts):
        write_ply(out / "fused_mono.ply", pts, np.zeros((0, 3), dtype=np.int64))
    spread = float(np.median(ratios_all)) if ratios_all else float("nan")
    report = {"frames": len(frames), "points": int(len(pts)),
              "scale_anchor_log_spread_median": spread,
              "model": "Depth-Anything-V2-Small (Apache-2.0)",
              "seconds": round(time.time() - started, 1),
              "limits": ["Relative depth anchored to SfM sparse points per frame; "
                         "log-spread measures internal anchoring consistency, not survey accuracy."]}
    (out / ("mono_report.json" if not args.report else args.report)).write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print(f"[mono] wrote {out} in {time.time() - started:.1f}s | anchor log-spread {spread:.4f}")


if __name__ == "__main__":
    main()
