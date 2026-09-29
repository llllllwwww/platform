param(
    [string]$RunName = 'dvp_repro',
    [string]$GeometryPython = "$PSScriptRoot/.venv/Scripts/python.exe",
    [string]$GpuPython = 'F:/data/WB/venvs/tunnel/Scripts/python.exe',
    [string]$Colmap = 'F:/data/toolchains/colmap420/bin/colmap.exe'
)
$ErrorActionPreference = 'Stop'
if ($RunName -notmatch '^[a-zA-Z0-9_-]+$') { throw 'RunName must use ASCII letters, numbers, underscore or hyphen.' }
$moduleRoot = $PSScriptRoot
$run = Join-Path $moduleRoot "results/$RunName"
$video = Join-Path $moduleRoot 'data/dvp_tunnel/source.webm'
if (Test-Path $run) { throw "Run directory already exists: $run. Choose another RunName." }
foreach ($executable in @($GeometryPython, $GpuPython, $Colmap)) {
    if (-not (Test-Path $executable -PathType Leaf)) { throw "Missing executable: $executable" }
}
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}
if (-not (Test-Path $video)) {
    Invoke-Checked $GeometryPython @("$moduleRoot/download_file.py", 'https://upload.wikimedia.org/wikipedia/commons/4/41/DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_%2820181008093109%29.webm', $video)
}
Invoke-Checked $GeometryPython @("$moduleRoot/reconstruct.py", 'video', '--video', $video, '--out', "$run/frames", '--sample-hz', '2', '--max-frames', '150')
Invoke-Checked $GeometryPython @("$moduleRoot/reconstruct.py", 'sfm', '--images', "$run/frames/images", '--out', "$run/reconstruction", '--matching', 'sequential', '--overlap', '8', '--source-kind', 'real_camera_video', '--max-image-size', '1600', '--max-features', '6000')
Invoke-Checked $GeometryPython @("$moduleRoot/dense_reconstruct.py", '--colmap', $Colmap, '--images', "$run/frames/images", '--model', "$run/reconstruction/model", '--out', "$run/reconstruction/dense")
Invoke-Checked $GeometryPython @("$moduleRoot/mesh_io.py", '--ply', "$run/reconstruction/dense/mesh.ply", '--scene', "$run/reconstruction/scene.json", '--out', "$run/reconstruction/surface.npz")
Invoke-Checked $GpuPython @("$moduleRoot/defect_detect.py", '--images', "$run/frames/images", '--scene', "$run/reconstruction/scene.json", '--out', "$run/detect", '--device', 'cuda', '--overlay', '--threshold', '0.7', '--min-area', '60', '--max-points', '80')
Invoke-Checked $GpuPython @("$moduleRoot/localize_defects.py", '--scene', "$run/reconstruction/scene.json", '--detections', "$run/detect/detections.json", '--mesh', "$run/reconstruction/surface.npz", '--out', "$run/localized.json", '--backend', 'gpu')
Invoke-Checked $GeometryPython @("$moduleRoot/export_web.py", '--scene', "$run/reconstruction/scene.json", '--localized', "$run/localized.json", '--detections', "$run/detect/detections.json", '--textured-mesh', "$run/reconstruction/dense/textured/mesh.ply", '--texture', "$run/reconstruction/dense/textured/texture.png", '--images', "$run/frames/images", '--frames', "$run/frames/frames.json", '--video', $video, '--quality', "$run/reconstruction/quality.json", '--out', "$run/web")
Write-Output "Completed: $run/web/index.html"
