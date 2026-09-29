"""Download an explicit HTTPS URL with SHA-256 provenance and no code execution."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('output')
    args = parser.parse_args()
    if not args.url.startswith('https://'):
        raise ValueError('HTTPS required')
    out = Path(args.output)
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(args.url, headers={'User-Agent': 'tunnel-research/0.1'})
    with urllib.request.urlopen(request, timeout=45) as response, out.with_suffix(out.suffix+'.part').open('wb') as f:
        final_url = response.url
        while chunk := response.read(1024*1024):
            digest.update(chunk)
            size += len(chunk)
            f.write(chunk)
    out.with_suffix(out.suffix+'.part').replace(out)
    out.with_suffix(out.suffix+'.source.json').write_text(json.dumps({'url': args.url, 'final_url': final_url, 'sha256': digest.hexdigest(), 'bytes': size}, indent=2), 'utf-8')
    print(json.dumps({'path': str(out), 'bytes': size}, ensure_ascii=False))

if __name__ == '__main__':
    main()
