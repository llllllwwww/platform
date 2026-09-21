#!/usr/bin/env bash
# 从数据下载到训练验证的一键流程（Linux / macOS）
#
# 用法：
#   ./scripts/run_pipeline.sh                       # 全流程
#   ./scripts/run_pipeline.sh --skip-download       # 跳过数据下载
#   ./scripts/run_pipeline.sh --stage seg           # 只跑到分割器训练
#
# 参数：
#   --python   解释器路径（默认当前 PATH 里的 python3）
#   --stage    all | data | seg | label | det
#   --seg-epochs / --det-epochs / --batch / --device

set -euo pipefail

PYTHON="python3"
STAGE="all"
SKIP_DOWNLOAD=0
SEG_EPOCHS=150
DET_EPOCHS=150
BATCH=16
DEVICE="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)     PYTHON="$2"; shift 2 ;;
        --stage)      STAGE="$2"; shift 2 ;;
        --skip-download) SKIP_DOWNLOAD=1; shift ;;
        --seg-epochs) SEG_EPOCHS="$2"; shift 2 ;;
        --det-epochs) DET_EPOCHS="$2"; shift 2 ;;
        --batch)      BATCH="$2"; shift 2 ;;
        --device)     DEVICE="$2"; shift 2 ;;
        -h|--help)    sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 1 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() {
    printf '\n'
    printf '=%.0s' {1..70}; printf '\n'
    echo "  $1"
    printf '=%.0s' {1..70}; printf '\n'
}

# ---------------------------------------------------------------- 1. 数据
if [[ $SKIP_DOWNLOAD -eq 0 && ( $STAGE == "all" || $STAGE == "data" ) ]]; then
    step "1/5 下载数据集"
    "$PYTHON" scripts/download_data.py
fi

# ---------------------------------------------------------------- 2. 切分
if [[ $STAGE == "all" || $STAGE == "data" ]]; then
    step "2/5 建立切分清单（按帧块分组，防泄漏）"
    "$PYTHON" src/build_scene_split.py
fi

# ---------------------------------------------------------------- 3. 分割器
if [[ $STAGE == "all" || $STAGE == "seg" ]]; then
    step "3/5 构建分割数据集并训练伪标注器"
    "$PYTHON" src/build_dataset_seg.py
    "$PYTHON" src/train.py --task segment \
        --data data/processed/ttd_seg/data.yaml \
        --model configs/yolo11s-tsd-afpn-seg.yaml \
        --imgsz 512 --batch "$BATCH" --epochs "$SEG_EPOCHS" --workers 4 --device "$DEVICE"
fi

# ---------------------------------------------------------------- 4. 伪标注
if [[ $STAGE == "all" || $STAGE == "label" ]]; then
    step "4/5 用伪标注器给真实隧道照片生成检测框"
    "$PYTHON" src/pseudo_label.py \
        --weights runs/segment/afpn-seg/weights/best.pt \
        --imgsz 640 --conf 0.15 --device "$DEVICE"
fi

# ---------------------------------------------------------------- 5. 检测器
if [[ $STAGE == "all" || $STAGE == "det" ]]; then
    step "5/5 训练检测器并验证"
    "$PYTHON" src/train.py --task detect \
        --data data/processed/scene_det/data.yaml \
        --model configs/yolo11s-tsd-afpn.yaml \
        --imgsz 640 --batch "$BATCH" --epochs "$DET_EPOCHS" --workers 4 --device "$DEVICE" --test
    printf '\n完成。指标见 reports/metrics_*.json，曲线与混淆矩阵见 runs/\n'
    echo "推理示例："
    echo "  $PYTHON src/infer.py --weights runs/detect/afpn-det/weights/best.pt --source <图片或视频>"
fi
