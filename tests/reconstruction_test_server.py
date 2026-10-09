"""浏览器实测专用：复制既有检测成果到独立测试目录，不重新跑 U-Net。"""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from video_inference_service import VideoInferenceService, LocalWorkBenchHandler

p = argparse.ArgumentParser()
p.add_argument("--qa-dir", required=True)
p.add_argument("--seed", required=True)
a = p.parse_args()
qa = Path(a.qa_dir).resolve()
qa.relative_to((ROOT / "tests" / "output").resolve())
seed = Path(a.seed).resolve()
seed.relative_to((ROOT / "runtime" / "video_inference").resolve())
qa.mkdir(parents=True, exist_ok=True)
runtime = qa / "runtime"
job_id = "qa-reconstruction-" + qa.name.split("-")[-1]
directory = runtime / job_id
directory.mkdir(parents=True)
for name in ("input", "frames", "detect"):
    shutil.copytree(seed / name, directory / name)
shutil.copy2(seed / "review.json", directory / "review.json")
review = json.loads((directory / "review.json").read_text("utf-8"))
# 测试服务把种子检测成果复制到新的 videoId；同步改写来源编号和文件 URL，
# 以便通过当前视频档案的同源校验，而不改变真实运行目录中的用户成果。
review["jobId"] = job_id
for frame in review.get("frames", []):
    for key in ("url", "frameUrl", "maskUrl", "overlayUrl"):
        if isinstance(frame.get(key), str):
            frame[key] = frame[key].replace(seed.name, job_id)
for candidate in review.get("candidates", []):
    for key in ("url", "frameUrl", "maskUrl", "overlayUrl"):
        if isinstance(candidate.get(key), str):
            candidate[key] = candidate[key].replace(seed.name, job_id)
(directory / "review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
# detections.json 也带有视频任务标识；复制到新的隔离任务时同步改写，
# 否则重建校验会把测试种子误判为另一条视频的检测结果。
detection_path = directory / "detect" / "detections.json"
if detection_path.is_file():
    detection = json.loads(detection_path.read_text("utf-8"))
    detection["scene_id"] = job_id
    detection_path.write_text(json.dumps(detection, ensure_ascii=False), encoding="utf-8")
manifest = {"id": job_id, "status": "completed", "filename": review["source"]["filename"], "batchId": "B202609", "taskId": "T-001",
    "progress": {"phase": "检测已完成", "percent": 100}, "detector": review.get("detector", {}),
    "result": {"candidateCount": len(review["candidates"]), "frameCount": len(review["frames"]),
        "preview": [{k: (v.replace(seed.name, job_id) if isinstance(v, str) and v.startswith("/api/") else v) for k, v in item.items()} for item in review["candidates"][:18]], "jsonUrl": f"/api/video-inference/file/{job_id}/review.json"}, "log": []}
(directory / "job.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
service = VideoInferenceService(ROOT)
service.runtime_root = runtime
handler = partial(LocalWorkBenchHandler, directory=str(ROOT), service=service)
server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
print("READY " + json.dumps({"port": server.server_port, "jobId": job_id, "qaDir": str(qa)}), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    service.pool.shutdown(wait=False, cancel_futures=True)
