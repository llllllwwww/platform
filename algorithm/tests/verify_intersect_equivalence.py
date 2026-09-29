"""验证 intersect_rays 的分块向量化实现与原始逐条实现**结果完全一致**。

优化几何内核必须做等价性验证：只比"看起来对"不算数。
本脚本自带一份原始逐条实现作为参考，用随机射线（含命中/未命中/擦边）对比。
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localize_defects import intersect_rays, validate_mesh  # noqa: E402


def reference_per_ray(origin, directions, vertices, faces):
    """原始逐条实现（与优化前 localize_defects.intersect_rays 逐行等价）。"""
    vertices, faces = validate_mesh(vertices, faces)
    origin = np.asarray(origin, dtype=float)
    directions = np.asarray(directions, dtype=float)
    tri = vertices[faces]
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    tvec = origin - tri[:, 0]
    qvec = np.cross(tvec, e1)
    lengths = np.full(len(directions), np.nan)
    triangle_ids = np.full(len(directions), -1, dtype=np.int64)
    det_tolerance = 1e-12 * np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1)
    positive_tolerance = np.finfo(float).eps * max(float(np.linalg.norm(np.ptp(vertices, axis=0))), 1e-12) * 64
    for i, direction in enumerate(directions):
        pvec = np.cross(np.broadcast_to(direction, e2.shape), e2)
        det = np.einsum('ij,ij->i', e1, pvec)
        valid = np.abs(det) > det_tolerance
        inv = np.zeros(len(det))
        inv[valid] = 1 / det[valid]
        u = np.einsum('ij,ij->i', tvec, pvec) * inv
        v = qvec @ direction * inv
        t = np.einsum('ij,ij->i', e2, qvec) * inv
        valid &= (u >= -1e-10) & (v >= -1e-10) & (u + v <= 1 + 1e-10) & (t > positive_tolerance)
        candidates = np.flatnonzero(valid)
        if len(candidates):
            best = candidates[np.argmin(t[candidates])]
            lengths[i], triangle_ids[i] = t[best], best
    return lengths, triangle_ids


def main():
    mesh_path = sys.argv[1] if len(sys.argv) > 1 else None
    rng = np.random.default_rng(0)
    if mesh_path:
        with np.load(mesh_path, allow_pickle=False) as data:
            vertices, faces = data['vertices'], data['faces']
        print(f'用真实网格: {len(vertices)} 顶点 / {len(faces)} 面')
    else:
        # 造一个球面网格（有确定的命中/未命中行为）
        n = 26
        u = np.linspace(0, np.pi, n)
        v = np.linspace(0, 2 * np.pi, 2 * n)
        verts = []
        for a in u:
            for b in v:
                verts.append([np.sin(a) * np.cos(b), np.cos(a), np.sin(a) * np.sin(b)])
        vertices = np.array(verts)
        faces = []
        for i in range(n - 1):
            for j in range(2 * n - 1):
                a = i * 2 * n + j
                faces.append([a, a + 1, a + 2 * n])
                faces.append([a + 1, a + 2 * n + 1, a + 2 * n])
        faces = np.array(faces, dtype=np.int64)
        # 丢掉退化面（极点处的零面积三角），validate_mesh 会拒绝
        tri = vertices[faces]
        area2 = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        keep = area2 > 1e-9 * np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1) * np.linalg.norm(tri[:, 2] - tri[:, 0], axis=1)
        faces = faces[keep]
        print(f'用合成球面网格: {len(vertices)} 顶点 / {len(faces)} 面（已剔除 {int((~keep).sum())} 个退化面）')

    # 从球心向外 + 随机方向（一部分必然命中，一部分必然错过）
    origin = np.array([0.0, 0.0, 0.0])
    dirs = rng.normal(size=(400, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    # 再用一批从外部射向内部的射线（考验最近命中与双面性）
    center = vertices.mean(axis=0)
    radius = np.linalg.norm(vertices-center,axis=1).max()
    origin2 = center + np.array([3*radius,0.0,0.0])
    targets = center + rng.normal(size=(400,3))*radius
    dirs2 = targets-origin2
    dirs2 /= np.linalg.norm(dirs2,axis=1,keepdims=True)

    ok = True
    for label, o, d in (('由内向外', origin, dirs), ('由外向内', origin2, dirs2)):
        ref_len, ref_id = reference_per_ray(o, d, vertices, faces)
        for chunk in (1, 3, 8, 64, 512):
            new_len, new_id = intersect_rays(o, d, vertices, faces, chunk=chunk)
            same_id = np.array_equal(ref_id, new_id)
            same_len = np.allclose(ref_len, new_len, rtol=0, atol=1e-12, equal_nan=True)
            status = '一致' if (same_id and same_len) else '**不一致**'
            if not (same_id and same_len):
                ok = False
                bad = np.flatnonzero(ref_id != new_id)[:5]
                print(f'   差异位置 {bad.tolist()}: ref={ref_id[bad].tolist()} new={new_id[bad].tolist()}')
            print(f'{label} chunk={chunk:<4} 命中 {int((ref_id >= 0).sum()):>3}/{len(d)}  -> {status}')

    print('\nResult:', 'PASS: chunked and reference intersections agree' if ok else 'FAIL: intersection mismatch')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
