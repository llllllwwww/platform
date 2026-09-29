#!/usr/bin/env bash
# ============================================================================
# 隧道三维重建 + 病害三维定位 —— 全流程编排（已在本机实测跑通）
#
#   视频 → 抽帧 → COLMAP(GPU) 稀疏/稠密/网格 → scene.json
#        → 病害分割(GPU) → 像素反变换为原始图像坐标 → 射线-网格求交 → 三维坐标
#
# 用法:
#   bash run_tunnel_pipeline.sh <视频文件> [工作目录]
#
# 依赖的环境变量:
#   PY        Python 解释器（默认 F:/data/WB/venvs/tunnel/Scripts/python.exe）
#   COLMAP_BIN COLMAP 可执行文件
# ============================================================================
set -eu

VIDEO="${1:?用法: bash run_tunnel_pipeline.sh <视频文件> [工作目录]}"
WORK="${2:-F:/data/WB/scratch/tunnel_full}"
PY="${PY:-F:/data/WB/venvs/tunnel/Scripts/python.exe}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
export COLMAP_BIN="${COLMAP_BIN:-/f/data/toolchains/colmap420/bin/colmap.exe}"

VIDEO="$(cd "$(dirname "$VIDEO")" && pwd)/$(basename "$VIDEO")"
mkdir -p "$WORK"
# COLMAP 在 Windows 上只吃 ASCII 相对路径，工作目录必须是 ASCII
case "$WORK" in *[!\ -~]*) echo "!! 工作目录必须全 ASCII: $WORK"; exit 1;; esac

echo "==================== 0. 抽帧 ===================="
if [ ! -f "$WORK/frames/frames.json" ]; then
  "$PY" "$ROOT/reconstruct.py" video --video "$VIDEO" --out "$WORK/frames" \
      --sample-hz "${SAMPLE_HZ:-2}" --max-frames "${MAX_FRAMES:-120}" --min-sharpness "${MIN_SHARPNESS:-0}"
else
  echo "已存在，跳过"
fi

echo "==================== 1. 重建（GPU） ===================="
if [ ! -d "$WORK/recon/sparse" ]; then
  bash "$ROOT/colmap_pipeline.sh" "$WORK/frames/images" "$WORK/recon"
fi

echo "==================== 2. 导出 scene.json ===================="
BEST="$(ls -d "$WORK"/recon/sparse/*/ 2>/dev/null | head -1)"
[ -n "$BEST" ] || { echo "!! 未找到稀疏模型"; exit 1; fi
# global_mapper 的模型在 sparse/0；转成相对 ROOT 的 ASCII 路径供 reconstruct.py 使用
REL="$(realpath --relative-to="$ROOT" "$BEST" 2>/dev/null || echo "$BEST")"
if [ ! -f "$WORK/scene/scene.json" ]; then
  ( cd "$ROOT" && "$PY" reconstruct.py export --model "$REL" \
      --out "$(realpath --relative-to="$ROOT" "$WORK/scene" 2>/dev/null || echo "$WORK/scene")" \
      --source-kind real_camera_video )
fi

echo "==================== 3. 网格 -> NPZ ===================="
MESH=""
for candidate in "$WORK/recon/dense/mesh.ply" "$WORK/recon/dense/mesh-delaunay.ply"; do
  [ -f "$candidate" ] && MESH="$candidate" && break
done
[ -n "$MESH" ] || { echo "!! 未找到网格，局部位定位将不可用"; }
if [ -n "$MESH" ] && [ ! -f "$WORK/scene/surface.npz" ]; then
  "$PY" "$ROOT/mesh_io.py" --ply "$MESH" --scene "$WORK/scene/scene.json" --out "$WORK/scene/surface.npz"
fi

echo "==================== 4. 病害检测（GPU） ===================="
if [ ! -f "$WORK/detect/detections.json" ]; then
  "$PY" "$ROOT/defect_detect.py" --images "$WORK/frames/images" --out "$WORK/detect" \
      --scene "$WORK/scene/scene.json" --overlay
fi

echo "==================== 5. 病害三维定位 ===================="
if [ ! -f "$WORK/localized.json" ]; then
  if [ -n "$MESH" ]; then
    "$PY" "$ROOT/localize_defects.py" --scene "$WORK/scene/scene.json" \
        --detections "$WORK/detect/detections.json" --mesh "$WORK/scene/surface.npz" \
        --out "$WORK/localized.json"
  else
    echo "无网格：跳过定位（localize_defects 会返回 pending_surface，这是设计行为）"
  fi
fi

echo ""
echo "==================== 汇总 ===================="
"$PY" - "$WORK" <<'EOF'
import json, sys, pathlib
work = pathlib.Path(sys.argv[1])
scene = json.loads((work/'scene/scene.json').read_text('utf-8'))
print(f"scene_id : {scene['scene_id']}")
print(f"相机/点数 : {len(scene['cameras'])} / {len(scene['points'])}   尺度: {scene['scale']['status']}")
q = work/'recon/quality.json'
if q.exists():
    d = json.loads(q.read_text('utf-8'))
    print(f"注册图像 : {d.get('registered_images')}/{d.get('input_images')}   "
          f"重投影 {d.get('point_mean_reprojection_error_px', float('nan')):.3f}px")
det = json.loads((work/'detect/detections.json').read_text('utf-8'))
print(f"病害候选 : {len(det['detections'])}")
loc = work/'localized.json'
if loc.exists():
    r = json.loads(loc.read_text('utf-8'))
    print(f"定位结果 : surface_available={r['surface_available']}  "
          f"命中 {r['summary']['hit_count']}/{r['summary']['requested_count']}")
    for d in r['defects'][:5]:
        p = d['points'][0] if d['points'] else None
        print(f"   {d['id']:<22} {d['label']:<6} {d['status']:<12} {d['hit_count']}/{d['requested_count']}"
              + (f"  首个三维点 {[round(v,3) for v in p]}" if p else ""))
EOF
