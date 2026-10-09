"""手动启动本次视频重建，复用视频推理的单任务资源槽位。"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
    temporary.replace(path)


class ReconstructionBusyError(RuntimeError):
    pass


class VideoReconstructionService:
    def __init__(self, inference):
        self.inference = inference
        self.jobs = {}
        self.lock = inference.lock

    def snapshot(self, job):
        return copy.deepcopy({k: v for k, v in job.items() if not k.startswith("_")})

    def busy(self):
        with self.lock:
            return any(j["status"] in {"queued", "running"} for j in self.jobs.values())

    def get(self, job_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id or ""):
            return None
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                path = self.inference.runtime_root / job_id / "reconstruction.json"
                if not path.is_file():
                    return None
                job = json.loads(path.read_text("utf-8"))
                if job.get("id") != job_id:
                    return None
                if job.get("status") in {"queued", "running"}:
                    job.update(status="failed", finishedAt=now_iso(), error="本地服务重新启动，上次建模已中断。原检测结果仍保留，可点击按钮重新启动建模。")
                    job["progress"].update(phase="上次建模已中断")
                    write_atomic(path, job)
                self.jobs[job_id] = job
            return self.snapshot(job)

    def update(self, job_id, **changes):
        with self.lock:
            job = self.jobs[job_id]
            for key, value in changes.items():
                if key == "progress":
                    job["progress"].update(value)
                elif key == "log_append":
                    job.setdefault("log", []).append(str(value))
                    job["log"] = job["log"][-20:]
                else:
                    job[key] = value
            # 持久化状态使浏览器刷新及服务重启仍能找回成果；不保存视频字节到 JSON。
            write_atomic(self.inference.runtime_root / job_id / "reconstruction.json", self.snapshot(job))

    def start(self, fields):
        job_id = fields.get("jobId")
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
            raise ValueError("请提供有效的已完成检测任务 ID。")
        if not fields.get("batchId") or not fields.get("taskId"):
            raise ValueError("缺少检测批次或任务，不能关联建模结果。")
        try:
            maximum = int(fields.get("maxFrames", 24))
        except (TypeError, ValueError) as exc:
            raise ValueError("补抽建模帧数必须是整数。") from exc
        if not 12 <= maximum <= 96:
            raise ValueError("补抽建模帧数必须在 12–96 之间。")
        with self.lock:
            source = self.inference.get(job_id)
            if not source or source.get("status") != "completed":
                raise ValueError("先完成本次视频裂缝检测，再手动启动三维重建。")
            # 旧版没有服务器任务清单；首次启动时用浏览器的任务上下文补齐，保留该来源标记。
            if not source.get("batchId") and not source.get("taskId"):
                internal = self.inference.jobs[job_id]
                internal.update(batchId=str(fields["batchId"]), taskId=str(fields["taskId"]), recoveredContext=True)
                self.inference.persist(job_id)
                source = self.inference.get(job_id)
            if source.get("batchId") != fields["batchId"] or source.get("taskId") != fields["taskId"]:
                raise ValueError("该检测成果不属于当前批次和任务，请切回原任务后操作。")
            bundle = self.inference.workspace.refresh(job_id, verify_source=True) if hasattr(self.inference, "workspace") else None
            if bundle and bundle["manifest"]["errors"]:
                raise ValueError("；".join(bundle["manifest"]["errors"]))
            previous = self.get(job_id)
            if previous and previous["status"] in {"queued", "running", "completed"}:
                return previous
            if self.busy() or any(j.get("status") in {"queued", "running", "uploading"} for j in self.inference.jobs.values()):
                raise ReconstructionBusyError("已有视频检测或三维重建正在运行，请等当前任务完成后再启动。")
            directory = self.inference.runtime_root / job_id
            if not (directory / "detect" / "detections.json").is_file() or not (directory / "review.json").is_file():
                raise ValueError("本机检测成果缺失，请重新导入视频并完成检测。")
            run_id = "r-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            out = directory / "reconstruction" / run_id
            job = {"id": job_id, "runId": run_id, "sourceJobId": job_id,
                "batchId": source["batchId"], "taskId": source["taskId"], "filename": source["filename"],
                "status": "queued", "createdAt": now_iso(), "startedAt": None, "finishedAt": None,
                "progress": {"phase": "等待手动建模任务启动", "percent": 0, "registeredFrames": 0, "sparsePoints": 0, "localizedCandidates": 0},
                "result": None, "error": None, "maxFrames": maximum, "log": [], "_dir": out,
                "sourceDetectionRevision": bundle["manifest"]["stages"]["detection"]["revision"] if bundle else None,
                "sourceSha256": bundle["manifest"]["source"]["sha256"] if bundle else source.get("sourceSha256")}
            self.jobs[job_id] = job
            self.update(job_id)
            self.inference.pool.submit(self.run, job_id)
            return self.snapshot(job)

    def run(self, job_id):
        job = self.jobs[job_id]
        out = job["_dir"]
        # 独立进程运行 PyCOLMAP，原生异常不会关闭工作台 HTTP 服务。
        python = os.environ.get("SLZJ_RECONSTRUCTION_PYTHON") or sys.executable
        command = [python, "-u", str(self.inference.algorithm_root / "video_reconstruction_job.py"),
            "--job-dir", str(self.inference.runtime_root / job_id), "--out", str(out), "--max-frames", str(job["maxFrames"])]
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="utf-8", OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
        self.update(job_id, status="running", startedAt=now_iso(), progress={"phase": "检查本机几何重建环境", "percent": 2})
        process = None
        timer = None
        expired = threading.Event()
        last_error = ""
        log_path = out.parent / (job["runId"] + ".log")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("", "utf-8")
            process = subprocess.Popen(command, cwd=self.inference.algorithm_root, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def timeout():
                expired.set()
                if process.poll() is None:
                    process.kill()
            timer = threading.Timer(900, timeout)
            timer.daemon = True
            timer.start()
            with log_path.open("w", encoding="utf-8") as log:
                for line in process.stdout:
                    log.write(line)
                    if line.startswith("SLZJ_RECONSTRUCTION "):
                        progress = json.loads(line.split(" ", 1)[1])
                        self.update(job_id, progress=progress)
                    elif line.startswith("SLZJ_RECONSTRUCTION_ERROR "):
                        last_error = line.split(" ", 1)[1].strip()
                    elif "ModuleNotFoundError" in line or "ImportError:" in line:
                        last_error = "重建 Python 环境缺少依赖。请在启动平台所用 Python 中安装 pycolmap、scipy、numpy、opencv-python、Pillow；或设置 SLZJ_RECONSTRUCTION_PYTHON 指向已配置环境。" + line.strip()
            code = process.wait()
            if expired.is_set():
                raise RuntimeError("本次建模超过 15 分钟已停止。可减少补抽帧数，使用较短、清晰且重叠充分的视频后重试；检测成果保留。")
            if code != 0:
                raise RuntimeError(last_error or f"三维重建子进程退出（{code}），请查看本次建模日志。")
            result = json.loads((out / "result.json").read_text("utf-8"))
            prefix = f"/api/video-inference/file/{job_id}/reconstruction/{job['runId']}"
            result.update(viewerUrl=prefix + "/web/index.html", jsonUrl=prefix + "/localized_defects.json",
                          qualityUrl=prefix + "/sfm/quality.json", sceneUrl=prefix + "/sfm/scene.json",
                          logUrl=f"/api/video-inference/file/{job_id}/reconstruction/{job['runId']}.log")
            self.update(job_id, status="completed", finishedAt=now_iso(), result=result,
                progress={"phase": "重建与定位完成，可进入本次视频三维复核", "percent": 100,
                    "registeredFrames": result["registeredFrames"], "sparsePoints": result["sparsePoints"], "localizedCandidates": result["localizedCandidates"]})
        except Exception as exc:
            if log_path.is_file():
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n建模失败：" + str(exc) + "\n")
            self.update(job_id, status="failed", finishedAt=now_iso(), error=str(exc),
                progress={"phase": "重建未完成，原检测成果已保留"},
                logUrl=f"/api/video-inference/file/{job_id}/reconstruction/{job['runId']}.log")
        finally:
            if timer:
                timer.cancel()
            if process and process.poll() is None:
                process.kill()
                process.wait()
            if process and process.stdout:
                process.stdout.close()
            if hasattr(self.inference, "workspace"):
                try:
                    self.inference.workspace.refresh(job_id)
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    self.update(job_id, workspaceError="本视频档案同步失败：" + str(exc))
