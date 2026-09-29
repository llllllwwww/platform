#!/usr/bin/env bash
# ============================================================================
# COLMAP 4.2.0 (CUDA) 隧道重建参考链路 —— 已在本机实测跑通
#   稀疏位姿 -> 稠密点云 -> 三角网格
#
# 用法:  bash colmap_pipeline.sh <图像目录> <输出目录>
#
# ⚠️ 三个已实测的坑（踩过，别再踩）：
#   1. COLMAP 4.x 选项命名空间已统一：是 --FeatureExtraction.* / --FeatureMatching.*，
#      旧的 --SiftExtraction.use_gpu 会直接报 "unrecognised option" 并静默跳过整步。
#      但 --SiftExtraction.max_num_features 仍留在旧命名空间下（两者混用）。
#   2. mapper 的 --output_path 目录必须预先存在，否则报 "output_path is not a directory"。
#   3. poisson_mesher 在本构建里恒输出空网格（vertex 0 / face 0），三种 depth 都试过。
#      → 用 advancing_front_mesher（最佳）或 delaunay_mesher。
#   4. ALIKED/LightGlue 的 ONNX 模型默认从 github.com 在线拉取 —— 本机被墙会失败。
#      模型需预置到 <colmap>/bin/ 下，并用 --AlikedExtraction.n16rot_model_path 指定本地路径。
#      （aliked-n16rot.onnx / aliked-n32.onnx 已放入 toolchains/colmap420/bin/）
#   5. GPU 版 ONNX 需要 cublas/cublasLt/cudart/cudnn/cufft，官方包**未捆绑**；
#      ALIKED 目前走 CPU（--FeatureExtraction.use_gpu 0）即可，速度够用。
#      patch_match_stereo 走 COLMAP 自带 CUDA（静态链接），不受影响。
# ============================================================================
set -euo pipefail

IMAGES="${1:?用法: bash colmap_pipeline.sh <图像目录> <输出目录>}"
OUT="${2:?用法: bash colmap_pipeline.sh <图像目录> <输出目录>}"
COLMA="${COLMAP_BIN:-/f/data/toolchains/colmap420/bin/colmap.exe}"
FEATURE_TYPE="${FEATURE_TYPE:-SIFT}"        # 可选 ALIKED_N16ROT（需本地 onnx）
ALIKED_MODEL="${ALIKED_MODEL:-F:/data/toolchains/colmap420/bin/aliked-n16rot.onnx}"

# 必须在 cd 之前把输入路径转成绝对路径，否则相对路径会失效
IMAGES="$(cd "$IMAGES" && pwd)"

mkdir -p "$OUT"
cd "$OUT"
OUT="$(pwd)"                                # 转绝对路径，后面 cwd 会变

# COLMAP 在 Windows 上只吃 ASCII 相对路径：把输入复制到输出目录下的 images/
if [ ! -d images ]; then
  mkdir -p images
  cp "$IMAGES"/* images/
fi
echo "输入图像: $(ls images | wc -l) 张"

step () { echo ""; echo "########## $1 ##########"; }

step "1. 特征提取 (${FEATURE_TYPE})"
if [ "$FEATURE_TYPE" = "SIFT" ]; then
  "$COLMA" feature_extractor --database_path db.db --image_path images \
      --FeatureExtraction.type SIFT --FeatureExtraction.use_gpu 1 \
      --SiftExtraction.max_num_features 8192 2>&1 | tail -3
else
  # 本地 onnx 模型；use_gpu 0 走 CPU（GPU 版缺 CUDA 运行时库，见文件头说明）
  "$COLMA" feature_extractor --database_path db.db --image_path images \
      --FeatureExtraction.type "$FEATURE_TYPE" \
      --AlikedExtraction.n16rot_model_path "$ALIKED_MODEL" \
      --FeatureExtraction.use_gpu 0 2>&1 | tail -3
fi

step "2. 顺序匹配 (视频序列用 sequential；再开回环检测抗漂移)"
"$COLMA" sequential_matcher --database_path db.db \
    --FeatureMatching.use_gpu 1 --FeatureMatching.guided_matching 1 \
    --SequentialMatching.overlap 10 --SequentialMatching.loop_detection 1 2>&1 | tail -3

step "3. 位姿估计：**优先全局 SfM (global_mapper / GLOMAP)**"
# ⚠️ 隧道场景的关键修正（2026-09-29 实测）：
#   增量 mapper 在此前这条地铁前向运动序列上失败（不是通用结论；DVP 序列可成功）—— 日志是
#     "Registering initial image pair #33 and #49" → "Discarding reconstruction due to bad initial pair"
#   原因是双视图三角化在纯前向平移下病态（窄三角化角），初始像对过不了检查。
#   即使匹配很好（实测中位 78 内点、最高 799）也照样失败。
#   GLOMAP 的全局策略直接绕过"选初始像对"，同一份数据 56/56 注册成功。
#   所以：**global_mapper 是隧道的主路径，mapper 只作为对照/兜底。**
mkdir -p sparse
if "$COLMA" global_mapper --database_path db.db --image_path images \
     --output_path sparse 2>&1 | tail -4; then
  BEST="$(ls -d sparse/*/ 2>/dev/null | head -1 || true)"
