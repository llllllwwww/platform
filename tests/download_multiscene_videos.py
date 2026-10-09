"""下载多场景复核的三段隧道原视频，校验登记哈希和抽样帧解码；不运行推理。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import Request, ProxyHandler, build_opener

PROJECT = Path(__file__).resolve().parent.parent
TARGET = PROJECT / "assets" / "videos" / "multiscene"
REGISTRY = PROJECT / "algorithm" / "experiments" / "captures.json"
MAX_BYTES = 128 * 1024 * 1024
CASES = [
    {"id": "dvp_handheld", "file": "dvp-handheld.webm", "title": "DVP 手持隧道原视频", "author": "Sikander Iqbal", "license": "CC BY-SA 4.0", "downloadUrl": "https://upload.wikimedia.org/wikipedia/commons/4/41/DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_%2820181008093109%29.webm"},
    {"id": "rail_test", "file": "rail-tunnel-short.mp4", "title": "铁路隧道短片原视频", "author": "leven87 仓库提供，原视频作者未核实", "license": "独立媒体许可未核实", "downloadUrl": "https://raw.githubusercontent.com/leven87/tunel-abnormal-detection/master/Test-2_1280_720.mp4"},
    {"id": "rail_train", "file": "rail-tunnel-long.mp4", "title": "铁路隧道长片原视频", "author": "leven87 仓库提供，原视频作者未核实", "license": "独立媒体许可未核实", "downloadUrl": "https://raw.githubusercontent.com/leven87/tunel-abnormal-detection/master/Trainingvideos_2_1280_720.mp4"},
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def official_fallback(item: dict, temporary: Path) -> tuple[str, str]:
    """只改本次传输方式，不改系统代理、DNS或原始媒体；继续严格校验原片哈希。"""
    if item["id"] == "dvp_handheld" and os.name == "nt":
        curl = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/curl.exe")
        # 原站在突发并发范围请求时可能限流；使用单连接，按 Retry-After 重试并支持断点续传。
        command = [curl, "--ipv4", "--fail", "--silent", "--show-error", "--location", "--connect-timeout", "12", "--max-time", "900", "--retry", "3", "--retry-delay", "10", "--retry-max-time", "900", "--max-filesize", str(MAX_BYTES), "--continue-at", "-", "--noproxy", "*", "--resolve", "upload.wikimedia.org:443:185.15.59.240", "--user-agent", "TunnelWorkbench/1.0 (https://github.com/llllllwwww/platform)", "--output", str(temporary), item["downloadUrl"]]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=950)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Wikimedia 原站下载失败")
        return "Wikimedia 原站 HTTPS 单连接下载（TLS 验证保留，未修改系统设置）", item["downloadUrl"]
    if item["id"] in {"rail_test", "rail_train"}:
        source_name = "Test-2_1280_720.mp4" if item["id"] == "rail_test" else "Trainingvideos_2_1280_720.mp4"
        # raw 域名不可达时，直接从同一个 GitHub 原仓库读取对应 blob；不使用第三方镜像。
        with TemporaryDirectory(prefix=".source-fetch-", dir=TARGET) as scratch:
            repository = Path(scratch) / "repository"
            options = ["git", "-c", "http.proxy=", "-c", "https.proxy="]
            clone = options + ["clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--single-branch", "https://github.com/leven87/tunel-abnormal-detection.git", str(repository)]
            result = subprocess.run(clone, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=75)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
            with temporary.open("wb") as stream:
                result = subprocess.run(options + ["-C", str(repository), "show", "HEAD:" + source_name], stdout=stream, stderr=subprocess.PIPE, timeout=85)
            if result.returncode:
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        return "GitHub 原仓库 Git blob（raw 域名不可达时的同源传输）", "https://github.com/leven87/tunel-abnormal-detection"
    raise RuntimeError("原始来源暂时无法连接")


def fetch(item: dict, registry: dict, verify_only: bool) -> dict:
    expected = registry[item["id"]]
    path = TARGET / item["file"]
    if path.is_file():
        if sha256(path) != expected["sha256"]:
            raise ValueError(f"已有文件与案例登记哈希不一致，保留文件并停止：{path}")
        return {**item, "transfer": "已有原视频，哈希一致", "sourcePage": expected["source_url"], "bytes": path.stat().st_size, "sha256": expected["sha256"]}
    if verify_only:
        raise FileNotFoundError(path)
    temporary = path.with_suffix(path.suffix + ".part")
    opener = build_opener(ProxyHandler({}))
    try:
        try:
            request = Request(item["downloadUrl"], headers={"User-Agent": "TunnelWorkbench-Multiscene/1.0"})
            with opener.open(request, timeout=12) as response, temporary.open("wb") as stream:
                size = 0
                final_url = response.geturl()
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("视频超出 128 MiB 下载上限")
                    stream.write(chunk)
            transfer = "原站 HTTPS 下载，原始字节未转码或裁剪"
        except (URLError, TimeoutError, OSError):
            transfer, final_url = official_fallback(item, temporary)
        size = temporary.stat().st_size
        if size > MAX_BYTES:
            raise ValueError("视频超出 128 MiB 下载上限")
        digest = sha256(temporary)
        if digest != expected["sha256"]:
            raise ValueError(f"下载文件与已有案例原片哈希不同：{item['id']}；未替换为新素材")
        temporary.replace(path)
        print(f"下载完成：{item['file']} ({size} bytes)，SHA-256 与案例一致", flush=True)
        return {**item, "transfer": transfer, "sourcePage": expected["source_url"], "finalUrl": final_url, "bytes": size, "sha256": digest}
    finally:
        if temporary.exists():
            temporary.unlink()


def check_decode(item: dict, expected: dict) -> dict:
    import cv2
    cv2.setNumThreads(1)
    path = TARGET / item["file"]
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"不能解码视频：{path}")
        width = round(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        count = round(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if width != expected["width"] or height != expected["height"] or count != expected["frames"]:
            raise ValueError(f"视频几何或帧数与案例登记不同：{item['id']} {width}x{height} / {count}")
        checks = []
        for index in sorted({0, count // 2, max(0, count - 1)}):
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, image = cap.read()
            if not ok or image is None or image.shape[:2] != (height, width):
                raise ValueError(f"抽样帧解码失败：{item['id']} frame={index}")
            checks.append(index)
        codec_number = round(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((codec_number >> (8 * i)) & 255) for i in range(4)).strip("\x00")
        print(f"解码通过：{item['file']} {width}x{height}，{count} 帧，首/中/末帧有效", flush=True)
        return {**item, "width": width, "height": height, "frames": count, "fps": fps, "durationSec": round(count / fps, 4), "codecFourcc": codec, "matchesCaptureRegistry": True, "decodeCheck": {"status": "passed", "frameIndices": checks}, "localPath": f"assets/videos/multiscene/{item['file']}"}
    finally:
        cap.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="仅复核本地文件，不请求下载")
    args = parser.parse_args()
    registry = {item["id"]: item for item in json.loads(REGISTRY.read_text(encoding="utf-8"))}
    TARGET.mkdir(parents=True, exist_ok=True)
    downloaded = {}
    # 网络下载可以独立并行，视频解码按顺序运行，避免占用多个 CPU 推理槽位。
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(fetch, item, registry, args.verify_only): item["id"] for item in CASES}
        for future in as_completed(pending):
            item = future.result()
            downloaded[item["id"]] = item
    rows = [check_decode(downloaded[item["id"]], registry[item["id"]]) for item in CASES]
    for row in rows:
        scene_file = PROJECT / "algorithm" / "web" / "multiscene" / row["id"] / "scene_data.js"
        scene_text = scene_file.read_text(encoding="utf-8")
        scene = json.loads(scene_text.split("=", 1)[1].strip().removesuffix(";"))
        detector = scene.get("detector") or {}
        row["existingScene"] = {"title": scene.get("title"), "sceneId": scene.get("scene_id"), "registeredFrames": len(scene.get("cameras") or []), "detector": {key: detector.get(key) for key in ["name", "checkpoint_sha256", "threshold", "threshold_origin"]}}
    checkpoint = PROJECT / "algorithm" / "video_reconstruction" / "data" / "crack_model" / "unet_v3.pth"
    checkpoint_digest = sha256(checkpoint) if checkpoint.is_file() else None
    same_weights = all(checkpoint_digest == row["existingScene"]["detector"].get("checkpoint_sha256") for row in rows) if checkpoint_digest else None
    manifest = {
        "schemaVersion": 1,
        "verifiedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "与多相机建模与裂缝候选复核中的三个隧道视频案例使用相同原始字节，供本地裂缝分割试用",
        "captureRegistry": "algorithm/experiments/captures.json",
        "videos": rows,
        "currentQuickInference": {"checkpoint": "algorithm/video_reconstruction/data/crack_model/unet_v3.pth", "checkpointSha256": checkpoint_digest, "threshold": 0.7, "defaultMaxFrames": 8, "sameWeightsAsExistingScenes": same_weights},
        "excludedCases": [{"id": "tumvi_fisheye", "reason": "该案例源于 TUM VI 走廊图像序列，本次只整理隧道原视频，不下载大型走廊归档"}],
        "limits": ["输入相同不代表快速推理与已有案例的候选数必然相同；采样帧、模型权重、阈值及标定有效区配置需要一致。", "此下载器只校验文件与抽样帧解码，不运行模型，不证明裂缝识别精度。", "媒体在本地保留，不自动提交到 GitHub。"],
    }
    if not args.verify_only:
        (TARGET / "sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "videos": len(rows), "totalBytes": sum(row["bytes"] for row in rows), "directory": str(TARGET)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
