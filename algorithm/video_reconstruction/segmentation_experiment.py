"""CTCD domain adaptation with untouched official holdout and validation-only model selection.

The external model architecture/initial checkpoint retain their upstream terms. This script
provides experiment code; it does not redistribute that architecture, dataset or weights.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time
import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset,DataLoader

ROOT=Path(__file__).resolve().parent
THRESHOLDS=[.1,.15,.2,.25,.3,.4,.5,.6,.7,.8,.85,.9]
MEAN=torch.tensor([.485,.456,.406])[None,:,None,None]
STD=torch.tensor([.229,.224,.225])[None,:,None,None]


def pairs(folder,split):
    images=folder/split;masks=folder/(split+'annot');result=[]
    for path in sorted(images.glob('*.bmp')):
        mask=masks/path.name
        if not mask.is_file():raise ValueError(f'Missing label: {mask}')
        image=np.array(Image.open(path).convert('RGB'));target=np.array(Image.open(mask).convert('L'))
        if image.shape[:2]!=target.shape:raise ValueError('Image/label dimensions differ')
        values=set(np.unique(target).tolist())
        intermediate=float(((target>1)&(target<255)).mean())
        if intermediate>.05:raise ValueError(f'Annotation is not a mostly binary mask: {mask.name}')
        # Development audit found three antialiased annotations; use a fixed midpoint,
        # rather than promoting faint interpolation halos to full crack pixels.
        decoded=target>0 if values<={0,1} else target>=128
        result.append({'id':path.stem,'image':image,'mask':decoded.astype(np.float32),
                       'sha256':hashlib.sha256(image.tobytes()).hexdigest()})
    return result


class Samples(Dataset):
    def __init__(self,rows,augment=False):self.rows=rows;self.augment=augment
    def __len__(self):return len(self.rows)
    def __getitem__(self,index):
        row=self.rows[index];x=torch.from_numpy(row['image'].copy()).permute(2,0,1).float()/255;y=torch.from_numpy(row['mask'].copy())[None]
        if self.augment:
            k=random.randrange(4);x=torch.rot90(x,k,(-2,-1));y=torch.rot90(y,k,(-2,-1))
            if random.random()<.5:x=x.flip(-1);y=y.flip(-1)
            if random.random()<.7:
                x=(x*random.uniform(.75,1.25)+random.uniform(-.06,.06)).clamp(0,1)
            if random.random()<.2:x=F.avg_pool2d(x[None],3,1,1)[0]
        return x,y


def metric(tp,fp,fn):
    precision=tp/max(tp+fp,1);recall=tp/max(tp+fn,1)
    return {'precision':precision,'recall':recall,'f1':2*tp/max(2*tp+fp+fn,1),'iou':tp/max(tp+fp+fn,1),
            'tp':int(tp),'fp':int(fp),'fn':int(fn)}


def metrics_from_probabilities(probabilities,rows,thresholds=THRESHOLDS):
    truth=np.stack([x['mask'] for x in rows])>0;metrics=[]
    for threshold in thresholds:
        pred=probabilities>=threshold
        tp=int((pred&truth).sum());fp=int((pred&~truth).sum());fn=int((~pred&truth).sum())
        result={'threshold':threshold,**metric(tp,fp,fn)}
        per_image=[]
        for p,t in zip(pred,truth):
            m=metric(int((p&t).sum()),int((p&~t).sum()),int((~p&t).sum()));per_image.append(m['f1'])
        result['macro_f1']=float(np.mean(per_image));metrics.append(result)
    return metrics


@torch.inference_mode()
def predict(model,rows,device,batch=8):
    model.eval();result=[]
    mean=MEAN.to(device);std=STD.to(device)
    for x,_ in DataLoader(Samples(rows),batch_size=batch,shuffle=False,num_workers=0):
        x=(x.to(device)-mean)/std
        with torch.autocast(device_type=device.type,enabled=device.type=='cuda'):
            logits=model(x)
        result.append(logits.float().sigmoid()[:,0].cpu().numpy())
    return np.concatenate(result)


def load_model(repo,checkpoint,device):
    sys.path.insert(0,str(repo.resolve()))
    from src.models.checkpoint import load_unet_checkpoint
    model,features=load_unet_checkpoint(str(checkpoint),device)
    return model


def train(args):
    out=args.out
    if (out/'protocol.json').exists():raise ValueError('Experiment exists; use a new output directory')
    rows=pairs(args.data,'train')
    if len(rows)!=250:raise ValueError(f'Expected 250 official training pairs, found {len(rows)}')
    # Inspect holdout filenames only. Exclude related source-prefix tiles from training;
    # the publisher's train/val folders share some prefixes despite different pixels.
    holdout_groups={p.stem.split('-')[0] for p in (args.data/'val').glob('*.bmp')}
    excluded=[r['id'] for r in rows if r['id'].split('-')[0] in holdout_groups]
    rows=[r for r in rows if r['id'].split('-')[0] not in holdout_groups]
    if len(rows)<100:raise ValueError('Insufficient development data after source-prefix exclusion')
    groups=sorted({r['id'].split('-')[0] for r in rows});rng=np.random.default_rng(args.seed);rng.shuffle(groups)
    validation_groups=set(groups[:max(1,round(.2*len(groups)))])
    validation=[r for r in rows if r['id'].split('-')[0] in validation_groups]
    training=[r for r in rows if r['id'].split('-')[0] not in validation_groups]
    if {r['sha256'] for r in training}&{r['sha256'] for r in validation}:raise ValueError('Duplicate pixels across development split')
    protocol={'seed':args.seed,'epochs':args.epochs,'label_decoding':'0/1 masks: >0; 8-bit masks: >=128, including antialiased boundaries; reject masks with >5% intermediate values.',
        'training_ids':[r['id'] for r in training],
        'validation_ids':[r['id'] for r in validation],'excluded_training_ids_sharing_holdout_prefix':excluded,
        'holdout_prefixes_reserved':sorted(holdout_groups),'development_images_after_exclusion':len(rows),
        'test_rule':'Official val/valannot is held out; never loaded during training or threshold selection.',
        'group_rule':'Stem prefix before hyphen; physical tunnel IDs are unavailable, so tunnel-disjoint generalization is not established.',
        'threshold_candidates':THRESHOLDS,'initial_checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
        'image_hashes':{r['id']:r['sha256'] for r in rows},'architecture':'external crack-seg U-Net + SE',
        'selection_rule':'Maximum validation micro-F1; ties prefer higher threshold. Fixed initial baseline threshold 0.7.'}
    out.mkdir(parents=True,exist_ok=True);(out/'protocol.json').write_text(json.dumps(protocol,indent=2),'utf-8')
    device=torch.device(args.device);model=load_model(args.repo,args.checkpoint,device)
    baseline_scores=metrics_from_probabilities(predict(model,validation,device),validation)
    best_baseline=max(baseline_scores,key=lambda x:(x['f1'],x['threshold']))
    print('BASELINE VALIDATION',json.dumps(best_baseline),flush=True)
    train_loader=DataLoader(Samples(training,True),batch_size=args.batch_size,shuffle=True,num_workers=0)
    optimizer=torch.optim.AdamW(model.parameters(),lr=2e-5,weight_decay=1e-4)
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=args.epochs,eta_min=2e-6)
    scaler=torch.amp.GradScaler('cuda',enabled=device.type=='cuda')
    foreground=sum(float(r['mask'].sum()) for r in training);pixels=sum(r['mask'].size for r in training)
    positive_weight=torch.tensor(min(8.,max(1.,np.sqrt((pixels-foreground)/max(foreground,1)))),device=device)
    mean=MEAN.to(device);std=STD.to(device);best=None;history=[];start=time.perf_counter()
    for epoch in range(1,args.epochs+1):
        model.train();losses=[]
        for x,y in train_loader:
            x=(x.to(device)-mean)/std;y=y.to(device);optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type,enabled=device.type=='cuda'):
                logits=model(x);bce=F.binary_cross_entropy_with_logits(logits.float(),y,pos_weight=positive_weight)
                probs=logits.float().sigmoid();dims=(1,2,3)
                dice=1-((2*(probs*y).sum(dims)+1)/(probs.sum(dims)+y.sum(dims)+1)).mean()
                loss=.5*bce+.5*dice
            if not torch.isfinite(loss):raise ValueError('Non-finite training loss')
            scaler.scale(loss).backward();scaler.unscale_(optimizer);torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            scaler.step(optimizer);scaler.update();losses.append(float(loss.detach()))
        scheduler.step()
        record={'epoch':epoch,'loss':float(np.mean(losses)),'elapsed_s':time.perf_counter()-start}
        if epoch==1 or epoch%5==0 or epoch==args.epochs:
            scores=metrics_from_probabilities(predict(model,validation,device),validation)
            chosen=max(scores,key=lambda x:(x['f1'],x['threshold']));record['validation']=chosen
            if best is None or chosen['f1']>best['validation']['f1']:
                best={'epoch':epoch,'validation':chosen};torch.save(model.state_dict(),out/'best.pt')
            print('EPOCH',json.dumps(record),flush=True)
        history.append(record);(out/'training.json').write_text(json.dumps({'baseline_validation':baseline_scores,'baseline_calibrated':best_baseline,
            'best':best,'history':history,'positive_weight':float(positive_weight)},indent=2),'utf-8')
    print('TRAINING COMPLETE; official test labels have not been loaded.',json.dumps(best),flush=True)


def evaluate(args):
    out=args.out;protocol=json.loads((out/'protocol.json').read_text('utf-8'));training=json.loads((out/'training.json').read_text('utf-8'))
    if (out/'holdout.json').exists():raise ValueError('Holdout already evaluated; preserve original result')
    rows=pairs(args.data,'val')
    if len(rows)!=42:raise ValueError('Official holdout must contain 42 pairs')
    if set(protocol['image_hashes'].values())&{r['sha256'] for r in rows}:raise ValueError('Exact image overlap between development data and official holdout')
    device=torch.device(args.device);baseline=load_model(args.repo,args.checkpoint,device)
    base_prob=predict(baseline,rows,device);del baseline
    if device.type=='cuda':torch.cuda.empty_cache()
    adapted=load_model(args.repo,out/'best.pt',device);adapted_prob=predict(adapted,rows,device)
    calibrated_threshold=training['baseline_calibrated']['threshold'];adapted_threshold=training['best']['validation']['threshold']
    result={'dataset':'CTCD official val used as final holdout','images':len(rows),'ids':[r['id'] for r in rows],
        'baseline_fixed':metrics_from_probabilities(base_prob,rows,[.7])[0],
        'baseline_validation_calibrated':metrics_from_probabilities(base_prob,rows,[calibrated_threshold])[0],
        'adapted_validation_selected':metrics_from_probabilities(adapted_prob,rows,[adapted_threshold])[0],
        'selected_epoch':training['best']['epoch'],'checkpoint_sha256':hashlib.sha256((out/'best.pt').read_bytes()).hexdigest(),
        'limits':['Pixel metrics on the official file split, not guaranteed unseen tunnels or full-video defect accuracy.',
                  'Threshold and epoch were frozen using only the development validation split.',
                  'Upstream pretraining membership is not fully documented; this holdout is independent of the local domain-adaptation training, not a proven pretraining-disjoint benchmark.']}
    (out/'holdout.json').write_text(json.dumps(result,indent=2),'utf-8')
    np.savez_compressed(out/'holdout_predictions.npz',baseline=base_prob.astype(np.float16),adapted=adapted_prob.astype(np.float16),ids=np.array(result['ids']))
    print(json.dumps(result,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['train','evaluate'])
    p.add_argument('--data',type=Path,default=ROOT/'data/ctcd');p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--out',type=Path,default=ROOT/'results/ctcd_adaptation_v1')
    p.add_argument('--epochs',type=int,default=40);p.add_argument('--batch-size',type=int,default=8);p.add_argument('--seed',type=int,default=2026)
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda');args=p.parse_args()
    random.seed(args.seed);np.random.seed(args.seed);torch.manual_seed(args.seed);torch.set_num_threads(4)
    torch.backends.cudnn.benchmark=False
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    (train if args.mode=='train' else evaluate)(args)
if __name__=='__main__':main()
