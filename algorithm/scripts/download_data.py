"""一键下载本算法所需的全部数据集。

用法：
    python scripts/download_data.py                 # 下载全部（约 2.6 GB）
    python scripts/download_data.py --only ttd ctcd # 只下载指定数据集
    python scripts/download_data.py --list          # 只打印数据来源清单
    python scripts/download_data.py --verify        # 只校验已下载的数据

数据都会落到 data/raw/<name>/ 下，可重复执行（已存在的会跳过）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

# ---------------------------------------------------------------- 数据来源清单
DATASETS: dict[str, dict] = {
    "tunnel_photos": {
        "title": "真实隧道衬砌现场彩色照片（主线训练数据）",
        "kind": "github_tar",
        "url": "https://codeload.github.com/cuijingqi/Tunnel_lining_multi-category_defect_segmentation_detection/tar.gz/refs/heads/master",
        "fallback_urls": [
            "https://codeload.github.com/cuijingqi/Tunnel_lining_multi-category_defect_segmentation_detection/tar.gz/refs/heads/main"
        ],
        "page": "https://github.com/cuijingqi/Tunnel_lining_multi-category_defect_segmentation_detection",
        "size": "1.8 GB",
        "license": "见仓库说明",
        "content": "crack 11375 张 / leakage 3980 张 / no defects 1958 张 / defects 25 张（文件夹级标签，无框）",
        "extract_to": "tunnel_lining/extracted",
        "expect": {"**/dataset/crack/images/*.jpg": 11375, "**/dataset/leakage/images/*.jpg": 3980},
    },
    "ttd": {
        "title": "TACK Tunnel Data —— 隧道衬砌像素级掩码（训练伪标注器）",
        "kind": "huggingface",
        "repo_id": "TACK-project/TACK_Tunnel_Data",
        "allow_patterns": ["3_img/*", "3_mask/*"],
        "page": "https://huggingface.co/datasets/TACK-project/TACK_Tunnel_Data",
        "paper": "https://arxiv.org/abs/2512.14477",
        "size": "约 910 MB",
        "license": "CC BY-NC-ND 4.0",
        "content": "3774 张 512x512 真实隧道衬砌图 + 同名像素掩码（40=Crack 160=Water 200=Leaching）",
        "extract_to": "ttd",
        "expect": {"3_img/*.png": 3774, "3_mask/*.png": 3774},
    },
    "ctcd": {
        "title": "Complex Tunnel Crack Dataset —— 隧道裂缝二值掩码（补充伪标注器）",
        "kind": "huggingface",
        "repo_id": "shiweiluo99/tunnel-crack-segmentation-dataset",
        "allow_patterns": ["Complex Tunnel Crack Dataset (CTCD)/*"],
        "page": "https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset",
        "size": "约 77 MB",
        "license": "见数据集说明",
        "content": "52 座公路隧道巡检车采集的裂缝图 + 二值掩码（train 250 / val 42）",
        "extract_to": "ctcd",
        "expect": {"**/train/*.bmp": 250},
    },
    "gpr_mendeley": {
        "title": "GPR 实测 B-scan（GPR 分支保留，非当前主线）",
        "kind": "direct_file",
        "url": "https://data.mendeley.com/public-files/datasets/ww7fd9t325/files/"
        "a7d95abe-e007-406f-a088-37a998591c64/file_downloaded",
        "filename": "GPR_data.rar",
        "page": "https://data.mendeley.com/datasets/ww7fd9t325/1",
        "paper": "doi:10.17632/ww7fd9t325.1",
        "size": "约 50 MB",
        "license": "CC BY-NC",
        "content": "2239 张 GPR 实测 B-scan（空洞 / 管线）+ 285 条原始剖面，GSSI 200/400 MHz",
        "extract_to": "gpr",
        "optional": True,
    },
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download_url(url: str, dest: Path) -> None:
    """流式下载并打印进度。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    def hook(block: int, block_size: int, total: int) -> None:
        if total <= 0:
            return
        done = min(block * block_size, total)
        pct = done / total * 100
        sys.stdout.write(f"\r    下载中 {pct:5.1f}%  {human(done)} / {human(total)}   ")
        sys.stdout.flush()

    urllib.request.urlretrieve(url, tmp, reporthook=hook)
    sys.stdout.write("\r" + " " * 60 + "\r")
    tmp.replace(dest)


