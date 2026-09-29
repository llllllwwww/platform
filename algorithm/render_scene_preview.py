"""把重建结果渲染成 2D 投影图，用于快速验证几何是否合理。

不需要 matplotlib：用 numpy 做投影 + cv2 画点到画布。
输出两个正交视角（侧视 / 俯视），并把相机轨迹与病害三维点叠加。

用法:
  python render_scene_preview.py --scene <scene.json> --mesh <surface.npz> \
      [--localized <localized.json>] --out <preview.png>
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np


def world_to_pixel(points, axis_h, axis_v, bounds, size, margin=30):
    """把三维点正交投影到 (axis_h, axis_v) 平面并映射到画布像素。"""
    h, v = points[:, axis_h], points[:, axis_v]
    h_lo, h_hi = bounds[axis_h]
    v_lo, v_hi = bounds[axis_v]
    scale = min((size[0] - 2 * margin) / max(h_hi - h_lo, 1e-9),
                (size[1] - 2 * margin) / max(v_hi - v_lo, 1e-9))
    px = margin + (h - h_lo) * scale
    # 图像 y 向下，翻转使 +v 朝上
    py = size[1] - margin - (v - v_lo) * scale
    return px, py, scale


def main():
    import cv2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--mesh', required=True)
    parser.add_argument('--localized')
    parser.add_argument('--out', required=True)
    parser.add_argument('--max-points', type=int, default=120000)
    parser.add_argument('--focus-radius', type=float, default=3.0,
                        help='取景范围 = 轨迹包围盒外扩 focus_radius × 中位顶点距')
    parser.add_argument('--width', type=int, default=1500)
    args = parser.parse_args()

    scene = json.loads(Path(args.scene).read_text('utf-8'))
    with np.load(args.mesh, allow_pickle=False) as data:
        vertices = data['vertices']
    if len(vertices) > args.max_points:
        idx = np.linspace(0, len(vertices) - 1, args.max_points).astype(int)
        vertices = vertices[idx]
    print(f'网格顶点 {len(vertices)}')

    centers = np.array([c['center'] for c in scene['cameras']])
    defects = []
    if args.localized:
        loc = json.loads(Path(args.localized).read_text('utf-8'))
        for d in loc['defects']:
            for p in d['points']:
                defects.append(p)
        defects = np.array(defects) if defects else np.zeros((0, 3))
        print(f'病害三维点 {len(defects)}')

    all_pts = np.vstack([vertices, centers] + ([defects] if len(defects) else []))
    # 隧道场景的正确取景：以**相机轨迹**为中心、按隧道半径外扩。
    # MVS 离群点会把百分位包围盒也撑歪，而轨迹是可靠先验（沿隧道前进）。
    from scipy.spatial import cKDTree
    r_med = float(np.median(cKDTree(centers).query(vertices)[0]))
    pad = args.focus_radius * r_med
    bounds = [(float(centers[:, i].min() - pad), float(centers[:, i].max() + pad)) for i in range(3)]
    span = [b[1] - b[0] for b in bounds]
    axis = int(np.argmax(span))
    other = [i for i in range(3) if i != axis]
    full = [(float(all_pts[:, i].min()), float(all_pts[:, i].max())) for i in range(3)]
    print(f'轴向 = {axis}, 取景跨度 {np.round(span,2).tolist()}  (焦点半径 {args.focus_radius}x 中位距 {r_med:.2f})')
    print(f'全量跨度 {np.round([f[1]-f[0] for f in full],2).tolist()}'
          f'  -> 离群点使包围盒膨胀 {max((f[1]-f[0]) for f in full)/max(span):.2f}x')

    views = [('side', other[0], other[1]), ('top', other[1], other[0])]
    panels = []
    size = (args.width, 620)
    for name, ah, av in views:
        canvas = np.full((size[1], size[0], 3), 255, np.uint8)
        px, py, scale = world_to_pixel(vertices, ah, av, bounds, size)
        inside = (px >= 0) & (px < size[0]) & (py >= 0) & (py < size[1])
        canvas[py[inside].astype(int), px[inside].astype(int)] = (170, 170, 170)
        cx, cy, _ = world_to_pixel(centers, ah, av, bounds, size)
        for x, y in zip(cx, cy):
            cv2.circle(canvas, (int(x), int(y)), 2, (200, 120, 0), -1)
        if len(defects):
            dx, dy, _ = world_to_pixel(defects, ah, av, bounds, size)
            for x, y in zip(dx, dy):
                cv2.circle(canvas, (int(x), int(y)), 4, (0, 0, 220), 1)
        label = f'{name}: horizontal=axis{ah}, vertical=axis{av}'
        cv2.putText(canvas, label, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 2)
        cv2.putText(canvas, f'mesh vertices={len(vertices)}  cameras={len(centers)}  defects={len(defects)}',
                    (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 120, 120), 1)
        panels.append(canvas)

    out = np.vstack([panels[0], np.full((8, size[0], 3), 220, np.uint8), panels[1]])
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), out)
    print('已写出', out_path)
    print('图例: 灰=隧道表面网格  蓝=相机中心  红圈=病害三维点')


if __name__ == '__main__':
    main()
