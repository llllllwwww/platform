"""验证服务启动会话和历史工作状态的非破坏性保存，不运行推理。"""
import functools
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from video_inference_service import VideoInferenceService, LocalWorkBenchHandler


class WorkbenchSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = VideoInferenceService(self.root)
        self.state = {"schemaVersion": 2, "project": {"name": "原项目"}, "batch": "B1", "activeTask": "T1",
                      "tasks": [{"id": "T1", "status": "paused", "progress": .7}], "batches": [{"id": "B1"}],
                      "defects": [], "radars": [{"id": "radar1", "matrix": [[1, 2], [3, 4]]}],
                      "videoJobsById": {"video1": {"id": "video1", "status": "completed"}},
                      "videoSelectionByContext": {"B1::T1": {"mode": "video", "videoId": "video1"}}, "alpha": .9}

    def tearDown(self):
        self.service.pool.shutdown(wait=False)
        self.temp.cleanup()

    def test_restart_has_new_identity_and_keeps_existing_files(self):
        original = self.service.runtime_root / "original" / "detect" / "result.json"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original-result")
        initial = self.service.workbench.session()
        restored = VideoInferenceService(self.root)
        try:
            self.assertNotEqual(restored.workbench.session()["sessionId"], initial["sessionId"])
            self.assertEqual(restored.workbench.session()["startupMode"], "unselected")
            self.assertEqual(original.read_bytes(), b"original-result")
            self.assertFalse(restored.workbench.archive_root.exists())
        finally:
            restored.pool.shutdown(wait=False)

    def test_complete_browser_state_is_archived_and_restored(self):
        summary = self.service.workbench.save({"state": self.state})
        self.assertEqual(summary["radarCount"], 1)
        self.assertEqual(summary["videoCount"], 1)
        self.assertEqual(self.service.workbench.read(summary["id"])["state"], self.state)

    def test_archives_survive_service_restart(self):
        summary = self.service.workbench.save({"state": self.state})
        restored = VideoInferenceService(self.root)
        try:
            self.assertEqual(restored.workbench.list()[0]["id"], summary["id"])
            self.assertEqual(restored.workbench.read(summary["id"])["state"]["radars"][0]["matrix"], [[1, 2], [3, 4]])
        finally:
            restored.pool.shutdown(wait=False)

    def test_identical_state_deduplicates_without_overwriting(self):
        first = self.service.workbench.save({"state": self.state})
        path = self.service.workbench._path(first["id"])
        before = path.stat().st_mtime_ns
        second = self.service.workbench.save({"state": self.state})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(len(self.service.workbench.list()), 1)

    def test_invalid_id_and_modified_archive_rejected(self):
        for value in ["../outside", "W-abc", "", None]:
            with self.assertRaises(ValueError):
                self.service.workbench.read(value)
        summary = self.service.workbench.save({"state": self.state})
        path = self.service.workbench._path(summary["id"])
        record = json.loads(path.read_text("utf-8"))
        record["state"]["alpha"] = 100
        path.write_text(json.dumps(record), "utf-8")
        with self.assertRaises(ValueError):
            self.service.workbench.read(summary["id"])

    def test_http_session_and_manual_archive_endpoints(self):
        handler = functools.partial(LocalWorkBenchHandler, directory=str(self.root), service=self.service)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def request(url, data=None):
            payload = None if data is None else json.dumps(data).encode()
            with urlopen(Request(base + url, payload, headers={"Content-Type": "application/json"}), timeout=5) as response:
                return json.loads(response.read())
        try:
            info = request("/api/workbench/session")
            self.assertEqual(info["sessionId"], self.service.workbench.session_id)
            self.assertEqual(request("/api/workbench/archives")["archives"], [])
            saved = request("/api/workbench/archive", {"state": self.state})
            self.assertEqual(request("/api/workbench/archives")["archives"][0]["id"], saved["id"])
            self.assertEqual(request("/api/workbench/archive?id=" + saved["id"])["state"], self.state)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