def fetch_github_tar(spec: dict, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / "repo.tar.gz"
    if not archive.exists():
        urls = [spec["url"], *spec.get("fallback_urls", [])]
        last_err: Exception | None = None
        for url in urls:
            try:
                print(f"  下载 {url}")
                download_url(url, archive)
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                print(f"  失败：{exc}")
        if last_err is not None:
            raise RuntimeError(f"所有下载地址均失败：{last_err}")
    out = dest_dir / "extracted"
    if out.exists() and any(out.iterdir()):
        print(f"  已解压，跳过 -> {out}")
        return
    print(f"  解压到 {out}")
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        # 数据集中有个非 ASCII 的空占位文件，单个文件失败不影响整体
        try:
            tf.extractall(out, filter="data")
        except Exception as exc:  # noqa: BLE001
            print(f"  部分条目解压失败（可忽略）：{exc}")
            try:
                for m in tf.getmembers():
                    try:
                        tf.extract(m, out, filter="data")
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
    print(f"  完成 -> {out}")


def fetch_huggingface(spec: dict, dest_dir: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit("需要 huggingface_hub：pip install huggingface_hub") from None
    print(f"  从 Hugging Face 拉取 {spec['repo_id']}")
    snapshot_download(
        repo_id=spec["repo_id"],
        repo_type="dataset",
        allow_patterns=spec["allow_patterns"],
        local_dir=str(dest_dir),
        max_workers=8,
    )
    print(f"  完成 -> {dest_dir}")


def fetch_direct_file(spec: dict, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / spec["filename"]
    if not target.exists():
        print(f"  下载 {spec['url']}")
        download_url(spec["url"], target)
    else:
        print("  已存在，跳过下载")
    if target.suffix.lower() == ".rar":
        out = dest_dir / "extracted"
        if out.exists() and any(out.iterdir()):
            print(f"  已解压，跳过 -> {out}")
            return
        out.mkdir(parents=True, exist_ok=True)
        # Windows 自带的 bsdtar 支持读取 rar；Linux 下需要 unrar
        for cmd in (["tar", "-xf", str(target), "-C", str(out)], ["unrar", "x", "-o+", str(target), str(out)]):
            if shutil.which(cmd[0]):
                print(f"  解压：{' '.join(cmd[:2])}")
                Proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: N806
                if Proc.returncode == 0:
                    print(f"  完成 -> {out}")
                    return
        print("  [警告] 没有可用的 rar 解压工具，请手工解压后放到", out)


def is_ready(spec: dict, dest_dir: Path) -> bool:
    """数据集是否已就绪。

    不能简单用 `dest 非空` 判断：github_tar 的归档本身就落在 dest 里，
    一旦上下载中断留下残缺归档，这里会误判为已完成、跳过解压，
    后面 verify 必然失败而用户无从恢复（只能手工删目录）。
    因此 github_tar 以解压产物为准。
    """
    if not dest_dir.exists():
        return False
    if spec["kind"] == "github_tar":
        out = dest_dir / "extracted"
        return out.exists() and any(out.iterdir())
    return any(dest_dir.rglob("*"))


def verify(name: str, spec: dict, dest_dir: Path) -> bool:
    expect = spec.get("expect")
    if not expect:
        return dest_dir.exists()
    ok = True
    for pattern, want in expect.items():
        got = len(list(dest_dir.glob(pattern)))
        flag = "OK " if got >= want else "缺失"
        if got < want:
            ok = False
        print(f"    [{flag}] {pattern}: {got} / {want}")
    return ok


def print_sources() -> None:
    print("\n数据来源清单")
    print("=" * 78)
    for name, s in DATASETS.items():
        print(f"\n[{name}] {s['title']}" + ("  (可选)" if s.get("optional") else ""))
        print(f"  页面   : {s.get('page', s.get('url'))}")
        if s.get("paper"):
            print(f"  论文   : {s['paper']}")
        print(f"  许可   : {s['license']}")
        print(f"  体积   : {s['size']}")
        print(f"  内容   : {s['content']}")
    print("\n" + "=" * 78)
    print("引用格式见 algorithm/docs/REFERENCES.md\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="下载隧道病害检测算法所需数据集")
    ap.add_argument("--only", nargs="*", choices=list(DATASETS), help="只下载指定数据集")
    ap.add_argument("--list", action="store_true", help="只打印数据来源清单")
    ap.add_argument("--verify", action="store_true", help="只校验已下载的数据")
    ap.add_argument("--include-optional", action="store_true", help="同时下载标记为可选的数据集")
    args = ap.parse_args()

    if args.list:
        print_sources()
        return

    names = args.only or [n for n, s in DATASETS.items() if not s.get("optional") or args.include_optional]
    print(f"将处理：{', '.join(names)}")
    print(f"数据根目录：{RAW}\n")

    results: dict[str, bool] = {}
    for name in names:
        spec = DATASETS[name]
        dest = RAW / spec["extract_to"]
        print(f"--- [{name}] {spec['title']}")
        if args.verify:
            results[name] = verify(name, spec, dest)
            continue
        if is_ready(spec, dest):
            print(f"  目录已存在，跳过下载 -> {dest}")
        else:
            if spec["kind"] == "github_tar":
                fetch_github_tar(spec, RAW / spec["extract_to"])
            elif spec["kind"] == "huggingface":
                fetch_huggingface(spec, dest)
            elif spec["kind"] == "direct_file":
                fetch_direct_file(spec, dest)
            else:
                raise SystemExit(f"未知数据源类型: {spec['kind']}")
        results[name] = verify(name, spec, dest)
        print()

    print_sources()
    bad = [n for n, ok in results.items() if not ok]
    if bad:
        print(f"[警告] 以下数据集不完整，请检查：{bad}")
        raise SystemExit(1)
    print("全部数据就绪。")


if __name__ == "__main__":
    main()