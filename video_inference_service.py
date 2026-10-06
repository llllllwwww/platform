"""本地视频推理桥接服务。

浏览器只能把视频作为媒体对象播放，不能直接启动本机 Python。这个模块给
启动平台.py 增加一个仅监听 127.0.0.1 的上传与任务接口：收到本地录像后，
保存到 runtime/video_inference，抽取代表帧，调用 algorithm/defect_detect.py，
并把掩码、叠加图、候选 JSON 供智能解析页面复核。
"""
from __future__ import annotations

from email.parser import BytesParser
from email.policy import default as email_default
import datetime as dt
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_FRAMES = 8
MAX_MAX_FRAMES = 96
DEFAULT_TORCH_THREADS = 2


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_name(value: str, fallback: str = "video") -> str:
    value = Path(value or fallback).name
    value = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff ]+", "_", value).strip(" .")
    return value[:160] or fallback


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", value)


class VideoInferenceBusyError(RuntimeError):
    """已有本地视频推理任务占用唯一 CPU 推理槽位。"""


class VideoInferenceService:
    """单进程、单并发的本地视频推理任务管理器。"""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.algorithm_root = self.root / "algorithm"
        self.runtime_root = self.root / "runtime" / "video_inference"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slzj-video-inference")
        self._detector_module = None
        self._detector_cache = {}

    def _snapshot(self, job: dict) -> dict:
        with self.lock:
            result = dict(job)
            result["progress"] = dict(job.get("progress") or {})
            result["result"] = dict(job.get("result") or {}) if job.get("result") else None
            result["log"] = list(job.get("log") or [])[-20:]
            for private_key in list(result):
                if private_key.startswith("_"):
                    result.pop(private_key, None)
            return result

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(safe_id(job_id))
            return self._snapshot(job) if job else None

    def _update(self, job_id: str, **changes) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            for key, value in changes.items():
                if key == "progress" and isinstance(value, dict):
                    job.setdefault("progress", {}).update(value)
                elif key == "log_append":
                    job.setdefault("log", []).append(str(value))
                    job["log"] = job["log"][-40:]
                else:
                    job[key] = value

    def create(self, file_bytes: bytes, filename: str, fields: dict[str, str]) -> dict:
        if not file_bytes:
            raise ValueError("没有收到视频文件内容。")
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise ValueError("视频文件超过 512 MB 限制，请先导出较短的检测片段。")
        extension = Path(filename).suffix.lower()
        if extension not in {".mp4", ".webm", ".mov", ".m4v", ".ogv", ".avi"}:
            raise ValueError("当前只接受 MP4、WebM、MOV、M4V、OGV 或 AVI 视频。")
        try:
            max_frames = int(fields.get("maxFrames") or DEFAULT_MAX_FRAMES)
        except ValueError as exc:
            raise ValueError("采样帧数必须是整数。") from exc
        max_frames = max(8, min(MAX_MAX_FRAMES, max_frames))
        try:
            threshold = float(fields.get("threshold") or 0.7)
        except ValueError as exc:
            raise ValueError("裂缝候选阈值必须是 0 到 1 之间的数字。") from exc
        if not 0.05 < threshold < 0.99:
            raise ValueError("裂缝候选阈值必须在 0.05 到 0.99 之间。")
        # 推理模型占用较多 CPU 内存。服务只允许一个排队或运行中的任务，
        # 避免连续点击导入后形成不可见队列，最终让浏览器看起来像“卡退”。
        with self.lock:
            active = next((item for item in self.jobs.values() if item.get("status") in {"uploading", "queued", "running"}), None)
            if active:
                phase = (active.get("progress") or {}).get("phase") or active.get("status")
                raise VideoInferenceBusyError(f"已有视频推理任务正在处理（{phase}），请等待完成后再导入下一个视频。")
            job_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            job_dir = self.runtime_root / job_id
            input_dir = job_dir / "input"
            input_dir.mkdir(parents=True, exist_ok=False)
            input_path = input_dir / safe_name(filename, "现场视频.mp4")
            input_path.write_bytes(file_bytes)
            job = {
                "id": job_id,
                "status": "queued",
                "filename": input_path.name,
                "batchId": fields.get("batchId") or "",
                "taskId": fields.get("taskId") or "",
                "taskName": fields.get("taskName") or "",
                "createdAt": now_iso(),
                "startedAt": None,
                "finishedAt": None,
                "progress": {"phase": "排队等待", "percent": 0, "frames": 0, "totalFrames": 0, "candidates": 0},
                "detector": {"name": "crack-seg U-Net", "threshold": threshold, "device": fields.get("device") or "cpu"},
                "result": None,
                "error": None,
                "log": [],
                "_dir": job_dir,
                "_input": input_path,
                "_max_frames": max_frames,
                "_threshold": threshold,
                "_device": fields.get("device") if fields.get("device") in {"cpu", "cuda"} else "cpu",
            }
            self.jobs[job_id] = job
        self.pool.submit(self._run, job_id)
        return self._snapshot(job)

    def _python_candidates(self) -> list[Path]:
        values = []
        configured = os.environ.get("SLZJ_VIDEO_PYTHON")
        if configured:
            values.append(Path(configured))
        if os.name == "nt":
            values.append(self.algorithm_root / "video_reconstruction" / ".venv" / "Scripts" / "python.exe")
        else:
            values.append(self.algorithm_root / "video_reconstruction" / ".venv" / "bin" / "python")
        values.append(Path(sys.executable))
        result = []
        seen = set()
        for value in values:
            try:
                resolved = value.expanduser().resolve()
            except OSError:
                continue
            if str(resolved).lower() not in seen and resolved.is_file():
                seen.add(str(resolved).lower())
                result.append(resolved)
        return result

    def _choose_python(self) -> tuple[Path, str]:
        configured = os.environ.get("SLZJ_VIDEO_PYTHON")
        candidates = [Path(configured)] if configured else [Path(sys.executable)]
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved.is_file():
                return resolved, "configured" if configured else "启动器 Python"
        raise RuntimeError("找不到视频推理 Python。请设置环境变量 SLZJ_VIDEO_PYTHON，或使用带有 Python 3 的启动器。")

    def _extract_frames(self, job_id: str, job: dict) -> tuple[Path, list[dict], dict]:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("当前启动平台的 Python 缺少 opencv-python 或 numpy，无法抽取视频帧。") from exc
        frame_dir = job["_dir"] / "frames" / "images"
        frame_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(job["_input"]))
        if not cap.isOpened():
            raise RuntimeError("Python 无法打开该视频文件；请使用浏览器可播放的 MP4（H.264）或 WebM。")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if total <= 0:
            total = job["_max_frames"]
        count = min(job["_max_frames"], total)
        indices = np.linspace(0, max(0, total - 1), count, dtype=int).tolist()
        frames = []
        try:
            for n, frame_index in enumerate(indices, 1):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                name = f"frame_{n:04d}.jpg"
                output = frame_dir / name
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
                if not ok:
                    continue
                encoded.tofile(str(output))
                frames.append({
                    "index": n - 1,
                    "sourceFrame": int(frame_index),
                    "name": name,
                    "timeSec": round(float(frame_index) / fps, 4) if fps > 0 else None,
                    "relativeProgress": round(float(frame_index) / max(1, total - 1), 6),
                    "url": f"/api/video-inference/file/{job_id}/frames/images/{name}",
                })
                if n == 1 or n == len(indices) or n % 4 == 0:
                    self._update(job_id, progress={"phase": "抽取视频代表帧", "percent": 12 + round(n / max(1, len(indices)) * 23), "frames": n, "totalFrames": len(indices)}, log_append=f"抽取代表帧 {n}/{len(indices)}")
        finally:
            cap.release()
        if not frames:
            raise RuntimeError("视频没有抽取到可处理的画面帧。")
        metadata = {"fps": fps, "totalFrames": total, "durationSec": round(total / fps, 4) if fps > 0 else None, "width": width, "height": height, "sampledFrames": len(frames)}
        (job["_dir"] / "frames" / "frames.json").write_text(json.dumps({"video": metadata, "frames": frames}, ensure_ascii=False, indent=2), "utf-8")
        return frame_dir, frames, metadata

    @staticmethod
    def _configure_torch_threads(torch) -> int:
        raw = os.environ.get("SLZJ_VIDEO_TORCH_THREADS", str(DEFAULT_TORCH_THREADS))
        try:
            count = max(1, min(2, int(raw)))
        except ValueError:
            count = DEFAULT_TORCH_THREADS
        try:
            torch.set_num_threads(count)
        except (RuntimeError, AttributeError):
            pass
        try:
            torch.set_num_interop_threads(1)
        except (RuntimeError, AttributeError):
            # 进程中若已有其他并行工作，PyTorch 会拒绝重复设置；不影响推理。
            pass
        return count

    def _load_detector_module(self):
        if self._detector_module is not None:
            return self._detector_module
        import importlib.util
        module_path = self.algorithm_root / "defect_detect.py"
        spec = importlib.util.spec_from_file_location("slzj_defect_detect", module_path)
        if not spec or not spec.loader:
            raise RuntimeError("无法加载 algorithm/defect_detect.py。")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._detector_module = module
        return module

    def _run_detector_inline(self, job_id: str, job: dict, frame_dir: Path, python: Path, probe: str) -> Path:
        """在启动器 Python 的后台线程中复用现有模型函数，避免每个任务重复启动解释器。"""
        import hashlib
        import time
        import cv2
        import numpy as np
        import torch
        torch_threads = self._configure_torch_threads(torch)
        module = self._load_detector_module()
        checkpoint = self.algorithm_root / "video_reconstruction" / "data" / "crack_model" / "unet_v3.pth"
        repo = self.algorithm_root / "video_reconstruction" / "third_party" / "crack-seg"
        if not checkpoint.is_file() or not repo.is_dir():
            raise RuntimeError("缺少本地 crack-seg 模型环境：需要 algorithm/video_reconstruction/data/crack_model/unet_v3.pth 和 third_party/crack-seg。")
        device = job["_device"]
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
            self._update(job_id, detector={**job["detector"], "device": "cpu", "note": "未检测到 CUDA，已回退 CPU"})
        detect_dir = job["_dir"] / "detect"
        detect_dir.mkdir(parents=True, exist_ok=True)
        (detect_dir / "masks").mkdir(exist_ok=True)
        (detect_dir / "overlay").mkdir(exist_ok=True)
        self._update(job_id, detector={**job["detector"], "device": device, "python": str(python), "probe": probe, "torchThreads": torch_threads}, progress={"phase": "加载 crack-seg U-Net", "percent": 40}, log_append=f"在启动器 Python 后台线程中加载模型（Torch 线程上限 {torch_threads}）")
        cache_key = (str(checkpoint.resolve()), device)
        if cache_key in self._detector_cache:
            predictor, torch_device = self._detector_cache[cache_key]
            self._update(job_id, log_append="复用本地已加载的 crack-seg 模型")
        else:
            predictor, torch_device = module.load_predictor(repo, checkpoint, device)
            self._detector_cache[cache_key] = (predictor, torch_device)
        files = sorted(path for path in frame_dir.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg"})
        threshold = job["_threshold"]
        detections, stats = [], []
        started = time.time()
        checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        self._update(job_id, log_append=f"模型已加载：{len(files)} 张代表帧，设备={torch_device}")
        for index, path in enumerate(files, 1):
            bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                raise ValueError(f"无法读取图像: {path}")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            probs, mask, _ = predictor.predict_large_image(rgb, threshold=threshold)
            mask = (mask > 0).astype(np.uint8)
            ratio = float(mask.mean())
            ok, encoded = cv2.imencode(".png", mask * 255)
            if not ok:
                raise ValueError(f"无法保存掩码: {path.name}")
            encoded.tofile(str(detect_dir / "masks" / f"{path.stem}.png"))
            regions = module.components(mask, 30)
            for j, (ys, xs) in enumerate(regions):
                confidence = float(probs[ys, xs].mean())
                pixels = module.sample_pixels(ys, xs, 80)
                detections.append({"id": f"{path.stem}-d{j}", "label": "crack_candidate", "review_status": "unreviewed", "confidence": round(confidence, 4), "image_name": path.name, "pixels": pixels.tolist(), "mask_area_px": int(len(ys))})
            overlay = bgr.copy()
            overlay[mask > 0] = (0, 0, 255)
            ok, overlay_bytes = cv2.imencode(".png", cv2.addWeighted(bgr, 0.6, overlay, 0.4, 0))
            if ok:
                overlay_bytes.tofile(str(detect_dir / "overlay" / path.name))
            stats.append({"image": path.name, "area_ratio": ratio, "regions": len(regions), "pixels_kept": int(sum(len(module.sample_pixels(*region, 80)) for region in regions))})
            candidates = len(detections)
            self._update(job_id, progress={"phase": "运行 crack-seg U-Net", "percent": 40 + round(index / max(1, len(files)) * 48), "frames": index, "totalFrames": len(files), "candidates": candidates}, log_append=f"推理帧 {index}/{len(files)}，累计候选观测 {candidates}")
        result = {"coordinate_space": "original_image_pixels", "scene_id": None, "detector": {"name": "crack-seg U-Net (external architecture)", "weights": str(checkpoint), "checkpoint_sha256": checkpoint_sha, "threshold": threshold, "threshold_origin": "local_video_inference", "camera_validity_mask_applied": False, "device": str(torch_device), "torch_threads": torch_threads, "min_area_px": 30}, "detections": detections, "per_image": stats, "limits": ["模型属于裂缝候选分割；候选需要人工复核。", "confidence 是掩码内平均概率，不代表现场定位精度。", "像素已抽稀到每病害 80 点，用于后续证据定位。"]}
        result_path = detect_dir / "detections.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        self._update(job_id, log_append=f"Python 推理完成：{len(detections)} 个候选观测，耗时 {time.time() - started:.0f}s")
        return result_path

    def _run_detector_subprocess(self, job_id: str, job: dict, frame_dir: Path, python: Path, probe: str) -> Path:
        device = job["_device"]
        checkpoint = self.algorithm_root / "video_reconstruction" / "data" / "crack_model" / "unet_v3.pth"
        repo = self.algorithm_root / "video_reconstruction" / "third_party" / "crack-seg"
        if not checkpoint.is_file() or not repo.is_dir():
            raise RuntimeError("缺少本地 crack-seg 模型环境：需要 algorithm/video_reconstruction/data/crack_model/unet_v3.pth 和 third_party/crack-seg。")
        detect_dir = job["_dir"] / "detect"
        command = [str(python), str(self.algorithm_root / "defect_detect.py"), "--images", str(frame_dir), "--out", str(detect_dir), "--repo", str(repo), "--checkpoint", str(checkpoint), "--threshold", str(job["_threshold"]), "--min-area", "30", "--max-points", "80", "--overlay", "--device", device]
        self._update(job_id, detector={**job["detector"], "device": device, "python": str(python), "probe": probe}, progress={"phase": "运行 crack-seg U-Net", "percent": 40}, log_append="启动外部 Python：" + " ".join(command[:3]) + " …")
        env = os.environ.copy(); env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(command, cwd=str(self.algorithm_root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.strip()
            if not line: continue
            self._update(job_id, log_append=line)
            match = re.search(r"\[(\d+)/(\d+)\].*候选观测\s+(\d+)", line)
            if match:
                done, total, candidates = map(int, match.groups())
                self._update(job_id, progress={"phase": "运行 crack-seg U-Net", "percent": 40 + round(done / max(1, total) * 48), "frames": done, "totalFrames": total, "candidates": candidates})
        code = process.wait()
        if code != 0: raise RuntimeError(f"Python 推理进程退出码 {code}。最近日志：{self._snapshot(job).get('log', [])[-3:]}")
        result = detect_dir / "detections.json"
        if not result.is_file(): raise RuntimeError("Python 推理结束但没有生成 detections.json。")
        return result

    def _run_detector(self, job_id: str, job: dict, frame_dir: Path) -> Path:
        python, probe = self._choose_python()
        if python.resolve() == Path(sys.executable).resolve():
            return self._run_detector_inline(job_id, job, frame_dir, python, probe)
        return self._run_detector_subprocess(job_id, job, frame_dir, python, probe)

    def _build_review(self, job_id: str, job: dict, detections_path: Path, frames: list[dict], metadata: dict) -> dict:
        detections = json.loads(detections_path.read_text("utf-8"))
        by_image = {frame["name"]: frame for frame in frames}
        frame_stats = {item.get("image"): item for item in detections.get("per_image", [])}
        candidate_rows = []
        for row in detections.get("detections", []):
            frame = by_image.get(row.get("image_name"), {})
            image_name = row.get("image_name") or ""
            stem = Path(image_name).stem
            candidate_rows.append({
                "id": row.get("id"), "imageName": image_name, "timeSec": frame.get("timeSec"),
                "relativeProgress": frame.get("relativeProgress"), "confidence": row.get("confidence"),
                "maskAreaPx": row.get("mask_area_px"), "reviewStatus": row.get("review_status", "unreviewed"),
                "frameUrl": frame.get("url"),
                "maskUrl": f"/api/video-inference/file/{job_id}/detect/masks/{stem}.png",
                "overlayUrl": f"/api/video-inference/file/{job_id}/detect/overlay/{image_name}",
            })
        candidate_rows.sort(key=lambda item: ((item.get("timeSec") is None), item.get("timeSec") or 0, -(item.get("confidence") or 0)))
        frame_rows = []
        for frame in frames:
            stat = frame_stats.get(frame["name"], {})
            frame_rows.append({**frame, "regions": stat.get("regions", 0), "areaRatio": stat.get("area_ratio", 0)})
        preview = candidate_rows[:18]
        result = {
            "schemaVersion": 1, "jobId": job_id, "source": {"filename": job["filename"], **metadata},
            "detector": detections.get("detector", {}), "frames": frame_rows,
            "candidates": candidate_rows, "limits": detections.get("limits", []),
            "createdAt": now_iso(),
        }
        review_path = job["_dir"] / "review.json"
        review_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        return {
            "candidateCount": len(candidate_rows), "frameCount": len(frame_rows),
            "jsonUrl": f"/api/video-inference/file/{job_id}/review.json",
            "reviewUrl": f"/api/video-inference/file/{job_id}/review.json",
            "preview": preview,
            "source": metadata,
        }

    def _run(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            return
        self._update(job_id, status="running", startedAt=now_iso(), progress={"phase": "准备视频推理", "percent": 3}, log_append="任务开始")
        try:
            frame_dir, frames, metadata = self._extract_frames(job_id, job)
            self._update(job_id, progress={"phase": "代表帧已准备", "percent": 38, "frames": len(frames), "totalFrames": len(frames)})
            detections_path = self._run_detector(job_id, job, frame_dir)
            result = self._build_review(job_id, job, detections_path, frames, metadata)
            self._update(job_id, status="completed", finishedAt=now_iso(), progress={"phase": "推理完成，已进入候选复核", "percent": 100, "frames": result["frameCount"], "totalFrames": result["frameCount"], "candidates": result["candidateCount"]}, result=result, log_append=f"完成：{result['candidateCount']} 个候选观测")
        except Exception as exc:  # pragma: no cover - exercised by environment failures
            message = str(exc) or exc.__class__.__name__
            self._update(job_id, status="failed", finishedAt=now_iso(), error=message, progress={"phase": "推理失败", "percent": 100}, log_append=traceback.format_exc(limit=3))

    def file_path(self, job_id: str, relative: str) -> Path | None:
        job_id = safe_id(job_id)
        relative = unquote(relative).replace("\\", "/")
        if not job_id or relative.startswith("/") or ".." in Path(relative).parts or relative.startswith("input/"):
            return None
        base = (self.runtime_root / job_id).resolve()
        target = (base / relative).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            return None
        return target if target.is_file() else None


class LocalWorkBenchHandler(SimpleHTTPRequestHandler):
    """静态文件服务加本地视频推理 API。"""

    def __init__(self, *args, service: VideoInferenceService | None = None, **kwargs):
        self.inference_service = service
        super().__init__(*args, **kwargs)

    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        service = self.inference_service
        parsed = urlparse(self.path)
        if service and parsed.path == "/api/video-inference/health":
            self._send_json(200, {"ok": True, "service": "local-video-inference", "python": str(sys.executable)})
            return
        if service and parsed.path == "/api/video-inference/status":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            job = service.get(job_id)
            if not job:
                self._send_json(404, {"error": "找不到推理任务。"})
            else:
                self._send_json(200, job)
            return
        prefix = "/api/video-inference/file/"
        if service and parsed.path.startswith(prefix):
            parts = parsed.path[len(prefix):].split("/", 1)
            if len(parts) != 2:
                self._send_json(400, {"error": "文件路径不完整。"})
                return
            target = service.file_path(parts[0], parts[1])
            if not target:
                self._send_json(404, {"error": "推理结果文件不存在。"})
                return
            data = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        service = self.inference_service
        parsed = urlparse(self.path)
        if not service or parsed.path != "/api/video-inference/start":
            self._send_json(404, {"error": "未知的本地 API。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES + 8 * 1024 * 1024:
            self._send_json(413, {"error": "上传内容为空或超过 512 MB 限制。"})
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self._send_json(415, {"error": "视频推理上传必须使用 multipart/form-data。"})
            return
        try:
            body = self.rfile.read(length)
            envelope = (b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body)
            message = BytesParser(policy=email_default).parsebytes(envelope)
            file_bytes = None
            filename = "现场视频.mp4"
            fields = {}
            for part in message.iter_parts():
                params = dict(part.get_params(header="content-disposition", unquote=True) or [])
                name = params.get("name")
                if name == "video":
                    file_bytes = part.get_payload(decode=True) or b""
                    filename = params.get("filename") or filename
                elif name in {"batchId", "taskId", "taskName", "maxFrames", "threshold", "device"}:
                    fields[name] = part.get_content().strip()
            if file_bytes is None:
                raise ValueError("请求中没有 video 文件字段。")
            job = service.create(file_bytes, filename, fields)
            self._send_json(202, job)
        except VideoInferenceBusyError as exc:
            self._send_json(409, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - malformed multipart / OS errors
            traceback.print_exc()
            self._send_json(500, {"error": f"无法创建视频推理任务：{exc}"})

    def log_message(self, fmt: str, *args) -> None:
        # 保留关键 HTTP 日志，但不把上传内容写入终端。
        if self.path.startswith("/api/video-inference"):
            super().log_message(fmt, *args)
