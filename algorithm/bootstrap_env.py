"""Install pinned official wheels in the local Python 3.11 environment without pip subprocesses."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
PACKAGES = {
    'numpy': ('2.2.6', 'cp311-cp311-win_amd64.whl'),
    'pycolmap': ('3.13.0', 'cp311-cp311-win_amd64.whl'),
    'opencv-python-headless': ('4.11.0.86', 'cp37-abi3-win_amd64.whl'),
    'scipy': ('1.15.3', 'cp311-cp311-win_amd64.whl'),
    'pillow': ('11.3.0', 'cp311-cp311-win_amd64.whl'),
}

def main():
    destination = ROOT / '.venv' / 'Lib' / 'site-packages'
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (version, suffix) in PACKAGES.items():
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=25) as r:
            metadata = json.load(r)
        candidates = [u for u in metadata['urls'] if u['filename'].endswith(suffix)]
        if len(candidates) != 1:
            raise RuntimeError(f'Expected exactly one official wheel: {name}, found {len(candidates)}')
        wheel = candidates[0]
        print('Downloading', wheel['filename'], flush=True)
        with urllib.request.urlopen(wheel['url'], timeout=40) as r:
            data = r.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != wheel['digests']['sha256']:
            raise ValueError('Wheel digest mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                path = destination / info.filename
                if not path.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Invalid wheel path')
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(info))
        manifest[name] = {'version': version, 'wheel': wheel['filename'], 'url': wheel['url'], 'sha256': digest}
        print('Installed', name, flush=True)
    (ROOT / 'environment.json').write_text(json.dumps(manifest, indent=2), 'utf-8')

if __name__ == '__main__':
    main()
