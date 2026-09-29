# -*- coding: utf-8 -*-
"""生成可发布的精简版 multiscene 前端数据。

完整离线包把 raw 网格、两份投影纹理（base64）与全部采样点都内嵌进
scene_data.js（单场景 55–83 MB），适合本机归档但不适合 git/Pages。
本脚本在**不改变数据接口**的前提下压缩体积，查看器零改动：

1. raw 对比网格按面抽稀（positions/uv/normals/support 都是按面展开的
   base64 float32，直接切片即可），面数 metadata 同步更新；
2. 两份投影纹理重采样到 <=1024px JPEG 重新内嵌（纹理本就不是刚需，
   本机完整版保留原 PNG）；
3. defects 的列表字段（采样点/像素命中/证据视图）截断到显示所需上限；
4. video_url 置空（源视频不入库，viewer 已有 null 容错）。

规则化管 mesh（约 2 MB）与 quality/frames 元数据原样保留。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

import cv2
import numpy as np


def decode_packed(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype="<f4")


def encode_packed(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode("ascii")


def slim_mesh(mesh: dict, keep_every: int) -> dict:
    faces = int(mesh["triangles"])
    kept = (faces + keep_every - 1) // keep_every
    stride = keep_every * 3  # 每面 3 顶点的展平长度
    out = dict(mesh)
    for key, comps in (("positions", 3), ("uv", 2), ("normals", 3)):
        if key not in mesh:
            continue
        arr = decode_packed(mesh[key]).reshape(-1, comps)
        arr = arr[::stride]
        out[key] = encode_packed(arr)
    if "support" in mesh:
        sup = np.frombuffer(base64.b64decode(mesh["support"]), dtype="<f4")
        out["support"] = base64.b64encode(sup[::stride].astype("<f4").tobytes()).decode("ascii")
    out["triangles"] = kept
    out["vertices"] = kept * 3
    return out


def slim_texture(data_url: str, max_side: int) -> str:
    head, b64 = data_url.split(",", 1)
    buf = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    if not ok:
        raise ValueError("texture re-encode failed")
    return "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")


def slim_defect(d: dict, max_points: int) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, list) and len(v) > max_points:
            out[k] = v[:max_points]
        elif isinstance(v, dict):
            out[k] = slim_defect(v, max_points)
        else:
            out[k] = v
    return out


def slim_document(text: str, args) -> tuple[str, dict]:
    dec = json.JSONDecoder()
    pos, out_parts, stats = 0, [], {}
    while pos < len(text):
        while pos < len(text) and text[pos] in ";\n\r\t ":
            pos += 1
        if pos >= len(text):
            break
        m = re.match(r"window\.([A-Z_]+)\s*=\s*", text[pos:])
        if not m:
            raise ValueError(f"unexpected statement at {pos}: {text[pos:pos+60]!r}")
        name = m.group(1)
        obj, end = dec.raw_decode(text, pos + m.end())
        if name == "TUNNEL_DATA":
            before = len(json.dumps(obj))
            if "mesh" in obj:
                obj["mesh"] = slim_mesh(obj["mesh"], args.keep_faces)
            if obj.get("texture_url"):
                obj["texture_url"] = slim_texture(obj["texture_url"], args.texture_max)
            reg = obj.get("regularized")
            if reg:
                if reg.get("texture_url"):
                    reg["texture_url"] = slim_texture(reg["texture_url"], args.texture_max)
            obj["video_url"] = None
            if "defects" in obj:
                obj["defects"] = [slim_defect(d, args.max_points) for d in obj["defects"]]
            stats[name] = len(json.dumps(obj)) / before
            before_mb = before / 1e6
            stats[f"{name}_mb"] = (before_mb, len(json.dumps(obj)) / 1e6)
        out_parts.append(f"window.{name}=" + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";")
        pos = end
    return "\n".join(out_parts) + "\n", stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--web-dir", required=True, type=Path)
    ap.add_argument("--keep-faces", type=int, default=16)
    ap.add_argument("--texture-max", type=int, default=1024)
    ap.add_argument("--max-points", type=int, default=32)
    args = ap.parse_args()

    for scene_js in sorted((args.web_dir / "multiscene").glob("*/scene_data.js")):
        text = scene_js.read_text(encoding="utf-8")
        slim, stats = slim_document(text, args)
        scene_js.write_text(slim, encoding="utf-8")
        size = scene_js.stat().st_size / 1e6
        detail = {k: f"{v[0]:.0f}->{v[1]:.0f}MB" for k, v in stats.items() if isinstance(v, tuple)}
        print(f"{scene_js.parent.name}: {size:.1f} MB on disk ({detail})")


if __name__ == "__main__":
    main()
