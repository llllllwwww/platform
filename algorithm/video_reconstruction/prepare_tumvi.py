"""Verify the official TUM VI archive and extract cam0 images plus calibration metadata."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import tarfile
ROOT=Path(__file__).resolve().parent

def main():
    archive=ROOT/'data/tumvi/dataset-corridor4_512_16.tar'
    expected=archive.with_suffix('.tar.md5').read_text('utf-8').split()[0]
    md5=hashlib.md5();sha=hashlib.sha256()
    with archive.open('rb') as stream:
        while block:=stream.read(8*1024*1024):md5.update(block);sha.update(block)
    if md5.hexdigest()!=expected:raise ValueError('Official archive checksum mismatch')
    destination=ROOT/'data/tumvi/corridor4';destination.mkdir(parents=True,exist_ok=True);metadata=[];images=0
    with tarfile.open(archive,'r:') as tar:
        for member in tar:
            if not member.isfile():continue
            rel=PurePosixPath(member.name);is_image='cam0' in rel.parts and rel.suffix=='.png'
            if not is_image and rel.suffix not in ('.yaml','.csv','.json','.txt'):continue
            path=destination/Path(*rel.parts[1:])
            if not path.resolve().is_relative_to(destination.resolve()):raise ValueError('Unsafe archive path')
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():
                with tar.extractfile(member) as data:path.write_bytes(data.read())
            if is_image:images+=1
            else:metadata.append(path.relative_to(ROOT).as_posix())
    manifest={'dataset':'TUM VI corridor4','source_url':'https://cdn2.vision.in.tum.de/tumvi/exported/euroc/512_16/dataset-corridor4_512_16.tar',
        'license':'CC BY 4.0','camera':'cam0 of calibrated monochrome wide-angle stereo rig; no IMU/stereo fusion used',
        'archive_sha256':sha.hexdigest(),'official_md5':expected,'cam0_images':images,'metadata':metadata}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2),'utf-8');print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
