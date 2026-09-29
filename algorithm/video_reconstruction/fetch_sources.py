"""Fetch official GitHub archives with provenance. Does not install or execute sources."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent

# 分组：重建(geometry) / 位姿与稠密匹配(pose) / 探伤(defect) / 前端(viewer)
# 只收录"上游官方仓库 + 明确版本"，便于复现与引用。
SOURCES = {
    # --- 已有（第一阶段） ---
    'colmap': ('colmap/colmap', '3.13.0'),
    'hloc': ('cvg/Hierarchical-Localization', 'master'),
    'LightGlue': ('cvg/LightGlue', 'main'),
    'MASt3R-SLAM': ('rmurai0610/MASt3R-SLAM', 'main'),
    'CrackSeg9k': ('Dhananjay42/crackseg9k', 'main'),
    # --- 第二批：重建主干 ---
    # COLMAP 4.2.0 源码：仅作参考。Windows 上真正可用的是官方预编译 CUDA 二进制
    # colmap-x64-windows-cuda.zip (381MB)，含 dense MVS + Poisson 网格，且支持 sm_120。
    'colmap420': ('colmap/colmap', '4.2.0'),
    'glomap': ('colmap/glomap', 'main'),                 # 全局 SfM，抗序列漂移
    'OpenMVS': ('cdcseacave/openMVS', 'master'),         # 备用稠密 MVS + 网格 + 纹理映射
    '2dgs': ('hbb1/2d-gaussian-splatting', 'main'),      # 2D 面元高斯 -> 可直接提网格
    'PGSR': ('zju3dv/PGSR', 'main'),                     # 平面化高斯，面向室内/平面表面
    # --- 第二批：无位姿/学习式几何（应对"摄像头不稳定"） ---
    'vggt': ('facebookresearch/vggt', 'main'),           # CVPR2025 最佳论文，前馈出位姿+点图
    'mast3r': ('naver/mast3r', 'main'),                  # 稠密匹配 + 点图，MASt3R-SLAM 的上游
    # --- 第二批：隧道探伤 ---
    'Tunnel200': ('Qiang-Z/Tunnel200-Dataset_IEEE_TIV', 'main'),  # 隧道衬砌裂缝像素级掩码
    'crack-seg': ('Ishaan1402/crack-seg', 'main'),
    'DeepCrack': ('yhlleo/DeepCrack', 'master'),         # 经典裂缝分割基线
    'omnicrack30k': ('ben-z-original/omnicrack30k', 'main'),  # 跨域裂缝（路桥/砌体/混凝土）
    'TunnelScan': ('KerouacDlg/TunnelScan', 'main'),     # 隧道表面病害检测模型
    'ultralytics': ('ultralytics/ultralytics', 'main'),  # YOLO 分割，工程落地用
}

def resolve_commit(repository, revision):
    """分支名（main/master）会漂移，记录当时的 commit SHA 才能复现同一份源码。"""
    url = f'https://api.github.com/repos/{repository}/commits/{revision}'
    request = urllib.request.Request(url, headers={'User-Agent': 'tunnel-reconstruction-research',
                                                   'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)['sha']
    except Exception:
        return None  # 拿不到不算致命，只是可复现性降级


def download(name, repository, revision):
    target = ROOT / 'third_party' / name
    if target.exists():
        raise FileExistsError(f'Refusing to replace existing source: {target}')
    url = f'https://codeload.github.com/{repository}/zip/{revision}'
    request = urllib.request.Request(url, headers={'User-Agent': 'tunnel-reconstruction-research'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
    archive_hash = hashlib.sha256(data).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Reject paths outside the upstream root. Do not follow archive symlinks.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            relative = Path(*Path(info.filename).parts[1:])
            if not relative.parts:
                continue
            destination = target / relative
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError(f'Unsafe ZIP entry: {info.filename}')
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
    return {'repository': f'https://github.com/{repository}', 'revision_requested': revision,
            'resolved_commit': resolve_commit(repository, revision),
            'archive_url': url, 'archive_sha256': archive_hash, 'bytes': len(data),
            'path': str(target.relative_to(ROOT)), 'checkout_type': 'official_source_archive',
            'submodules_included': False, 'weights_downloaded': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('names', nargs='*', choices=list(SOURCES))
    parser.add_argument('--record-revisions', action='store_true',
                        help='仅为已下载条目回填 resolved_commit，不重新下载')
    args = parser.parse_args()
    manifest_path = ROOT / 'third_party' / 'sources.json'
    manifest = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else {}

    if args.record_revisions:
        for name in (args.names or list(manifest)):
            if name not in SOURCES or name not in manifest:
                continue
            commit = resolve_commit(*SOURCES[name])
            manifest[name]['resolved_commit'] = commit
            print(name, commit or 'unresolved', flush=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        return

    if not args.names:
        parser.error('至少给一个仓库名，或用 --record-revisions')
    failed = []
    for name in args.names:
        target = ROOT / 'third_party' / name
        if target.is_dir():
            # 已存在：保留原记录，不重下、不算失败（脚本可安全反复运行）
            manifest.setdefault(name, {'repository': f'https://github.com/{SOURCES[name][0]}',
                                       'revision_requested': SOURCES[name][1],
                                       'path': str(target.relative_to(ROOT)),
                                       'checkout_type': 'official_source_archive',
                                       'note': 'present before this run; provenance unrecorded'})
            manifest[name]['status'] = 'already_present'
            print(name, 'already present, skipped', flush=True)
            continue
        try:
            record = download(name, *SOURCES[name])
            record['status'] = 'downloaded'
            manifest[name] = record
            print(name, 'downloaded', record['bytes'], flush=True)
        except Exception as error:
            manifest[name] = {'status': 'download_failed', 'error': str(error),
                              'repository': f'https://github.com/{SOURCES[name][0]}',
                              'revision_requested': SOURCES[name][1]}
            failed.append(name)
            print(name, type(error).__name__, str(error), flush=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
    if failed:
        print('FAILED:', ', '.join(failed), flush=True)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
