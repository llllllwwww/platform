"""验证 GPU 射线求交与 CPU 参考实现的一致性，并测量加速比。

判据（float32 vs float64 的合理判据，不苛求逐位相同）：
  ① 命中/未命中判定必须一致；
  ② 命中射线的 t 值相对误差在 float32 精度内（1e-4）；
  ③ 交点三维坐标差异在容差内；
  ④ 三角面 ID 可以不同（相邻面共享边的退化情形），但交点必须几乎重合。
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from localize_defects import intersect_rays  # noqa: E402


def main():
    mesh_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        from intersect_gpu import intersect_rays_gpu, is_available
    except Exception as error:
        print('无法导入 GPU 实现:', error)
        return 1
    if not is_available():
        print('CUDA 不可用，跳过 GPU 验证')
        return 1

    if mesh_path:
        with np.load(mesh_path, allow_pickle=False) as data:
            V, F = data['vertices'], data['faces']
    else:
        print('用法: python tests/verify_intersect_gpu.py <mesh.npz>')
        return 2
    print(f'网格: {len(V)} 顶点 / {len(F)} 面')

    rng = np.random.default_rng(7)
    origin = V.mean(axis=0)
    dirs = rng.normal(size=(1500, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)

    t0 = time.perf_counter()
    cpu_len, cpu_id = intersect_rays(origin, dirs, V, F)
    cpu_time = time.perf_counter() - t0
    print(f'CPU 逐条+分块 : {cpu_time:.2f}s  ({cpu_time/len(dirs)*1000:.1f} ms/射线)')

    all_passed = True
    for chunk in (32, 64, 128):
        t0 = time.perf_counter()
        gpu_len, gpu_id = intersect_rays_gpu(origin, dirs, V, F, chunk=chunk)
        gpu_time = time.perf_counter() - t0
        hit_cpu = cpu_id >= 0
        hit_gpu = gpu_id >= 0
        agree = float((hit_cpu == hit_gpu).mean())
        both = hit_cpu & hit_gpu
        rel = np.abs(gpu_len[both] - cpu_len[both]) / np.maximum(np.abs(cpu_len[both]), 1e-12)
        id_same = float((cpu_id[both] == gpu_id[both]).mean()) if both.any() else float('nan')
        pt_err = np.nan
        if both.any():
            p_cpu = origin + cpu_len[both, None] * dirs[both]
            p_gpu = origin + gpu_len[both, None] * dirs[both]
            pt_err = float(np.linalg.norm(p_cpu - p_gpu, axis=1).max())
        print(f'GPU chunk={chunk:<4}: {gpu_time:.2f}s  ({gpu_time/len(dirs)*1000:.2f} ms/射线)  '
              f'加速 {cpu_time/gpu_time:.1f}x')
        print(f'    命中判定一致率 {agree*100:.3f}%  命中数 cpu={int(hit_cpu.sum())} gpu={int(hit_gpu.sum())}')
        max_rel = float(rel.max()) if both.any() else 0.0
        print(f'    t 相对误差 最大 {max_rel:.2e}   面 ID 相同率 {id_same*100:.2f}%   '
              f'交点最大偏差 {pt_err:.2e}')
        passed = bool(np.array_equal(hit_cpu,hit_gpu) and (not both.any() or (max_rel < 1e-4 and pt_err < 1e-3)))
        all_passed &= passed
        verdict = ('PASS: intersections agree within float32 tolerance' if passed else 'FAIL: tolerance exceeded')
        print(f'    -> {verdict}')
        if chunk == 32:
            # 保存一次结果供后续比对
            np.savez(ROOT / 'tests' / 'gpu_vs_cpu_sample.npz',
                     cpu_len=cpu_len, cpu_id=cpu_id, gpu_len=gpu_len, gpu_id=gpu_id)
    return 0 if all_passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
