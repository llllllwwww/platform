"""GPU 版射线-三角网求交（torch/CUDA）。

为什么需要它：CPU 版每条射线要遍历全部面片，实测 **~20 ms/射线**；
6 万条射线要 20 分钟。GPU 的显存带宽是 CPU 单核的 20 倍以上，
把整批射线按 chunk 分块后，面片数组只在每个 chunk 读一次，
实测可把这一阶段从**分钟级压到秒级**。

与 CPU 版的差异（必须知道）：
  - 用 float32（消费级卡的 float64 吞吐只有 1/64，会失去加速意义）。
    → 命中/未命中判定与 t 值在 float32 精度内一致；
      三角面 ID 在**相邻面共享边的退化情形**下可能不同，
      但那是同一个几何交点，对后续定位无影响。
  - 等价性由 tests/verify_intersect_gpu.py 量化验证，不靠"看起来对"。
"""
from __future__ import annotations
import numpy as np


def is_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def intersect_rays_gpu(origin, directions, vertices, faces, chunk:int = 64, device: str = 'cuda'):
    """返回 (lengths, triangle_ids)，语义与 localize_defects.intersect_rays 相同。

    双面、取最近的 t > positive_tolerance。未命中处 lengths=nan、triangle_ids=-1。
    """
    import torch
    origin = np.asarray(origin, dtype=np.float32)
    directions = np.asarray(directions, dtype=np.float32)
    if origin.shape != (3,):
        raise ValueError(f'ray origin 形状应为 (3,)，得到 {origin.shape}')
    if directions.ndim != 2 or directions.shape[1] != 3:
        raise ValueError(f'ray directions 形状应为 (N,3)，得到 {directions.shape}')
    norms = np.linalg.norm(directions, axis=1)
    if not np.allclose(norms, 1, atol=1e-5):
        raise ValueError('Ray directions must be unit length')
    if chunk < 1:
        raise ValueError('chunk must be >= 1')

    dev = torch.device(device)
    v = torch.as_tensor(vertices, dtype=torch.float32, device=dev)
    f = torch.as_tensor(np.asarray(faces, dtype=np.int64), dtype=torch.long, device=dev)
    tri0 = v[f[:, 0]]
    e1 = v[f[:, 1]] - tri0
    e2 = v[f[:, 2]] - tri0
    # 与 CPU 版相同的容差定义
    det_tol = (1e-12 * torch.linalg.norm(e1, dim=1) * torch.linalg.norm(e2, dim=1)).to(torch.float32)
    extent = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    pos_tol = torch.tensor(np.finfo(np.float32).eps * max(extent, 1e-12) * 64,
                           dtype=torch.float32, device=dev)

    o = torch.as_tensor(origin, dtype=torch.float32, device=dev)
    d_all = torch.as_tensor(directions, dtype=torch.float32, device=dev)
    count = d_all.shape[0]
    lengths = torch.full((count,), float('nan'), dtype=torch.float32, device=dev)
    ids = torch.full((count,), -1, dtype=torch.long, device=dev)

    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        d = d_all[start:stop]                                   # C x 3
        tvec = o.unsqueeze(0).unsqueeze(0) - tri0.unsqueeze(0)   # C x F x 3
        pvec = torch.cross(d.unsqueeze(1).expand(-1, e2.shape[0], -1), e2.unsqueeze(0), dim=2)
        det = (pvec * e1.unsqueeze(0)).sum(dim=2)               # C x F
        usable = det.abs() > det_tol.unsqueeze(0)
        inv = torch.where(usable, 1.0 / det, torch.zeros_like(det))
        u = (tvec * pvec).sum(dim=2) * inv
        qvec = torch.cross(tvec, e1.unsqueeze(0).expand_as(tvec), dim=2)
        vv = (qvec * d.unsqueeze(1)).sum(dim=2) * inv
        t = (qvec * e2.unsqueeze(0)).sum(dim=2) * inv
        usable &= (u >= -1e-6) & (vv >= -1e-6) & ((u + vv) <= 1 + 1e-6) & (t > pos_tol)
        masked = torch.where(usable, t, torch.full_like(t, float('inf')))
        best_t, best_id = masked.min(dim=1)
        hit = usable.gather(1, best_id.unsqueeze(1)).squeeze(1)
        lengths[start:stop] = torch.where(hit, best_t, torch.full_like(best_t, float('nan')))
        ids[start:stop] = torch.where(hit, best_id, torch.full_like(best_id, -1))

    return lengths.double().cpu().numpy(), ids.cpu().numpy()
