"""Axial constant-velocity reparameterization: repair monocular axial scale drift.

Diagnosis on the tumvi corridor run: COLMAP places camera stations whose per-step
axial spacing grows 2.68x from the first half to the second half (median gap
0.033 -> 0.089 SfM units). Real push-through (cart / handheld walk) is roughly
constant-velocity, so the compressed half makes the corridor look far shorter than
reality while the stretched half looks inflated. All four multiscene runs show the
same artifact (drift indicators +0.0015 .. +1.69 in mesh_postprocess reports).

Repair, applied only when explicitly requested:
1. project camera centers on the principal axis -> per-frame station s_i,
2. build the constant-velocity target u_i = linspace(s_first, s_first + L, n)
   where L is the current axial span (or --real-length when supplied),
3. remap every mesh vertex's axial coordinate through the monotone map s -> u
   (piecewise-linear over stations, endpoint slopes extended outside),
4. report per-half spacing before/after and max vertex displacement.

This deliberately trades exact pixel-ray consistency along the axis for a
stated assumption (constant push speed). It is a display/measurement-grade
warp, not a new reconstruction; the report records everything applied.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from mesh_io import read_ply, write_ply
from mesh_postprocess import principal_axis

ROOT = Path(__file__).resolve().parent


def station_targets(s_sorted: np.ndarray, real_length: float = 0.0) -> np.ndarray:
    n = len(s_sorted)
    span = float(s_sorted[-1] - s_sorted[0])
    total = span if real_length <= 0 else float(real_length)
    return s_sorted[0] + np.linspace(0.0, total, n)


def remap_axis(vertices: np.ndarray, origin: np.ndarray, axis: np.ndarray,
               s_sorted: np.ndarray, u_sorted: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    z = (vertices - origin) @ axis
    z_mapped = np.interp(z, s_sorted, u_sorted)
    # endpoint-linear extension so points beyond the trajectory keep moving
    lo_slope = (u_sorted[1] - u_sorted[0]) / max(s_sorted[1] - s_sorted[0], 1e-12)
    hi_slope = (u_sorted[-1] - u_sorted[-2]) / max(s_sorted[-1] - s_sorted[-2], 1e-12)
    below = z < s_sorted[0]
    above = z > s_sorted[-1]
    z_mapped = np.where(below, u_sorted[0] + (z - s_sorted[0]) * lo_slope, z_mapped)
    z_mapped = np.where(above, u_sorted[-1] + (z - s_sorted[-1]) * hi_slope, z_mapped)
    moved = (z_mapped - z)[:, None] * axis[None, :]
    return vertices + moved, np.abs(z_mapped - z)


def half_gap_report(s: np.ndarray, u: np.ndarray) -> dict:
    gaps = np.diff(s)
    half = len(gaps) // 2
    ug = np.diff(u)
    uh = len(ug) // 2
    return {
        "gap_first_half_median": float(np.median(gaps[:half])),
        "gap_second_half_median": float(np.median(gaps[half:])),
        "gap_ratio_before": float(np.median(gaps[half:]) / max(np.median(gaps[:half]), 1e-12)),
        "uniform_gap_ratio_after": float(np.median(ug[uh:]) / max(np.median(ug[:uh]), 1e-12)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scene", required=True, help="scene.json with cameras[].center")
    ap.add_argument("--mesh", required=True, help="input mesh PLY")
    ap.add_argument("--out", required=True, help="output mesh PLY")
    ap.add_argument("--real-length", type=float, default=0.0,
                    help="force axial span to this length (SfM units otherwise preserved); 0 = keep")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    scene = json.load(open(args.scene, encoding="utf-8"))
    centers, axis, bending = principal_axis(scene["cameras"])
    s = (centers - centers.mean(0)) @ axis
    order = np.argsort(s)
    s_sorted = s[order]

    props, faces = read_ply(args.mesh)
    vertices = np.column_stack([props["x"], props["y"], props["z"]]).astype(np.float64)
    origin = centers.mean(0)

    u_sorted = station_targets(s_sorted, args.real_length)
    out_vertices, disp = remap_axis(vertices, origin, axis, s_sorted, u_sorted)

    write_ply(Path(args.out), out_vertices, faces)

    report = {
        "frames": int(len(s_sorted)),
        "axis_bending_ratio": bending,
        "axial_span_before": float(s_sorted[-1] - s_sorted[0]),
        "axial_span_after": float(u_sorted[-1] - u_sorted[0]),
        "real_length_requested": args.real_length,
        "max_vertex_displacement": float(disp.max()) if len(disp) else 0.0,
        "median_vertex_displacement": float(np.median(disp)) if len(disp) else 0.0,
        **half_gap_report(s_sorted, u_sorted),
    }
    report_path = Path(args.report) if args.report else Path(args.out).with_suffix(".reparam.json")
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    print(f"[axial_reparam] wrote {args.out} and {report_path}")


if __name__ == "__main__":
    main()
