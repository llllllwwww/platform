"""隧雷智检本地启动器：仅监听回环地址，不接收互联网连接，不上传数据。"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import threading
import webbrowser

parser = argparse.ArgumentParser(description='启动隧雷智检本地工作台')
parser.add_argument('--port', type=int, default=8765)
parser.add_argument('--no-browser', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parent
handler = partial(SimpleHTTPRequestHandler, directory=str(root))
try:
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
except OSError as exc:
    print(f'Port {args.port} unavailable: {exc}. Try --port 8766.')
    raise SystemExit(1)
url = f'http://127.0.0.1:{args.port}/index.html'
print(f'Tunnel workbench: {url}\nLocal only. Ctrl+C to stop.', flush=True)
if not args.no_browser:
    threading.Timer(.5, lambda: webbrowser.open(url)).start()
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
