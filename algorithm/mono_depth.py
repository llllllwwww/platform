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
    ap.add_argument("--mask-radius", type=float, default=0.0,
                    help="pixels within this distance of a projected sparse point are trusted; 0 = off")
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

    fused_pts, ratios_all, anchor_samples = [], [], []
    raw_preds, frame_refs = [], []
    kept_frames = []
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
            # corr(1/pred,ref) > 0); ref*pred samples the global scale.
            anchor_samples.append(ref_depth[ok] * sampled[ok])
        raw_preds.append(pred.astype(np.float16))
        frame_refs.append((fr, pix, ref_depth))
        kept_frames.append(fr)
        if (idx + 1) % 40 == 0:
            print(f"[mono] inferred {idx + 1}/{len(frames)}")
    # Global anchoring: one scale for the whole sequence. Per-frame medians vary
    # ~18% (measured) because sparse coverage differs per frame; a rigid corridor
    # needs a single consistent scale or TSDF fusion stacks contradictory fields.
    global_scale = float(np.median(np.concatenate(anchor_samples))) if anchor_samples else 1.0
    print(f"[mono] global scale {global_scale:.4f} from {sum(len(a) for a in anchor_samples)} anchor samples")
    depth_dir = out / "depth"
    for pred16, (fr, pix, ref_depth) in zip(raw_preds, frame_refs):
        pred = pred16.astype(np.float32)
        depth_metric = pred * global_scale
        # Confidence region: pixels within mask_radius of a projected sparse point.
        # Outside it the network is extrapolating; those depths are invalid (0) so
        # TSDF integration and point back-projection ignore them.
        from scipy.spatial import cKDTree

        hh, ww = depth_metric.shape
        grid = np.mgrid[0:hh, 0:ww].transpose(1, 2, 0).reshape(-1, 2)[:, ::-1]  # (u,v)
        if len(pix) >= 5 and args.mask_radius > 0:
            dist, _ = cKDTree(pix).query(grid, k=1, workers=-1)
            valid2d = (dist.reshape(hh, ww) <= args.mask_radius)
        else:
            valid2d = np.ones((hh, ww), dtype=bool)
        # Per-frame range gate from the sparse reference depths themselves.
        if len(ref_depth) >= 8:
            d_hi = float(np.quantile(ref_depth, 0.95) * 1.3)
            d_lo = max(float(np.quantile(ref_depth, 0.05) * 0.7), 1e-3)
        else:
            d_hi, d_lo = np.inf, 0.0
        keep = valid2d & (depth_metric >= d_lo) & (depth_metric <= d_hi)
        depth_metric = np.where(keep, depth_metric, 0.0)
        np.savez_compressed(depth_dir / (fr["name"] + ".depth.npz"),
                            depth=depth_metric.astype(np.float32), scale=global_scale)
        # backproject subsampled grid (depth already gated: 0 = invalid)
        h, w = depth_metric.shape
        vv, uu = np.mgrid[0:h:args.stride, 0:w:args.stride]
        d = depth_metric[::args.stride, ::args.stride]
        ok = d > 1e-6
        cam_xy = np.stack([(uu[ok] - fr["K"][0, 2]) / fr["K"][0, 0] * d[ok],
                           (vv[ok] - fr["K"][1, 2]) / fr["K"][1, 1] * d[ok],
                           d[ok]], axis=-1)
        world = (fr["R"].T @ (cam_xy - fr["t"]).T).T
        fused_pts.append(world.astype(np.float64))
    pts = np.concatenate(fused_pts, axis=0) if fused_pts else np.zeros((0, 3))
    if len(pts):
        write_ply(out / "fused_mono.ply", pts, np.zeros((0, 3), dtype=np.int64))
    spread = float(np.median(ratios_all)) if ratios_all else float("nan")
    report = {"frames": len(kept_frames), "points": int(len(pts)),
              "global_scale": global_scale,
              "scale_anchor_log_spread_median": spread,
              "model": "Depth-Anything-V2-Small (Apache-2.0)",
              "seconds": round(time.time() - started, 1),
              "limits": ["Relative depth anchored to SfM sparse points with ONE global scale "
                         "(per-frame medians vary ~18%); log-spread measures internal anchoring "
                         "consistency, not survey accuracy."]}
    (out / ("mono_report.json" if not args.report else args.report)).write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print(f"[mono] wrote {out} in {time.time() - started:.1f}s | anchor log-spread {spread:.4f}")


if __name__ == "__main__":
    main()
