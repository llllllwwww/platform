"""下载 3 个公开混凝土检测验证视频，仅用于本地对应性测试，不当作隧道测量真值。"""
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib, json, uuid

ROOT = Path(__file__).resolve().parent / "output" / "public-videos"
SOURCES = [
    {"file": "cracks.mp4", "url": "https://raw.githubusercontent.com/ileocho/CrackSeg/main/cracks.mp4", "source": "CrackSeg 公开混凝土裂缝示例；仓库 GPL-3.0，素材单独授权未注明", "sha256": "f5757be0caefb6cc559dabf624e994eca2a48323fc3253d64ef310deebc82269"},
    {"file": "concrete-damages-S4.mp4", "url": "https://ars.els-cdn.com/content/image/1-s2.0-S2589004224005583-mmc5.mp4", "source": "iScience 2024 109337 补充 Video S4；CC BY-NC-ND 4.0", "sha256": "832b7a2ef39e34aaf579db5f70fb43020b8de471ca479c7ffa423883020ec1b4"},
    {"file": "recognition-comparison-S5.mp4", "url": "https://ars.els-cdn.com/content/image/1-s2.0-S2589004224005583-mmc6.mp4", "source": "iScience 2024 109337 补充 Video S5；CC BY-NC-ND 4.0", "sha256": "5ab72eef15d7826c13285bb4b7060d8cc1c252e06542730e53c441293f6d867c"},
]

def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            sha.update(chunk)
    return sha.hexdigest()

def download(item):
    target = ROOT / item["file"]
    if not target.is_file() or digest(target) != item["sha256"]:
        temporary = ROOT / (item["file"] + "." + uuid.uuid4().hex + ".part")
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open(Request(item["url"], headers={"User-Agent": "platform-local-video-validation"}), timeout=45) as source, temporary.open("wb") as output:
                size = 0
                while True:
                    chunk = source.read(1048576)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise ValueError("公开视频超过 200 MB，停止下载")
                    output.write(chunk)
            if digest(temporary) != item["sha256"]:
                raise ValueError("文件校验与记录不一致，请核查源端更新")
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
    result = dict(item, bytes=target.stat().st_size, boundary="公开混凝土示例；无隧道里程 / 环位真值；人工候选标注仅用于顺序与位置对应验证。")
    print("OK " + item["file"] + " " + str(result["bytes"]) + " bytes", flush=True)
    return result

if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(download, item) for item in SOURCES]):
            results.append(future.result())
    (ROOT / "sources.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "README.md").write_text("# 本地公开视频验证素材\n\n三个原始视频与来源 / SHA-256 记录用于现场标注、顺序、相对位置和孪生联动测试。均不是本项目现场隧道测量资料；原视频内的模型提示只作为人工候选参考。文件保留在 Git 忽略目录，不随平台发布。\n\n重新验证：先启动平台，再运行 npm run test:evidence。\n", encoding="utf-8")
