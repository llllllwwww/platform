"""下载并校验三段真实隧道检测作业视频，仅用于本地位置对应验证。"""
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import URLError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import os
import subprocess
import ssl
import re
import hashlib
import json
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE / "output" / "public-videos"
SOURCES = json.loads((HERE / "tunnel-video-cases.json").read_text(encoding="utf-8"))
MAX_BYTES = 200 * 1024 * 1024
BOUNDARY = "真实隧道检测作业公开演示；无本项目工程里程或环位真值。两段轨道隧道视频来自同一作业。人工待复核观测只验证编号、顺序和相对位置，不判定病害类型或定位精度。"


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            sha.update(chunk)
    return sha.hexdigest()


def local_target(name):
    if not re.fullmatch(r"[a-zA-Z0-9._-]+\.mp4", name):
        raise ValueError("素材文件名必须为本目录内的 mp4 文件")
    return ROOT / name


def fetch(url, target):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(url, headers={"User-Agent": "platform-local-video-validation"}), timeout=45) as source, target.open("wb") as output:
            size = 0
            while chunk := source.read(1048576):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("公开视频超过 200 MB，停止下载")
                output.write(chunk)
        return "Python HTTPS"
    except URLError as exc:
        # 部分官网未发送完整证书链，Windows 的证书存储可正确验证它；不关闭 TLS 校验。
        if os.name != "nt" or not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        env = dict(os.environ, SLZJ_MEDIA_URL=url, SLZJ_MEDIA_TARGET=str(target))
        command = "Invoke-WebRequest -UseBasicParsing -Uri $env:SLZJ_MEDIA_URL -OutFile $env:SLZJ_MEDIA_TARGET -TimeoutSec 45 -ErrorAction Stop"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], env=env, check=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        if target.stat().st_size > MAX_BYTES:
            raise ValueError("公开视频超过 200 MB，停止使用")
        return "Windows HTTPS（系统证书验证）"


def download(item):
    target = local_target(item["file"])
    transfer = "已存在且 SHA-256 一致"
    if not target.is_file() or digest(target) != item["sha256"]:
        temporary = ROOT / (item["file"] + "." + uuid.uuid4().hex + ".part")
        try:
            transfer = fetch(item["url"], temporary)
            if digest(temporary) != item["sha256"]:
                raise ValueError("文件校验与记录不一致，请核查源端更新")
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
    result = dict(item, bytes=target.stat().st_size, boundary=BOUNDARY, transfer=transfer, checkedAt=datetime.now(timezone.utc).isoformat())
    if item.get("playbackFile"):
        playback = local_target(item["playbackFile"])
        cached = PREVIOUS.get(item["file"], {})
        valid = playback.is_file() and cached.get("playbackSha256") == digest(playback) and cached.get("sha256") == item["sha256"]
        if not valid:
            if not FFMPEG:
                raise ValueError("车载雷达原片为 HEVC；请用 --ffmpeg 指定 FFmpeg 生成 H.264 播放副本，保留原片")
            temporary = ROOT / (playback.stem + "." + uuid.uuid4().hex + ".part.mp4")
            try:
                subprocess.run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(target), "-map", "0:v:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast", "-threads", "2", "-movflags", "+faststart", str(temporary)], check=True, timeout=180)
                temporary.replace(playback)
            finally:
                if temporary.exists():
                    temporary.unlink()
        result.update(playbackSha256=digest(playback), playbackBytes=playback.stat().st_size, conversion="仅为浏览器兼容将 HEVC 转为 H.264/yuv420p；不改变原视频分辨率、画幅和视频时序，去音轨。完整原片保留。")
    print("OK " + item["file"] + " " + str(result["bytes"]) + " bytes; " + transfer, flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下载并校验三段真实隧道检测视频，车载雷达 HEVC 原片另生成 H.264 副本")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_EXE"))
    FFMPEG = parser.parse_args().ffmpeg
    previous_path = ROOT / "sources.json"
    PREVIOUS = {row["file"]: row for row in json.loads(previous_path.read_text(encoding="utf-8"))} if previous_path.is_file() else {}
    ROOT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(download, SOURCES))
    (ROOT / "sources.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["# 本地隧道检测视频验证素材", "", "当前保留 3 段真实隧道检测作业视频：轨道隧道扫描的设备近景、人员跟拍，以及高速公路隧道车载探地雷达检测。两段轨道隧道素材属于同一作业的不同拍摄片段，不当作三个独立工程样本。", "", "非隧道墙面 / 结构素材及其播放副本、标注、截图已经撤回清理。", "", "| 播放文件 | 内容 | 人工待复核观测时刻 |", "|---|---|---|"]
    rows.extend(f"| `{r.get('playbackFile', r['file'])}` | {r['inspectionContext']} | {' / '.join(str(p['time']) for p in r['points'])} s |" for r in results)
    rows.extend(["", BOUNDARY, "", "视频已抽帧核对。地铁近景含衬砌接缝；车载原片还有隧道外道路和软件展示，仅在 10 / 14 / 18 秒的第一处隧道内选点。画面接缝、痕迹不代表确诊病害，重复拍摄也不等于不同实体。", "", "车载雷达原片为 HEVC；完整保留，并另生成 H.264 浏览器副本，不裁剪画面或时序。sources.json 分别记录原片与副本的 SHA-256、大小、来源、独立授权边界。公开页面未注明视频单独复用许可；只做本地验证，媒体和输出不随代码发布。", "", "试用：平台默认 B202609 / T-001 导入 ../现场病害对应验证.json，再打开 highway-tunnel-gpr-h264.mp4，在 10 / 14 / 18 秒复核。不要重复导入已有编号。", "", "复现：先启动平台，再运行 npm run test:evidence、npm run test:positions 和 npm run test:layout。完整重建原片及副本：python tests/download_public_videos.py --ffmpeg \"你的 ffmpeg.exe 路径\"。", ""])
    (ROOT / "README.md").write_text("\n".join(rows), encoding="utf-8")
