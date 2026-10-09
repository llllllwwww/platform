"""启动会话与历史工作状态归档。重启只更换会话编号，不删除业务文件。"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import threading
from uuid import uuid4
from video_reconstruction_service import write_atomic


class WorkbenchSessionService:
    def __init__(self, inference):
        self.inference = inference
        self.session_id = "S-" + uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.lock = threading.RLock()

    @property
    def archive_root(self):
        # 与视频运行目录同属 runtime，测试替换 runtime_root 时也保持隔离。
        return self.inference.runtime_root.parent / "workbench_sessions"

    def session(self):
        return {"sessionId": self.session_id, "startedAt": self.started_at,
                "startupMode": "unselected", "preserveVideoArchives": True}

    def _path(self, archive_id):
        if not isinstance(archive_id, str) or not re.fullmatch(r"W-[0-9a-f]{24}", archive_id):
            raise ValueError("历史工作档案编号无效。")
        root = self.archive_root.resolve()
        target = (root / (archive_id + ".json")).resolve()
        if target.parent != root:
            raise ValueError("工作档案必须位于本机归档目录。")
        return target

    @staticmethod
    def _summary(record):
        state = record["state"]
        return {"id": record["id"], "savedAt": record["savedAt"],
                "batchId": state.get("batch", ""), "taskId": state.get("activeTask", ""),
                "radarCount": len(state.get("radars", [])),
                "videoCount": len(state.get("videoJobsById", {})),
                "reason": record.get("reason", "startup")}

    def save(self, fields):
        state = fields.get("state")
        if not isinstance(state, dict) or state.get("schemaVersion") != 2:
            raise ValueError("仅接受平台版本 2 的工作状态。")
        for key in ("tasks", "batches", "defects", "radars"):
            if not isinstance(state.get(key, []), list):
                raise ValueError("工作状态字段格式无效：" + key)
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(text.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("工作状态超过 8 MB，请先单独导出数据。")
        digest = sha256(text.encode("utf-8")).hexdigest()
        archive_id = "W-" + digest[:24]
        with self.lock:
            target = self._path(archive_id)
            if target.is_file():
                record = json.loads(target.read_text("utf-8"))
                if record.get("sha256") != digest:
                    raise ValueError("工作档案校验不一致，原档案已保留。")
            else:
                self.archive_root.mkdir(parents=True, exist_ok=True)
                record = {"schemaVersion": 1, "id": archive_id,
                          "savedAt": datetime.now(timezone.utc).isoformat(),
                          "previousSessionId": str(fields.get("previousSessionId", ""))[:100],
                          "reason": str(fields.get("reason", "startup"))[:100],
                          "sha256": digest, "state": state}
                write_atomic(target, record)
            return self._summary(record)

    def list(self):
        if not self.archive_root.is_dir():
            return []
        records = []
        for path in self.archive_root.glob("W-*.json"):
            try:
                record = self.read(path.stem)
                records.append(self._summary(record))
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return sorted(records, key=lambda record: record["savedAt"], reverse=True)

    def read(self, archive_id):
        path = self._path(archive_id)
        if not path.is_file():
            raise ValueError("本机没有这个历史工作档案。")
        record = json.loads(path.read_text("utf-8"))
        if record.get("id") != archive_id or not isinstance(record.get("state"), dict):
            raise ValueError("工作档案格式无效。")
        text = json.dumps(record["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if sha256(text.encode("utf-8")).hexdigest() != record.get("sha256"):
            raise ValueError("历史工作档案校验未通过，未加载。")
        return record
