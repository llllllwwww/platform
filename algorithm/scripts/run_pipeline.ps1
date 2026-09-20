# 从数据下载到训练验证的一键流程（Windows PowerShell）
#
# 用法：
#   .\scripts\run_pipeline.ps1                      # 全流程
#   .\scripts\run_pipeline.ps1 -SkipDownload        # 跳过数据下载
#   .\scripts\run_pipeline.ps1 -Stage seg           # 只跑到分割器训练
#
# 参数：
#   -Python  解释器路径（默认当前 PATH 里的 python）
#   -Stage   all | data | seg | label | det

param(
    [string]$Python = "python",
    [string]$Stage = "all",
    [switch]$SkipDownload,
    [int]$SegEpochs = 150,
    [int]$DetEpochs = 150,
    [int]$Batch = 16,
    [string]$Device = "0"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Step($name) {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host "  $name" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan
}

$runSeg = $Stage -in @("all", "seg")
$runLabel = $Stage -in @("all", "label")
$runDet = $Stage -in @("all", "det")

# ---------------------------------------------------------------- 1. 数据
if (-not $SkipDownload -and $Stage -in @("all", "data")) {
    Step "1/5 下载数据集"
    & $Python scripts\download_data.py
}

# ---------------------------------------------------------------- 2. 切分
if ($Stage -in @("all", "data")) {
    Step "2/5 建立切分清单（按帧块分组，防泄漏）"
    & $Python src\build_scene_split.py
}

# ---------------------------------------------------------------- 3. 分割器
if ($runSeg) {
    Step "3/5 构建分割数据集并训练伪标注器"
    & $Python src\build_dataset_seg.py
    & $Python src\train.py --task segment `
        --data data\processed\ttd_seg\data.yaml `
        --model configs\yolo11s-tsd-afpn-seg.yaml `
        --imgsz 512 --batch $Batch --epochs $SegEpochs --workers 4 --device $Device
}

# ---------------------------------------------------------------- 4. 伪标注
if ($runLabel) {
    Step "4/5 用伪标注器给真实隧道照片生成检测框"
    & $Python src\pseudo_label.py `
        --weights runs\segment\afpn-seg\weights\best.pt `
        --imgsz 640 --conf 0.15 --device $Device
}

# ---------------------------------------------------------------- 5. 检测器
if ($runDet) {
    Step "5/5 训练检测器并验证"
    & $Python src\train.py --task detect `
        --data data\processed\scene_det\data.yaml `
        --model configs\yolo11s-tsd-afpn.yaml `
        --imgsz 640 --batch $Batch --epochs $DetEpochs --workers 4 --device $Device --test
    Write-Host ""
    Write-Host "完成。指标见 reports\metrics_*.json，曲线与混淆矩阵见 runs\" -ForegroundColor Green
    Write-Host "推理示例：" -ForegroundColor Green
    Write-Host "  $Python src\infer.py --weights runs\detect\afpn-det\weights\best.pt --source <图片或视频>"
}