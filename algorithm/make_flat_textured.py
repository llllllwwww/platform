# -*- coding: utf-8 -*-
"""Primitive 档的“无纹理纹理化”：生成 export_web 能吃的最小占位包。

export_web 的 regularized 分支固定读取 regularization/textured/{mesh.ply,texture.png}
（COLMAP 二进制格式：每面 3 顶点索引 + 6 个 UV）。理想几何没有投影纹理可贴，
这里写出 **UV 全零** 的同格式网格——查看器 shader 对 uv≈0 自动走纯色法线着色，
呈现 Unity primitive 式的简约观感；texture.png 为浅灰占位，满足打包依赖。

用法：python make_flat_textured.py --run <run> [--rgb 200 210 218]
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

from mesh_io import read_ply


def write_textured_ply(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    """COLMAP 4.2 textured-mesh layout: float32 XYZ + per-face (nv,3 indices, nu, 6 UVs)."""
    vertices = np.ascontiguousarray(vertices, dtype="<f4")
    faces = np.ascontiguousarray(faces, dtype="<i4")
    face_dtype = np.dtype([("nv", "u1"), ("face", "<i4", (3,)), ("nu", "u1"), ("uv", "<f4", (6,))])
    record = np.empty(len(faces), dtype=face_dtype)
    record["nv"] = 3
    record["face"] = faces
    record["nu"] = 6
    record["uv"] = 0.0
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(vertices)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        f"element face {len(faces)}\n"
        "property list uchar int vertex_indices\n"
        "property list uchar float texcoord\n"
        "end_header\n"
    )
    with open(path, "wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(vertices.tobytes())
        handle.write(record.tobytes())


def write_placeholder_png(path: Path, rgb=(200, 210, 218)) -> None:
    """Minimal 64x64 RGB PNG, no external dependency."""
    w = h = 64
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    import zlib

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--rgb", nargs=3, type=int, default=[200, 210, 218])
    args = ap.parse_args()

    run = args.run
    src = run / "regularization/surface_regularized.ply"
    out_dir = run / "regularization/textured"
    out_dir.mkdir(parents=True, exist_ok=True)
    props, faces = read_ply(src)
    vertices = np.column_stack([props["x"], props["y"], props["z"]])
    write_textured_ply(out_dir / "mesh.ply", vertices, faces)
    write_placeholder_png(out_dir / "texture.png", tuple(args.rgb))
    print(f"[flat] wrote {out_dir/'mesh.ply'} ({len(vertices)} verts, UV=0) + placeholder texture.png")


if __name__ == "__main__":
    main()
