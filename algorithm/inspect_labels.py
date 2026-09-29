"""Render exact-size development image/annotation pairs to verify label encoding."""
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parent
base=ROOT/'data/ctcd';out=ROOT/'results/ctcd_label_audit';out.mkdir(parents=True,exist_ok=True)
paths=sorted((base/'train').glob('*.bmp'))[:6]
canvas=Image.new('RGB',(512,280*len(paths)),(245,245,245));draw=ImageDraw.Draw(canvas)
for i,path in enumerate(paths):
    image=Image.open(path);label=Image.open(base/'trainannot'/path.name)
    canvas.paste(image.convert('RGB'),(0,i*280));canvas.paste(label.convert('RGB'),(256,i*280));draw.text((5,i*280+256),path.name,fill=(0,0,0))
    values=np.asarray(label);print(json.dumps({'name':path.name,'image_mode':image.mode,'mask_mode':label.mode,'mask_values':np.unique(values).tolist()[:24],
        'foreground_fraction_gt0':float((values>0).mean()),'foreground_fraction_gt127':float((values>127).mean()),
        'mask_identical_to_image':bool(np.array_equal(np.asarray(image),values))}),flush=True)
canvas.save(out/'development_pairs.png')
for path in sorted((base/'train').glob('*.bmp')):
    label=Image.open(base/'trainannot'/path.name);values=np.asarray(label.convert('L'))
    if not set(np.unique(values))<={0,1,255}:
        image=Image.open(path);pair=Image.new('RGB',(512,256));pair.paste(image.convert('RGB'),(0,0));pair.paste(label.convert('RGB'),(256,0));pair.save(out/('nonbinary_'+path.stem+'.png'))
        print(json.dumps({'nonbinary_annotation':path.name,'unique_grayscale_values':len(np.unique(values)),
                          'fraction_intermediate':float(((values>1)&(values<255)).mean()),'fraction_gt127':float((values>127).mean())}),flush=True)
