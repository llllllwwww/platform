# -*- coding: utf-8 -*-
"""axial_reparam 回归：均匀化、全长保持、端点外线性延伸、前后半步距比。"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from axial_reparam import half_gap_report, remap_axis, station_targets  # noqa: E402


def test_uniform_spacing_preserves_span():
    # 前半压缩、后半拉伸的站位（2.68x 漂移的缩小版）
    n = 20
    gaps = np.concatenate([np.full(n // 2, 0.5), np.full(n - n // 2 - 1, 1.34)])
    s = np.concatenate([[0.0], np.cumsum(gaps)])
    u = station_targets(s, real_length=0.0)
    assert abs((u[-1] - u[0]) - (s[-1] - s[0])) < 1e-9, "span must be preserved"
    assert np.allclose(np.diff(u), np.diff(u)[0]), "target must be uniform"


def test_remap_expands_compressed_half():
    n = 20
    gaps = np.concatenate([np.full(n // 2, 0.5), np.full(n - n // 2 - 1, 1.34)])
    s = np.concatenate([[0.0], np.cumsum(gaps)])
    u = station_targets(s, 0.0)
    axis = np.array([0.0, 0.0, 1.0])
    origin = np.zeros(3)
    # 顶点放在相机站位上，z = s_i
    verts = np.column_stack([np.zeros(n), np.zeros(n), s])
    out, disp = remap_axis(verts, origin, axis, s, u)
    z_after = out[:, 2]
    assert np.allclose(z_after, u), "stations should map onto uniform targets"
    assert disp[1:n // 2].min() > 0.0, "compressed half (except the origin station) must be expanded"
    assert np.all(np.diff(z_after) > 0), "remap must stay monotone"


def test_endpoint_extension_is_linear():
    s = np.array([1.0, 2.0, 4.0])
    u = np.array([0.0, 1.0, 3.0])
    axis = np.array([1.0, 0.0, 0.0])
    verts = np.array([[-1.0, 0, 0], [5.0, 0, 0]])  # 轨迹两端之外
    out, _ = remap_axis(verts, np.zeros(3), axis, s, u)
    # 左端：u[0] + (z - s[0]) * (u[1]-u[0])/(s[1]-s[0]) = 0 + (-2)*(1) = -2
    # 右端：u[-1] + (z - s[-1]) * (u[-1]-u[-2])/(s[-1]-s[-2]) = 3 + 1*(2/2) = 4
    assert abs(out[0, 0] - (-2.0)) < 1e-9
    assert abs(out[1, 0] - 4.0) < 1e-9


def test_half_gap_report_signs():
    n = 20
    gaps = np.concatenate([np.full(n // 2, 0.5), np.full(n - n // 2 - 1, 1.34)])
    s = np.concatenate([[0.0], np.cumsum(gaps)])
    u = station_targets(s, 0.0)
    rep = half_gap_report(s, u)
    assert rep["gap_ratio_before"] > 2.0
    assert abs(rep["uniform_gap_ratio_after"] - 1.0) < 1e-9


if __name__ == "__main__":
    test_uniform_spacing_preserves_span()
    test_remap_expands_compressed_half()
    test_endpoint_extension_is_linear()
    test_half_gap_report_signs()
    print("4 tests passed")
