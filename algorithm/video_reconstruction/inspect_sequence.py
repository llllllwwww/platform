"""Write display previews and bit-depth statistics for the TUM VI sequence."""
import json
from pathlib import Path
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parent
rows=np.genfromtxt(ROOT/'data/tumvi/corridor4/mav0/cam0/data.csv',delimiter=',',comments='#',dtype=str)
times=rows[:,0].astype(np.int64);relative=(times-times[0])/1e9
out=ROOT/'results/tumvi_previews';out.mkdir(parents=True,exist_ok=True)
for desired in [5,25,45,65]:
    i=int(np.argmin(abs(relative-desired)));path=ROOT/'data/tumvi/corridor4/mav0/cam0/data'/rows[i,1]
    image=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
    display=(image/256).clip(0,255).astype(np.uint8) if image.dtype==np.uint16 else image
    cv2.imencode('.png',display)[1].tofile(out/f't{desired}.png')
    print(json.dumps({'time_s':float(relative[i]),'dtype':str(image.dtype),'range':[int(image.min()),int(image.max())],
                      'median':float(np.median(image)),'path':str(out/f't{desired}.png')}),flush=True)
