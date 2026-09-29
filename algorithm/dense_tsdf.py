"""GPU TSDF fusion over COLMAP patch-match depth maps, then marching cubes + Taubin smoothing.

Why this exists: advancing_front_mesher on fused.ply leaves a jagged, hole-riddled
surface (measured on tumvi corridor: median dihedral angle 17.9 deg between adjacent
faces, p99 146.7 deg, 13.9k boundary edges, 1184 small holes). Screened poisson in
this COLMAP build outputs an empty mesh regardless of depth, and the pycolmap wheel
is CPU-only, so the per-frame depth maps are fused here directly.

Method (Curless & Levoy 1996; KinectFusion-style integration, no external deps):
1. parse COLMAP depth .geometric.bin (uint32 width/height/channels header,
   float32 width-fastest samples),
2. read undistorted pinhole poses from dense/sparse via pycolmap,
3. integrate truncated SDF on a uniform voxel grid (torch, chunked to fit VRAM),
4. mask voxels with too little evidence, then marching cubes (scikit-image,
   Lewiner 2003) at level 0,
5. Taubin (lambda|mu) smoothing removes staircase aliasing without global shrink.

The mesh leaves this module untextured; re-texture against the same dense workspace
with `colmap mesh_texturer` (input mesh + dense workspace must share the undistorted
frame). No metric scale is invented here; axial spacing repair is a separate step
(axial_reparam.py).
"""
from __future__ import annotations

import argparse
import json
import struct
import time
from pathlib import Path

import numpy as np
import torch

from mesh_io import read_ply
from mesh_io import write_ply  # shared PLY writer (no torch dependency)

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------- COLMAP I/O

def read_depth_bin(path: Path) -> np.ndarray:
    """COLMAP 4.2 depth map: ASCII header '<w>&<h>&<c>&' then float32 samples (x fastest).

    Verified against a real workspace: file size 9 + 516*516*4 bytes for a 516x516
    single-channel map. (Older docs describe a 3x uint32 header; do not trust that here.)
    """
    import re

    with Path(path).open("rb") as handle:
        head = handle.read(64)
        match = re.match(rb"(\d+)&(\d+)&(\d+)&", head)
        if not match:
            raise ValueError(f"unexpected depth map header: {path}")
        width, height, channels = (int(g) for g in match.groups())
        handle.seek(match.end())
        data = np.fromfile(handle, dtype="<f4", count=width * height * channels)
    if data.size != width * height * channels:
        raise ValueError(f"truncated depth map: {path}")
    return data.reshape(height, width, channels)[:, :, 0]


def read_frames(sparse_dir: Path):
    """Registered undistorted pinhole frames: (name, R(w2c), t(w2c), K, width, height, center)."""
    import pycolmap

    rec = pycolmap.Reconstruction(str(sparse_dir))
    frames = []
    for image in rec.images.values():
        if not image.has_pose:
            continue
        pose = image.cam_from_world()
        rot = np.asarray(pose.rotation.matrix(), dtype=np.float64)
        trans = np.asarray(pose.translation, dtype=np.float64).reshape(3)
        camera = rec.cameras[image.camera_id]
        kmat = np.asarray(camera.calibration_matrix(), dtype=np.float64)
        frames.append(
            {
                "name": image.name,
                "R": rot,
                "t": trans,
                "K": kmat,
                "size": (int(camera.width), int(camera.height)),
                "center": (-rot.T @ trans).astype(np.float64),
            }
        )
    if not frames:
        raise ValueError(f"no posed images in {sparse_dir}")
    frames.sort(key=lambda f: f["name"])
    return frames


def robust_bounds(points: np.ndarray, margin: float) -> tuple[np.ndarray, np.ndarray]:
    lo = np.quantile(points, 0.005, axis=0) - margin
    hi = np.quantile(points, 0.995, axis=0) + margin
    return lo, hi


