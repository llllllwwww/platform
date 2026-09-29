"""离线轮子下载器：绕过沙箱内不可用的 pip 网络。

背景：本机 pip 的**索引可读**（`pip index versions` 正常）但**下载被挡**
（`pip install` 一律 `from versions: none`）。curl / urllib 反而可以。
所以流程是：用本脚本把「目标包 + 全部依赖」的轮子下到本地目录，
再用 `pip install --no-index --find-links <目录> <包>` 离线安装。

用法:
  python offline_wheels.py torch numpy --out F:/data/WB/cache/wheels
  python offline_wheels.py torch --index-url https://download.pytorch.org/whl/cu128

设计取舍：
  - 只做「按当前解释器标签选轮子」，不做完整版本求解（够用即可）。
  - 依赖只递归一层层取 wheel 的 METADATA，跳过已满足的环境标记。
  - 已存在的同尺寸文件直接跳过，可反复运行续传。
"""
from __future__ import annotations
import argparse
import email
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

PYPI = 'https://pypi.org/pypi/{name}/json'


def http_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'offline-wheel-fetcher'})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def http_bytes(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'offline-wheel-fetcher'})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def wheel_tags():
    """当前解释器可接受的轮子标签（优先级从高到低）。"""
    v = sys.version_info
    abi = f'cp{v.major}{v.minor}'
    tags = [f'{abi}-{abi}-win_amd64', f'{abi}-{abi}-win32', 'py3-none-any']
    # abi3：如 opencv 的 cp37-abi3-win_amd64，实测可用
    for minor in range(v.minor, 6, -1):
        tags.append(f'cp3{minor}-abi3-win_amd64')
    tags.append('py3-none-any')
    tags.append('py2.py3-none-any')
    return tags


def pick_wheel(files, tags):
    for tag in tags:
        candidates = [f for f in files if f['filename'].endswith(f'-{tag}.whl')]
        if candidates:
            # 同标签下取版本最高（PyPI 已按上传顺序，取最后一个最新）
            return candidates[-1]
    return None


def deps_of(wheel_bytes):
    """读 wheel 内 METADATA 的 Requires-Dist。

    返回 [(名字, 精确版本或 None)]。
    ⚠️ 必须解析 `==` 精确约束：否则会拿到依赖的**最新**版，
    而父包往往 pin 了具体版本（实测 pydantic 2.13.5 要 pydantic-core==2.46.5，
    但最新是 2.49.0 → pip 报 ResolutionImpossible）。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            meta_name = next(n for n in archive.namelist()
                             if n.endswith('.dist-info/METADATA'))
            metadata = email.message_from_bytes(archive.read(meta_name))
    except Exception:
        return []
    result = []
    for value in metadata.get_all('Requires-Dist') or []:
        spec = value.split(';', 1)[0].strip()
        if ';' in value:
            marker = value.split(';', 1)[1]
            if 'linux' in marker.lower() or 'darwin' in marker.lower():
                continue
            if 'extra ==' in marker:
                continue
        match = re.match(r'^([A-Za-z0-9._-]+)\s*(.*)$', spec)
        if not match:
            continue
        name, rest = match.group(1), match.group(2).strip()
        pin = None
        exact = re.search(r'==\s*([0-9][0-9A-Za-z.\-+!]*)', rest)
        if exact:
            pin = exact.group(1)
        result.append((name, pin))
    return result


def resolve(name, index_url, out, tags, seen):
    # 支持 "name" 与 "name==version" 两种写法（后者用于解决版本约束冲突）
    pinned = None
    if '==' in name:
        name, pinned = name.split('==', 1)
    key = f'{name.lower().replace("_", "-")}=={pinned}'
    if key in seen:
        return
    seen.add(key)
    try:
        meta = http_json(PYPI.format(name=name))
    except Exception as error:
        print(f'  [!] {name}: 无法查询元数据 ({type(error).__name__})', flush=True)
        return
    if pinned:
        files = [f for release in [pinned] if release in meta['releases']
                 for f in meta['releases'][release]]
        if not files:
            print(f'  [!] {name}=={pinned}: 该版本没有可用发行版', flush=True)
            return
    else:
        files = meta['urls'] or [f for r in meta['releases'].values() for f in r]
    wheel = pick_wheel(files, tags)
    if not wheel:
        print(f'  [!] {name}{"==" + pinned if pinned else ""}: 找不到匹配当前解释器的轮子', flush=True)
        return
    target = out / wheel['filename']
    if target.exists() and target.stat().st_size == wheel['size']:
        print(f'  = {wheel["filename"]} (已存在)', flush=True)
    else:
        print(f'  + {wheel["filename"]} ({wheel["size"]/1e6:.1f}MB)', flush=True)
        data = http_bytes(wheel['url'])
        target.write_bytes(data)
    for dep_name, dep_pin in deps_of(target.read_bytes()):
        resolve(f'{dep_name}=={dep_pin}' if dep_pin else dep_name,
                index_url, out, tags, seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packages', nargs='+')
    parser.add_argument('--out', default='F:/data/WB/cache/wheels')
    parser.add_argument('--index-url', default='https://pypi.org')
    parser.add_argument('--wheel-url', action='append', default=[],
                        help='直接指定轮子 URL（用于 PyTorch 这类非 PyPI 索引）')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tags = wheel_tags()
    print('解释器标签:', tags[:3], flush=True)
    seen = set()
    for url in args.wheel_url:
        name = os.path.basename(url.split('?')[0])
        target = out / name
        if target.exists():
            print(f'  = {name} (已存在)', flush=True)
        else:
            print(f'  + {name}', flush=True)
            target.write_bytes(http_bytes(url))
        for dep in deps_of(target.read_bytes()):
            resolve(dep, args.index_url, out, tags, seen)
    for package in args.packages:
        resolve(package, args.index_url, out, tags, seen)
    print('完成，目录:', out)


if __name__ == '__main__':
    main()
