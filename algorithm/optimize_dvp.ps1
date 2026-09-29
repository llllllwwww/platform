param(
    [string]$GeometryPython = "$PSScriptRoot/.venv/Scripts/python.exe",
    [string]$GpuPython = 'F:/data/WB/venvs/tunnel/Scripts/python.exe',
    [string]$Colmap = 'F:/data/toolchains/colmap420/bin/colmap.exe'
)
$ErrorActionPreference='Stop'
function Invoke-Checked {
    param([string]$Exe,[string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Exe $($Arguments -join ' ')" }
}
foreach ($executable in @($GeometryPython,$GpuPython,$Colmap)) {
    if (-not (Test-Path $executable -PathType Leaf)) { throw "Missing executable: $executable" }
}
Push-Location $PSScriptRoot
try {
    $run='results/dvp_tunnel'
    $fit="$run/regularization"
    if (-not (Test-Path "$run/surface.npz")) { throw 'First generate the DVP reconstruction described in README.md.' }
    Invoke-Checked $GeometryPython @('regularize_tunnel.py','--run',$run)
    Invoke-Checked $Colmap @('mesh_texturer','--input_path',"$fit/surface_regularized.ply",'--output_path',"$fit/textured",'--workspace_path',"$run/dense")
    Invoke-Checked $GpuPython @('relocalize_regularized.py','--run',$run,'--backend','gpu')
    Invoke-Checked $GeometryPython @('tests/verify_regularization.py')
    Invoke-Checked $GeometryPython @('export_web.py','--scene',"$run/scene.json",'--localized',"$run/localized.json",'--detections',"$run/detect/detections.json",'--textured-mesh',"$run/dense/textured_oriented/mesh.ply",'--texture',"$run/dense/textured_oriented/texture.png",'--images','data/dvp_tunnel/frames/images','--frames','data/dvp_tunnel/frames/frames.json','--video','data/dvp_tunnel/source.webm','--quality',"$run/quality.json",'--regularized',$fit,'--out','web')
    Write-Output "Completed: $PSScriptRoot/web/index.html"
} finally { Pop-Location }