class TsdfVolume:
    def __init__(self, lo, hi, voxel, device):
        self.voxel = float(voxel)
        self.lo = np.asarray(lo, dtype=np.float64)
        self.hi = np.asarray(hi, dtype=np.float64)
        dims = np.maximum(np.ceil((self.hi - self.lo) / self.voxel).astype(np.int64) + 1, 2)
        self.dims = dims
        n = int(np.prod(dims))
        self.tsdf = torch.ones(n, dtype=torch.float16, device=device)
        self.weight = torch.zeros(n, dtype=torch.float16, device=device)
        self.device = device
        # 网格坐标生成留到积分时按 chunk 做，避免一次性 3xN 大数组
        gx = torch.arange(int(dims[0]), device=device, dtype=torch.float32)
        gy = torch.arange(int(dims[1]), device=device, dtype=torch.float32)
        gz = torch.arange(int(dims[2]), device=device, dtype=torch.float32)
        self._axis = (gx, gy, gz)
        self.lo_t = torch.from_numpy(self.lo.astype(np.float32)).to(device)

    def _chunk_centers(self, chunk: int):
        """Yield (flat_index, xyz) for voxel centers in chunks of `chunk` points."""
        gx, gy, gz = self._axis
        nz, ny, nx = int(self.dims[2]), int(self.dims[1]), int(self.dims[0])
        # flat index layout: x fastest, then y, then z (row-major over dims)
        for z0 in range(0, nz, max(1, chunk // max(1, nx * ny))):
            z1 = min(nz, z0 + max(1, chunk // max(1, nx * ny)))
            gzz = gz[z0:z1].view(-1, 1, 1).expand(-1, ny, nx).reshape(-1)
            gyy = gy.view(1, -1, 1).expand(z1 - z0, -1, nx).reshape(-1)
            gxx = gx.view(1, 1, -1).expand(z1 - z0, ny, -1).reshape(-1)
            xyz = torch.stack([gxx, gyy, gzz], dim=1).float() * self.voxel + self.lo_t
            gxi, gyi, gzi = gxx.long(), gyy.long(), gzz.long()
            idx = (gzi * ny + gyi) * nx + gxi
            yield idx, xyz

    def integrate(self, depth: np.ndarray, R, t, K, size, depth_max: float, chunk: int = 2_000_000):
        w, h = size
        depth_t = torch.from_numpy(np.ascontiguousarray(depth)).to(self.device)
        kmat = torch.from_numpy(K.astype(np.float32)).to(self.device)
        rot = torch.from_numpy(R.astype(np.float32)).to(self.device)
        tr = torch.from_numpy(t.astype(np.float32)).to(self.device)
        fx, fy, cx, cy = float(kmat[0, 0]), float(kmat[1, 1]), float(kmat[0, 2]), float(kmat[1, 2])
        trunc = 4.0 * self.voxel
        for idx, xyz in self._chunk_centers(chunk):
            cam = xyz @ rot.T + tr
            z = cam[:, 2]
            valid = z > 1e-6
            u = fx * cam[:, 0] / torch.clamp(z, min=1e-6) + cx
            v = fy * cam[:, 1] / torch.clamp(z, min=1e-6) + cy
            valid &= (u >= 0) & (u <= w - 1) & (v >= 0) & (v <= h - 1)
            if not valid.any():
                continue
            ui = u[valid].round().long().clamp(0, w - 1)
            vi = v[valid].round().long().clamp(0, h - 1)
            d0 = depth_t[vi, ui]
            zc = z[valid]
            ok = (d0 > 0) & (d0 < depth_max) & (zc > 0) & (zc < depth_max)
            if not ok.any():
                continue
            sdf = d0[ok] - zc[ok]
            inside = sdf.abs() <= trunc
            if not inside.any():
                continue
            sdf_in = (sdf[inside] / trunc).clamp(-1.0, 1.0)
            sel = idx[valid][ok][inside]
            w_new = torch.ones_like(sdf_in)
            old_t = self.tsdf[sel].float()
            old_w = self.weight[sel].float()
            new_w = old_w + w_new
            self.tsdf[sel] = ((old_t * old_w + sdf_in * w_new) / new_w).half()
            self.weight[sel] = new_w.half()


def taubin_smooth(vertices: np.ndarray, faces: np.ndarray, lam: float = 0.5, mu: float = -0.53,
                  iterations: int = 12, device: str = "cuda") -> np.ndarray:
    """Taubin |lambda|=|mu|-style two-step smoothing; volume-preserving (no shrink)."""
    v = torch.from_numpy(np.ascontiguousarray(vertices, dtype=np.float32)).to(device)
    f = torch.from_numpy(np.ascontiguousarray(faces, dtype=np.int64)).to(device)
    edges = torch.cat([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], dim=0)
    edges = torch.cat([edges, edges.flip(1)], dim=0)  # directed both ways
    src, dst = edges[:, 0], edges[:, 1]
    n = v.shape[0]
    counts = torch.zeros(n, device=device, dtype=torch.float32)
    counts.index_add_(0, dst, torch.ones_like(src, dtype=torch.float32))
    counts = counts.clamp(min=1).unsqueeze(1)
    for _ in range(iterations):
        for step in (lam, mu):
            acc = torch.zeros_like(v)
            acc.index_add_(0, dst, v[src])
            v = v + step * (acc / counts - v)
    return v.cpu().numpy().astype(np.float64)


def boundary_stats(faces: np.ndarray) -> dict:
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=0)
    edges = np.sort(edges, axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    return {"edges": int(len(unique)), "boundary_edges": int((counts == 1).sum()),
            "nonmanifold_edges": int((counts > 2).sum())}


def dihedral_stats(vertices: np.ndarray, faces: np.ndarray) -> dict:
    tri = vertices[faces]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(fn, axis=1, keepdims=True)
    ok = norm[:, 0] > 1e-12
    fn = fn[ok] / norm[ok]
    faces_ok = faces[ok]
    m = len(faces_ok)
    e = np.sort(np.concatenate([faces_ok[:, [0, 1]], faces_ok[:, [1, 2]], faces_ok[:, [2, 0]]]), axis=1)
    face_ids = np.tile(np.arange(m), 3)  # parallel to e rows
    unique, inv, counts = np.unique(e, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    interior = counts == 2
    keep = interior[inv]
    if keep.sum() == 0:
        return {"dihedral_median_deg": None}
    edge_of_face = inv[keep]
    face_of_edge = face_ids[keep]
    order = np.argsort(edge_of_face, kind="stable")
    edge_sorted = edge_of_face[order]
    face_sorted = face_of_edge[order]
    starts = np.flatnonzero(np.r_[True, edge_sorted[1:] != edge_sorted[:-1]])
    ends = np.r_[starts[1:], len(edge_sorted)]
    if not np.all(ends - starts == 2):
        return {"dihedral_median_deg": None, "note": "unexpected interior-edge multiplicity"}
    a = fn[face_sorted[starts]]
    b = fn[face_sorted[ends - 1]]
    dots = np.clip(np.sum(a * b, axis=1), -1.0, 1.0)
    ang = np.degrees(np.arccos(dots))
    return {"dihedral_median_deg": float(np.median(ang)),
            "dihedral_p90_deg": float(np.quantile(ang, 0.9)),
            "dihedral_p99_deg": float(np.quantile(ang, 0.99))}


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dense", required=True, help="COLMAP dense workspace (with sparse/, stereo/depth_maps)")
    ap.add_argument("--out", required=True, help="output mesh PLY")
    ap.add_argument("--voxel", type=float, default=0.015, help="voxel size in SfM units")
    ap.add_argument("--depth-max", type=float, default=0.0, help="max integrated depth; 0 = auto from fused cloud")
    ap.add_argument("--min-weight", type=int, default=4, help="voxels observed by fewer views are treated as empty")
    ap.add_argument("--smooth-iterations", type=int, default=12)
    ap.add_argument("--report", default="", help="optional JSON report path")
    ap.add_argument("--max-voxels", type=float, default=160e6, help="safety cap on grid size")
    args = ap.parse_args()

    dense = Path(args.dense)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()

    frames = read_frames(dense / "sparse")
    print(f"[tsdf] {len(frames)} posed frames")

    props, _ = read_ply(dense / "fused.ply")
    pts = np.column_stack([props["x"], props["y"], props["z"]]).astype(np.float64)
    margins = 6 * args.voxel
    lo, hi = robust_bounds(pts, margins)
    # 深度上限：fused 点到最近相机中心距离的高分位（单目 SfM 尺度，不引入真实单位）
    centers = np.array([f["center"] for f in frames])
    d2 = np.linalg.norm(pts[:, None, :] - centers[None, :, :], axis=2) if len(centers) <= 64 else None
    if d2 is None:  # 内存友好路径：KD-tree 最近中心距离
        from scipy.spatial import cKDTree

        d_near = cKDTree(centers).query(pts, k=1)[0]
    else:
        d_near = d2.min(axis=1)
    depth_max = float(args.depth_max) if args.depth_max > 0 else float(np.quantile(d_near, 0.99) * 1.25)
    print(f"[tsdf] bounds {np.round(lo, 2)} .. {np.round(hi, 2)}  depth_max={depth_max:.3f}")

    vol = TsdfVolume(lo, hi, args.voxel, "cuda")
    total = int(np.prod(vol.dims))
    print(f"[tsdf] grid dims {vol.dims.tolist()} = {total/1e6:.1f}M voxels (cap {args.max_voxels/1e6:.0f}M)")
    if total > args.max_voxels:
        raise SystemExit(f"voxel grid too large ({total}); raise --voxel or --max-voxels")

    depth_dir = dense / "stereo/depth_maps"
    used = 0
    for i, fr in enumerate(frames):
        bin_path = depth_dir / f"{fr['name']}.geometric.bin"
        if not bin_path.exists():
            continue
        depth = read_depth_bin(bin_path)
        if depth.shape != (fr["size"][1], fr["size"][0]):
            continue  # undistorted size mismatch guard
        vol.integrate(depth, fr["R"], fr["t"], fr["K"], fr["size"], depth_max)
        used += 1
        if (i + 1) % 40 == 0:
            print(f"[tsdf] integrated {i + 1}/{len(frames)}")
    print(f"[tsdf] integrated {used} depth maps in {time.time() - started:.1f}s")

    weight = vol.weight
    tsdf = vol.tsdf.float().cpu().numpy()
    wgt = weight.float().cpu().numpy()
    covered = float((wgt > 0).mean())
    print(f"[tsdf] voxels with evidence: {covered*100:.1f}%  (gating at >= {args.min_weight} views)")

    from skimage import measure

    # volume axes are (z, y, x) with x fastest — matching our flat layout
    volume3d = np.where(wgt >= args.min_weight, tsdf, 1.0).reshape(
        int(vol.dims[2]), int(vol.dims[1]), int(vol.dims[0]))

    spacing = (args.voxel, args.voxel, args.voxel)
    v0, f0, _normals, _values = measure.marching_cubes(volume3d, level=0.0, spacing=spacing)
    v0 = v0[:, [2, 1, 0]].astype(np.float64)  # (z,y,x) -> (x,y,z)
    v0 = v0 + vol.lo.astype(np.float64)       # voxel-center grid origin
    print(f"[mc] mesh {len(v0)} verts / {len(f0)} faces  ({time.time() - started:.1f}s)")

    v1 = taubin_smooth(v0, f0, iterations=args.smooth_iterations)
    print(f"[taubin] smoothed x{args.smooth_iterations}  ({time.time() - started:.1f}s)")

    write_ply(out, v1, f0.astype(np.int64))

    report = {
        "frames_integrated": used,
        "voxel": args.voxel,
        "dims": vol.dims.tolist(),
        "depth_max": depth_max,
        "min_weight": args.min_weight,
        "covered_voxel_fraction": covered,
        "vertices": int(len(v1)),
        "faces": int(len(f0)),
        "topology_raw_mc": boundary_stats(f0),
        "dihedral": dihedral_stats(v1, f0),
        "seconds": round(time.time() - started, 1),
    }
    report_path = Path(args.report) if args.report else out.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"[tsdf] wrote {out} and {report_path}")


if __name__ == "__main__":
    main()