fi
if [ -z "${BEST:-}" ]; then
  echo ">> global_mapper 未出模型，退回增量 mapper（对照路径）"
  "$COLMA" mapper --database_path db.db --image_path images --output_path sparse 2>&1 | tail -4
  BEST="$(ls -d sparse/*/ 2>/dev/null | head -1 || true)"
fi
if [ -z "${BEST:-}" ]; then echo "!! SfM 失败：无任何稀疏组件，后续步骤中止"; exit 1; fi
echo "选用模型: $BEST"
"$COLMA" model_analyzer --path "$BEST" 2>&1 | grep -Ei "cameras|images|points|error|track" | head -6

step "4. 稀疏模型统计滤波（注意：作用于稀疏模型目录，不是 PLY）"
mkdir -p sparse_filtered
"$COLMA" point_filtering --input_path "$BEST" --output_path sparse_filtered \
    --min_track_len 2 --max_reproj_error 4 --min_tri_angle 1.5 2>&1 | tail -3
BEST="sparse_filtered"

step "5. 图像去畸变"
"$COLMA" image_undistorter --image_path images --input_path "$BEST" \
    --output_path dense --output_type COLMAP --max_image_size 1600 2>&1 | tail -2

step "6. 稠密立体匹配 (patch_match_stereo, 走 CUDA)"
"$COLMA" patch_match_stereo --workspace_path dense --workspace_format COLMAP \
    --PatchMatchStereo.geom_consistency 1 2>&1 | tail -3

step "7. 深度融合"
"$COLMA" stereo_fusion --workspace_path dense --workspace_format COLMAP \
    --input_type geometric --output_path dense/fused.ply 2>&1 | tail -3

step "8. 网格化（注意：不要用 poisson_mesher，本构建下恒为空网格）"
"$COLMA" advancing_front_mesher --input_path dense --output_path dense/mesh.ply 2>&1 | tail -4
"$COLMA" delaunay_mesher --input_path dense --output_path dense/mesh-delaunay.ply \
    --input_type dense 2>&1 | tail -2

step "9. 纹理映射（可选；--output_path 是目录不是文件，且没有 --workspace_format 参数）"
"$COLMA" mesh_texturer --input_path dense/mesh.ply --output_path dense/textured \
    --workspace_path dense 2>&1 | tail -4 || \
  echo "   （纹理映射失败，可跳过；不影响几何）"

echo ""; echo "=== 产物 ==="
for f in sparse/*/dense/fused.ply dense/fused.ply dense/mesh.ply dense/mesh-delaunay.ply \
         dense/textured/mesh.ply; do
  [ -f "$f" ] && echo "  OK  $f  $(stat -c %s "$f") 字节" || true
done
echo ""
echo "下一步："
echo "  python reconstruct.py export --model <模型目录> --out <scene目录> --source-kind <类型>"
echo "  python mesh_io.py --ply <网格.ply> --scene <scene目录>/scene.json --out <网格.npz>"
echo "  python localize_defects.py --scene ... --detections ... --mesh ... --out ..."
