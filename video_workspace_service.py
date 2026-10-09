"""一个视频一份档案：同源成果索引、派生状态和复核记录均落在视频目录。

已有 input/frames/detect/reconstruction 路径保持兼容。每次导入有独立 videoId，
文件内容的 SHA-256 用于核验来源，文件名不能充当视频身份。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from video_reconstruction_service import now_iso, write_atomic


class VideoWorkspaceService:
    def __init__(self, inference):
        self.inference = inference

    def directory(self, video_id):
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", video_id):
            raise ValueError("视频档案编号无效。")
        base = self.inference.runtime_root.resolve()
        target = (base / video_id).resolve()
        if target.parent != base:
            raise ValueError("视频档案路径越界。")
        return target

    @staticmethod
    def read(path, default=None):
        if not path.is_file():
            return default
        return json.loads(path.read_text("utf-8"))

    @staticmethod
    def digest(path):
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    @staticmethod
    def put(path, value):
        # 状态未变化时不重写磁盘，也不让前端相同轮询产生新的时间戳。
        path.parent.mkdir(parents=True, exist_ok=True)
        old = VideoWorkspaceService.read(path)
        if old == value:
            return value
        write_atomic(path, value)
        return value

    def source(self, job, verify=False):
        directory = self.directory(job["id"])
        path = directory / "source.json"
        source = self.read(path, {})
        original = directory / "input" / Path(job["filename"]).name
        result = {"videoId": job["id"], "filename": job["filename"],
                  "path": "input/" + original.name, "sha256": source.get("sha256"),
                  "bytes": source.get("bytes"), "evidenceSourceId": source.get("evidenceSourceId"), "available": original.is_file(),
                  "identityBasis": "SHA-256 整文件校验" if source.get("sha256") else "独立导入编号，原始文件待核验"}
        if original.is_file():
            stat = original.stat()
            changed = source.get("mtimeNs") != stat.st_mtime_ns or source.get("bytes") != stat.st_size
            if verify and (changed or not result["sha256"]):
                observed = self.digest(original)
                if result["sha256"] and observed != result["sha256"]:
                    result.update(sourceMismatch=True, observedSha256=observed)
                else:
                    evidence_hash = observed
                    if stat.st_size > 128 * 1024 * 1024:
                        with original.open("rb") as stream:
                            head = stream.read(1048576)
                            stream.seek(-1048576, 2)
                            evidence_hash = hashlib.sha256(str(stat.st_size).encode() + head + stream.read(1048576)).hexdigest()
                    result.update(sha256=observed, bytes=stat.st_size, evidenceSourceId="VF-" + evidence_hash, identityBasis="SHA-256 整文件校验")
                    self.put(path, {**result, "mtimeNs": stat.st_mtime_ns})
            elif result["bytes"] is None:
                result["bytes"] = stat.st_size
        if result.get("sha256") and not result.get("evidenceSourceId") and (result.get("bytes") or 0) <= 128 * 1024 * 1024:
            result["evidenceSourceId"] = "VF-" + result["sha256"]
        return result

    def initialize(self, job, file_bytes):
        directory = self.directory(job["id"])
        original = directory / "input" / Path(job["filename"]).name
        whole_hash = hashlib.sha256(file_bytes).hexdigest()
        sampled = len(file_bytes) > 128 * 1024 * 1024
        evidence_hash = hashlib.sha256(str(len(file_bytes)).encode() + file_bytes[:1048576] + file_bytes[-1048576:]).hexdigest() if sampled else whole_hash
        self.put(directory / "source.json", {"videoId": job["id"], "filename": job["filename"],
            "path": "input/" + original.name, "sha256": hashlib.sha256(file_bytes).hexdigest(),
            "bytes": len(file_bytes), "evidenceSourceId": "VF-" + evidence_hash, "mtimeNs": original.stat().st_mtime_ns,
            "available": True, "identityBasis": "SHA-256 整文件校验"})
        return self.refresh(job["id"])

    def _detection(self, job, source):
        directory = self.directory(job["id"])
        review_path, detection_path = directory / "review.json", directory / "detect" / "detections.json"
        stage = {"status": job["status"], "revision": None, "frameCount": 0, "candidateCount": 0,
                 "reviewPath": "review.json", "path": "detect/detections.json"}
        if source.get("sourceMismatch"):
            raise ValueError("视频原始文件已被替换，已隔离旧检测与下游数据；请重新导入检测。")
        if job["status"] != "completed":
            return stage, None
        review = self.read(review_path)
        detection = self.read(detection_path)
        if review is None or detection is None:
            raise ValueError("当前视频的检测文件缺失，不能展示其他视频的检测成果。")
        if review.get("jobId") != job["id"]:
            raise ValueError("review.json 的视频编号不属于当前视频。")
        for record in (review, detection):
            owner = record.get("videoId") or record.get("sourceJobId")
            fingerprint = record.get("sourceSha256")
            if owner and owner != job["id"]:
                raise ValueError("检测成果来源编号与当前视频不一致。")
            if fingerprint and source.get("sha256") and fingerprint != source["sha256"]:
                raise ValueError("原视频校验值与检测成果不一致，请重新检测。")
        frames = {x["name"] for x in review.get("frames", [])}
        ids = [x.get("id") for x in review.get("candidates", [])]
        if len(set(ids)) != len(ids) or None in ids:
            raise ValueError("候选编号缺失或重复。")
        native_rows = detection.get("detections", [])
        native = {row.get("id"): row for row in native_rows}
        if len(native) != len(native_rows) or set(ids) != set(native):
            raise ValueError("候选复核清单与本视频检测文件不一致。")
        prefix = f"/api/video-inference/file/{job['id']}/"
        for row in review.get("candidates", []):
            if row.get("imageName") not in frames or native[row["id"]].get("image_name") != row.get("imageName"):
                raise ValueError("候选引用了非本视频采样帧。")
            for key in ("imageUrl", "frameUrl", "maskUrl", "overlayUrl"):
                if row.get(key) and not row[key].startswith(prefix):
                    raise ValueError("候选图像地址引用了其他视频。")
        revision = hashlib.sha256(review_path.read_bytes() + detection_path.read_bytes()).hexdigest()
        stage.update(revision=revision, frameCount=len(frames), candidateCount=len(ids))
        return stage, review

    def _reconstruction(self, job, detection, source):
        directory = self.directory(job["id"])
        record = self.inference.reconstruction.get(job["id"])
        stage = {"status": record.get("status", "not_started") if record else "not_started", "runId": None,
                 "path": "reconstruction/", "result": None}
        if not record:
            return stage, None
        run_id = record.get("runId")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
            raise ValueError("三维重建运行编号无效。")
        if record.get("id") != job["id"] or record.get("sourceJobId") != job["id"]:
            raise ValueError("三维重建来源不属于当前视频。")
        if record.get("batchId") != job.get("batchId") or record.get("taskId") != job.get("taskId"):
            raise ValueError("三维重建与当前视频的任务归属不一致。")
        if record.get("sourceSha256") and source.get("sha256") and record["sourceSha256"] != source["sha256"]:
            raise ValueError("三维重建任务的视频校验值已失效。")
        if record.get("sourceDetectionRevision") and record["sourceDetectionRevision"] != detection["revision"]:
            raise ValueError("检测成果已改变，旧三维成果不能继续用于本次评估。")
        stage.update(runId=run_id, path=f"reconstruction/{run_id}/")
        if record.get("status") != "completed":
            return stage, record
        base = directory / "reconstruction" / run_id
        result, scene = self.read(base / "result.json"), self.read(base / "sfm" / "scene.json")
        localized = self.read(base / "localized_defects.json")
        if not result or not scene or not localized or not (base / "web" / "index.html").is_file():
            raise ValueError("本视频三维成果不完整，当前窗口不加载旧模型。")
        if result.get("sourceJobId") and result["sourceJobId"] != job["id"]:
            raise ValueError("三维成果文件声明了其他视频来源。")
        if result.get("sourceSha256") and source.get("sha256") and result["sourceSha256"] != source["sha256"]:
            raise ValueError("三维成果的视频校验值不一致。")
        if result.get("sceneId") != scene.get("scene_id") or localized.get("scene_id") != scene.get("scene_id"):
            raise ValueError("模型与定位成果的场景编号不一致。")
        native = self.read(directory / "detect" / "detections.json", {})
        ids = {x["id"] for x in native.get("detections", [])}
        if len(localized.get("defects", [])) != len(ids) or {x.get("id") for x in localized.get("defects", [])} != ids:
            raise ValueError("三维定位候选并非本视频检测清单。")
        prefix = f"/api/video-inference/file/{job['id']}/reconstruction/{run_id}"
        urls = {"viewerUrl": prefix + "/web/index.html", "jsonUrl": prefix + "/localized_defects.json",
                "qualityUrl": prefix + "/sfm/quality.json", "sceneUrl": prefix + "/sfm/scene.json"}
        stage["result"] = {**result, **urls}
        record = {**record, "result": stage["result"]}
        return stage, record

    def refresh(self, video_id, verify_source=False):
        with self.inference.lock:
            job = self.inference.get(video_id)
            if not job:
                raise ValueError("本机不存在这个视频档案。")
            directory = self.directory(video_id)
            source = self.source(job, verify_source)
            context = {"batchId": job.get("batchId", ""), "taskId": job.get("taskId", ""), "taskName": job.get("taskName", "")}
            errors = []
            review, rebuilt = None, None
            try:
                detection, review = self._detection(job, source)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(str(exc))
                detection = {"status": "invalid", "revision": None, "frameCount": 0, "candidateCount": 0, "path": "detect/detections.json", "reviewPath": "review.json"}
            try:
                if detection["status"] == "invalid":
                    raise ValueError("检测来源校验未通过，已隔离下游成果。")
                reconstruction, rebuilt = self._reconstruction(job, detection, source)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(str(exc))
                reconstruction = {"status": "invalid", "runId": None, "path": "reconstruction/", "result": None}
            dependency = {"detectionRevision": detection["revision"], "reconstructionRunId": reconstruction["runId"]}
            common = {"schemaVersion": 1, "videoId": video_id, "sourceSha256": source.get("sha256"), "context": context, "dependency": dependency}
            annotations = self.read(directory / "acquisition" / "annotations.json", {**common, "records": [], "status": "not_marked"})
            if annotations.get("videoId") != video_id or annotations.get("sourceSha256") != source.get("sha256"):
                annotations = {**common, "records": [], "status": "not_marked"}
            decisions = self.read(directory / "review" / "decisions.json", {**common, "decisions": {}, "notes": ""})
            if decisions.get("videoId") != video_id or decisions.get("dependency", {}).get("detectionRevision") != detection["revision"]:
                # 原复核文件保留，但改变后的检测不继承旧决定。
                decisions = {**common, "decisions": {}, "notes": "", "status": "awaiting_review"}
            ids = {x["id"] for x in (review or {}).get("candidates", [])}
            decisions = {**decisions, **common}
            annotations = {**annotations, **common}
            decisions["decisions"] = {key: value for key, value in decisions.get("decisions", {}).items() if key in ids}
            ready = reconstruction["status"] == "completed" and reconstruction["result"] is not None
            twin = {**common, "status": "ready" if ready else "awaiting_reconstruction", "sceneId": None,
                    "coordinateSpace": "未生成", "modelUrl": None, "candidateCount": detection["candidateCount"], "localizedCandidates": 0}
            if ready:
                result = reconstruction["result"]
                twin.update(sceneId=result["sceneId"], coordinateSpace="局部 SfM 坐标，尺度未标定", modelUrl=result["viewerUrl"],
                            localizedCandidates=result.get("localizedCandidates", 0), geometryKind=result.get("geometryKind"),
                            localizationUrl=result["jsonUrl"], qualityUrl=result["qualityUrl"])
            health = {**common, "status": "awaiting_measurements" if ready else "awaiting_reconstruction",
                      "shi": None, "risk": None, "measurements": [], "basis": "本视频候选与局部三维定位",
                      "missing": ["经校核的物理尺度与隧道坐标标定", "已确认病害类型和实测尺寸", "经审定的评估参数"],
                      "message": "候选数量、像素面积和任意尺度坐标不能直接生成工程 SHI。"}
            operations = {**common, "status": "awaiting_assessment", "alerts": [], "maintenance": []}
            simulation = {**common, "status": "not_started", "result": None, "message": "本视频尚无经验证的仿真输入和求解成果。"}
            report = {**common, "filename": job["filename"], "source": source, "status": "processing_record",
                      "detection": detection, "reconstruction": reconstruction, "twin": twin, "health": health,
                      "operations": operations, "simulation": simulation, "review": decisions, "acquisition": annotations, "errors": errors,
                      "limitations": ["自动检测输出为裂缝候选，人工复核不等于工程确诊。", "局部三维定位无物理尺度，不自动套用演示健康分。"]}
            files = {"acquisition": ("acquisition/annotations.json", annotations), "review": ("review/decisions.json", decisions), "twin": ("twin/state.json", twin),
                     "health": ("health/assessment.json", health), "operations": ("operations/state.json", operations),
                     "simulation": ("simulation/state.json", simulation), "report": ("reports/report.json", report)}
            for relative, value in files.values():
                self.put(directory / relative, value)
            stages = {"detection": detection, "reconstruction": reconstruction,
                      **{name: {"status": value.get("status", "awaiting_review"), "path": relative} for name, (relative, value) in files.items()}}
            manifest = {"schemaVersion": 1, "videoId": video_id, "filename": job["filename"], "context": context,
                        "source": source, "createdAt": job.get("createdAt") or (review or {}).get("createdAt"),
                        "directory": "runtime/video_inference/" + video_id, "stages": stages, "errors": errors,
                        "cleanup": {"status": job.get("cleanupStatus", "not_needed"), "deletedVideoIds": job.get("deletedVideoIds", []), "error": job.get("cleanupError")}}
            self.put(directory / "manifest.json", manifest)
            # 同目录 HTML 是可追溯的处理记录，内嵌数据，不引用其他视频或全局演示台账。
            from html import escape
            body = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>视频处理档案</title><style>body{background:#0b1018;color:#e8f0f7;font:15px 'Microsoft YaHei',sans-serif;max-width:1000px;margin:40px auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#131b27;padding:24px;border:1px solid #25473f;border-radius:14px}h1{color:#56d9b1}</style><h1>" + escape(job["filename"]) + " · 视频处理档案</h1><p>视频编号：" + escape(video_id) + "。此报告仅使用本视频成果；健康分未满足工程输入条件时保持空值。</p><pre>" + escape(json.dumps(report, ensure_ascii=False, indent=2)) + "</pre></html>"
            html_path = directory / "reports" / "report.html"
            if not html_path.is_file() or html_path.read_text("utf-8") != body:
                html_path.write_text(body, "utf-8")
            safe_job = dict(job)
            if errors:
                if detection["status"] == "invalid":
                    safe_job.update(status="failed", result=None, error="；".join(errors))
                rebuilt = None
            return {"manifest": manifest, "inference": safe_job, "reconstruction": rebuilt,
                    "review": decisions, "twin": twin, "health": health, "operations": operations,
                    "simulation": simulation, "report": report, "acquisition": annotations, "candidates": (review or {}).get("candidates", [])}

    def list(self):
        records = []
        with self.inference.lock:
            for directory in sorted(self.inference.runtime_root.iterdir(), reverse=True):
                if not directory.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", directory.name):
                    continue
                try:
                    job = self.inference.get(directory.name)
                    if not job:
                        # 保留无法确认归属的旧中断目录，明确标识，不冒充已完成成果。
                        videos = sorted((directory / "input").glob("*")) if (directory / "input").is_dir() else []
                        if videos:
                            records.append({"videoId": directory.name, "filename": videos[0].name, "context": {"batchId": "", "taskId": ""}, "status": "unindexed", "selectable": False})
                        continue
                    records.append({"videoId": job["id"], "filename": job["filename"],
                                    "context": {"batchId": job.get("batchId", ""), "taskId": job.get("taskId", "")},
                                    "status": job["status"], "createdAt": job.get("createdAt"), "selectable": True})
                except (ValueError, KeyError, OSError) as exc:
                    records.append({"videoId": directory.name, "filename": directory.name, "status": "invalid", "selectable": False, "error": str(exc), "context": {}})
        return records

    def bind(self, fields):
        video_id = fields.get("videoId")
        self.directory(video_id)
        with self.inference.lock:
            job = self.inference.get(video_id)
            if not job:
                raise ValueError("无法关联这个视频档案。")
            for key in ("batchId", "taskId"):
                if not isinstance(fields.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", fields[key]):
                    raise ValueError("需要明确的批次和任务编号。")
                if job.get(key) and job[key] != fields[key]:
                    raise ValueError("已归属其他批次或任务的视频不能重新绑定。")
            internal = self.inference.jobs[video_id]
            internal.update(batchId=fields["batchId"], taskId=fields["taskId"], recoveredContext=True)
            self.inference.persist(video_id)
            return self.refresh(video_id, verify_source=True)

    def save_review(self, fields):
        video_id = fields.get("videoId")
        self.directory(video_id)
        with self.inference.lock:
            bundle = self.refresh(video_id, verify_source=True)
            manifest = bundle["manifest"]
            if manifest["errors"] or manifest["stages"]["detection"]["status"] != "completed":
                raise ValueError("需先完成本视频检测并通过来源核验。")
            if fields.get("batchId") != manifest["context"]["batchId"] or fields.get("taskId") != manifest["context"]["taskId"]:
                raise ValueError("复核请求不属于本视频的批次和任务。")
            if fields.get("sourceSha256") != manifest["source"]["sha256"] or fields.get("detectionRevision") != manifest["stages"]["detection"]["revision"]:
                raise ValueError("视频或检测版本已改变，请重新加载当前视频后再保存。")
            decisions = fields.get("decisions", {})
            if not isinstance(decisions, dict) or len(decisions) > 10000:
                raise ValueError("复核决定必须是候选编号字典。")
            native = self.read(self.directory(video_id) / "review.json", {})
            ids = {x["id"] for x in native.get("candidates", [])}
            for key, value in decisions.items():
                if key not in ids or value not in {"pending", "confirmed_candidate", "rejected"}:
                    raise ValueError("复核决定包含非本视频候选或未知状态。")
            notes = fields.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 4000:
                raise ValueError("视频备注最多 4000 字。")
            record = {**bundle["review"], "decisions": decisions, "notes": notes, "updatedAt": now_iso(), "status": "reviewed" if decisions else "awaiting_review"}
            self.put(self.directory(video_id) / "review" / "decisions.json", record)
            return self.refresh(video_id)

    def media_path(self, video_id):
        directory = self.directory(video_id)
        job = self.inference.get(video_id)
        if not job:
            raise ValueError("没有找到本视频原始录像。")
        target = (directory / "input" / Path(job["filename"]).name).resolve()
        if target.parent != (directory / "input").resolve() or not target.is_file():
            raise ValueError("本视频原始录像不存在。")
        return target

    def save_annotations(self, fields):
        video_id = fields.get("videoId")
        self.directory(video_id)
        with self.inference.lock:
            bundle = self.refresh(video_id, verify_source=True)
            manifest = bundle["manifest"]
            if manifest["errors"] or fields.get("sourceSha256") != manifest["source"]["sha256"]:
                raise ValueError("视频来源校验未通过，不能保存采集标注。")
            if fields.get("batchId") != manifest["context"]["batchId"] or fields.get("taskId") != manifest["context"]["taskId"]:
                raise ValueError("标注请求不属于本视频任务。")
            records = fields.get("records", [])
            if not isinstance(records, list) or len(records) > 10000:
                raise ValueError("标注必须为有限的记录列表。")
            seen = set()
            for record in records:
                if not isinstance(record, dict) or not record.get("id") or record["id"] in seen:
                    raise ValueError("标注编号缺失或重复。")
                seen.add(record["id"])
                if record.get("batchId") != fields["batchId"] or record.get("taskId") != fields["taskId"]:
                    raise ValueError("标注包含其他任务记录。")
                anchors = record.get("anchors", [])
                if not anchors or any(a.get("kind") != "video" or a.get("sourceId") != manifest["source"]["evidenceSourceId"] for a in anchors):
                    raise ValueError("标注关联了其他视频来源。")
            value = {**bundle["acquisition"], "records": records, "status": "marked" if records else "not_marked"}
            self.put(self.directory(video_id) / "acquisition" / "annotations.json", value)
            return self.refresh(video_id)
