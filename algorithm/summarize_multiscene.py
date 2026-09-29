"""Publish a small auditable metrics record and a local index of the actual scene packages."""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont,PngImagePlugin
ROOT=Path(__file__).resolve().parent


def load(path):return json.loads(Path(path).read_text('utf-8'))
def fmt(value):return '—' if value is None else f'{value:.3f}'


def examples(folder,out):
    predictions=np.load(folder/'holdout_predictions.npz');holdout=load(folder/'holdout.json');ids=predictions['ids'].tolist()
    indices=np.linspace(0,len(ids)-1,4,dtype=int);canvas=Image.new('RGB',(1072,1250),'#f4f7fa');draw=ImageDraw.Draw(canvas)
    try:font=ImageFont.truetype('arial.ttf',18)
    except OSError:font=ImageFont.load_default()
    for col,label in enumerate(['Original','Ground truth','Baseline (t=0.85)','Adapted (t=0.85)']):draw.text((10+col*266,8),label,fill='#253647',font=font)
    for row,index in enumerate(indices):
        identity=ids[index];image=np.array(Image.open(ROOT/'data/ctcd/val'/(identity+'.bmp')).convert('RGB'))
        target=np.array(Image.open(ROOT/'data/ctcd/valannot'/(identity+'.bmp')).convert('L'));target=target>0 if target.max()<=1 else target>=128
        panels=[image.copy(),np.repeat((target.astype(np.uint8)*255)[:,:,None],3,axis=2)]
        for name,threshold in [('baseline',holdout['baseline_validation_calibrated']['threshold']),('adapted',holdout['adapted_validation_selected']['threshold'])]:
            predicted=predictions[name][index]>=threshold;panel=(image*.7).astype(np.uint8)
            panel[predicted&target]=[25,180,115];panel[predicted&~target]=[235,65,65];panel[~predicted&target]=[55,125,245];panels.append(panel)
        y=40+row*286
        for col,panel in enumerate(panels):canvas.paste(Image.fromarray(panel),(10+col*266,y))
        draw.text((10,y+259),identity,fill='#253647',font=font)
    draw.text((10,1180),'Green: TP    Red: FP    Blue: FN   |   fixed evenly spaced holdout IDs',fill='#253647',font=font)
    draw.text((10,1210),'CTCD: huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset | CC BY 4.0',fill='#536879',font=font)
    meta=PngImagePlugin.PngInfo();meta.add_text('Source','https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset')
    meta.add_text('License','Source images and labels: https://creativecommons.org/licenses/by/4.0/')
    meta.add_text('Modifications','Four fixed holdout examples with model predictions and TP/FP/FN color overlays.')
    canvas.save(out,pnginfo=meta)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selected',type=Path,default=ROOT/'experiments/selected_runs.json')
    parser.add_argument('--baseline-experiment',help='Name of a rerun using the documented 2 Hz baseline budget')
    parser.add_argument('--recognition-experiment',type=Path,default=ROOT/'results/ctcd_adaptation_grouped_v2')
    args=parser.parse_args();selected=load(args.selected);captures={x['id']:x for x in load(ROOT/'experiments/captures.json')}
    folder=args.recognition_experiment;holdout=load(folder/'holdout.json');profile=load(folder/'model_profile.json')
    rows=[]
    for case in selected:
        run=ROOT/case['run'];q=load(run/'quality.json');audit=load(run/'geometry_audit.json');surface=load(run/'regularization/report.json')
        postproc_path=run/'dense/filtered/postprocess_report.json';postproc=load(postproc_path) if postproc_path.exists() else None
        frames=load(ROOT/case['frames']/'frames.json');scene=load(run/'scene.json');capture=captures[case['id']]
        baseline_experiment=args.baseline_experiment or ('multiscene_v1' if case['id']=='dvp_handheld' else 'multiscene_v2')
        baseline_dir=ROOT/'results'/baseline_experiment/(case['id']+'__baseline');baseline_path=baseline_dir/'reconstruction/quality.json'
        baseline=load(baseline_path) if baseline_path.exists() else None
        baseline_inputs=baseline['input_images'] if baseline else sum(x['accepted'] for x in load(baseline_dir/'frames/frames.json')['frames'])
        row={'id':case['id'],'title':case['title'],'source':capture['source_url'],'camera_description':capture['camera_description'],
            'review_notes':case.get('review_notes',[]),'video_sha256':capture['sha256'],'sample_hz':frames['target_sample_hz'],'input_images':q['input_images'],
            'registered_images':q['registered_images'],'points3D':q['points3D'],'reprojection_error_px':q['point_mean_reprojection_error_px'],
            'baseline':{'sample_hz':2,'input_images':baseline_inputs,'registered_images':baseline['registered_images'] if baseline else 0,
                'points3D':baseline['points3D'] if baseline else 0,'reprojection_error_px':baseline['point_mean_reprojection_error_px'] if baseline else None},
            'audit':audit,'surface_kind':surface['surface_kind'],'raw_triangles':surface['raw_topology']['triangles'],
            'refined_triangles':surface['regularized_topology']['triangles'],'supported_vertices':surface['supported_vertices'],
            'inferred_vertices':surface['inferred_vertices'],'raw_boundary_edges':surface['raw_topology']['boundary_edges'],
            'refined_boundary_edges':surface['regularized_topology']['boundary_edges'],'closed_boundary_edges':surface['closed_topology']['boundary_edges'] if surface['closed_topology'] else None,
            'prior_rejection':surface.get('prior_rejection_reason'),
            'mesh_postprocess':None if not postproc else {'points_before':postproc['points_before'],'points_kept':postproc['points_kept'],
                'extent_shrink_factor':postproc['extent_shrink_factor'],
                'aspect_ratio_before':postproc['aspect_ratio_extent_over_radius_before'],'aspect_ratio_after':postproc['aspect_ratio_extent_over_radius_after'],
                'holes_filled':postproc['hole_repair']['holes_filled'],'triangles_added':postproc['hole_repair']['triangles_added'],
                'velocity_drift_indicator':None if not postproc.get('velocity_drift_indicator') else postproc['velocity_drift_indicator']['drift_indicator']},
            'raw_localization':load(run/'localized.json')['summary'],
            'refined_localization':load(run/'regularization/localized.json')['summary'],'multiview':load(run/'multiview.json')['summary'],
            'viewer_verification':load(ROOT/'results/browser_multiscene'/case['id']/'verification.json'),
            'preview':case['id']+'/assets/'+Path(scene['cameras'][len(scene['cameras'])//2]['image_name']).stem+'.jpg'}
        rows.append(row)
    record={'geometry':rows,'recognition':holdout,'adaptation_protocol':{k:profile[k] for k in ['training_images','validation_images','holdout_images','excluded_training_tiles_sharing_holdout_prefix','decision_threshold','selected_epoch','checkpoint_sha256']},
        'limits':['Internal camera/mesh checks do not establish survey accuracy or metric scale.',
            'Rail short and TUM final results use 6 Hz instead of the 2 Hz baseline; this is an explicit additional sampling budget.',
            'Pixel holdout scores concern local adaptation with source-prefix exclusion; these images were also seen during exploratory trials, and upstream pretraining and physical tunnel separation are unverified.',
            'Multi-view groups remain unreviewed and can retain seams or writing; no video-level detection precision was measured.']}
    docs=ROOT/'docs';docs.mkdir(exist_ok=True);(docs/'multiscene_metrics.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
    out=ROOT/'web/multiscene';out.mkdir(parents=True,exist_ok=True);examples(folder,out/'recognition_examples.png')
    cards=[];table=[];inspection=[]
    for r in rows:
        method={'regularized_tunnel':'断面拟合','primitive_cylinder':'规则几何 · 圆柱','primitive_box':'规则几何 · 长方体'}.get(r['surface_kind'],'局部观测 · 有缺口')
        cards.append(f'<a class="card" href="{r["id"]}/index.html"><img src="{r["preview"]}" alt="所选原始帧"><div><span class="tag">{method}</span><h2>{html.escape(r["title"])}</h2><p>{r["registered_images"]}/{r["input_images"]} 帧 · {r["points3D"]:,} 稀疏点 · {r["refined_triangles"]:,} 面</p><b>打开三维与影像复核 →</b></div></a>')
        b=r['baseline'];mp=r.get('mesh_postprocess');mp_text='—' if not mp else f"×{mp['extent_shrink_factor']:.2f} · 填 {mp['holes_filled']} 孔"
        table.append(f'<tr><td>{html.escape(r["title"])}</td><td>{b["registered_images"]}/{b["input_images"]}</td><td>{r["registered_images"]}/{r["input_images"]}</td><td>2 → {r["sample_hz"]:g} Hz</td><td>{fmt(b["reprojection_error_px"])} → {r["reprojection_error_px"]:.3f}</td><td>{mp_text}</td></tr>')
        m=r['multiview'];inspection.append(f'<tr><td>{html.escape(r["title"])}</td><td>{m["candidate_observations"]}</td><td>{m["evidence_groups"]}</td><td>{m["groups_with_multiple_observations"]}</td></tr>')
    recognition=[]
    for name,key in [('原权重 / 原固定阈值','baseline_fixed'),('原权重 / 验证集选阈值','baseline_validation_calibrated'),('CTCD 域适配 / 验证集选阈值','adapted_validation_selected')]:
        m=holdout[key];recognition.append('<tr><td>'+name+'</td>'+''.join(f'<td>{m[k]:.3f}</td>' for k in ['threshold','precision','recall','f1','iou'])+'</tr>')
    page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>多相机建模与裂缝候选复核</title><style>
*{box-sizing:border-box}body{margin:0;color:#233747;background:#edf2f6;font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1240px;margin:auto;padding:32px 24px 70px}header{padding:20px 0 24px}h1{font-size:30px;margin:7px 0}h2{font-size:18px;margin:8px 0}.eyebrow{font-size:12px;letter-spacing:2px;color:#42667b}.lead{max-width:950px;color:#526675}.cards{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}.card{color:inherit;text-decoration:none;display:flex;background:white;border:1px solid #d7e1e8;border-radius:12px;overflow:hidden;transition:transform .15s}.card:hover{transform:translateY(-3px);border-color:#6c9cac}.card img{width:42%;height:200px;object-fit:contain;background:#192a38;flex-shrink:0}.card>div{padding:18px}.card p{font-size:13px;color:#586f80}.card b{font-size:13px;color:#216a7a}.tag{font-size:11px;background:#e7f3ef;color:#276558;padding:4px 8px;border-radius:20px}section{background:white;border:1px solid #d7e1e8;border-radius:12px;padding:22px;margin-top:22px;overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid #e6edf2}th{color:#536b7e;font-weight:500}p.note{font-size:13px;color:#586c7c}section img.examples{display:block;width:min(100%,1072px);margin:auto}.links a{color:#216a7a;margin-right:18px}@media(max-width:850px){.cards{grid-template-columns:1fr}main{padding:18px 12px}h1{font-size:25px}}</style></head><body><main><header><span class="eyebrow">REAL CAPTURES · CALIBRATED GEOMETRY · REVIEWABLE EVIDENCE</span><h1>多相机建模与裂缝候选复核</h1><p class="lead">四组真实素材覆盖手持手机、铁路前视与已标定鱼眼走廊。每个页面可切换原始/优化表面、同步相机帧、查看原图掩码，并跳转到关联观测。所有红色标记仍是待人工复核的表面裂缝候选。</p></header><div class="cards">'''+''.join(cards)+'''</div><section><h2>相机重建实测</h2><table><tr><th>素材</th><th>基线注册帧</th><th>优化注册帧</th><th>采样预算</th><th>平均重投影残差 / px</th><th>网格清理（轴向范围收缩 · 填充小孔）</th></tr>'''+''.join(table)+'''</table><p class="note">铁路短片和鱼眼序列增加了采样帧数，不能把全部收益归于某一算法。同帧数实验、失败尝试与观测支持门槛见完整报告。DVP 的重投影残差略有上升；这些残差都是内部拟合量，不是实测定位精度。单目场景保持相对尺度。鱼眼走廊的低纹理墙面仍有明显缺口，当前结果属于局部观测模型；三维显示使用虚拟透视，右侧保留原始镜头影像。</p></section><section><h2>裂缝分割：42 幅保留图的像素级结果</h2><table><tr><th>模型</th><th>阈值</th><th>精确率</th><th>召回率</th><th>F1</th><th>IoU</th></tr>'''+''.join(recognition)+'''</table><p class="note">从原 250 幅开发图中排除 30 幅与保留集共享源图编号的切片；176 幅训练、44 幅验证。模型在验证集选定第 35 轮与 0.85 阈值后评估保留集。这 42 幅曾用于探索性预试验；上游预训练数据及物理隧道 ID 也不完整，不能声称完全未接触、隧道独立或预训练独立；也未测量这四段视频的病害识别准确率。</p><img class="examples" src="recognition_examples.png" alt="固定选取保留图上的原图、人工标注与两种模型掩码"><p class="note">示例固定取保留集排序后的四个等间隔位置，未按得分挑选。绿色为命中，红色为假阳性像素，蓝色为漏检像素。可视化读取已保存的 float16 概率；表中正式指标来自原始评测记录。图像与人工标注来源：<a href="https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset">shiweiluo99 发布的 CTCD</a>，CC BY 4.0；预测和错误着色由本项目生成。</p></section><section><h2>视频中的候选关联</h2><table><tr><th>素材</th><th>候选观测数</th><th>关联组数</th><th>含多次观测的组</th></tr>'''+''.join(inspection)+'''</table><p class="note">使用标定投影、MVS 深度一致性和最小视差检查邻帧证据；同一帧的不同候选不会合并。遮挡、缺深度和弱视差不计为反证。重复出现的施工缝、涂写也可能形成关联组，因此组数不代表确诊病害数量。</p></section><section class="links"><h2>追溯与复现</h2><a href="../../docs/MULTISCENE.md">实验说明与运行命令</a><a href="../../docs/multiscene_metrics.json">实测记录</a><p class="note">原始视频、图像、三维数据和权重保存在本机；仓库发布流程代码与小型实验记录。素材作者、来源和许可见各场景页面。</p></section></main></body></html>'''
    (out/'index.html').write_text(page,'utf-8');print(json.dumps({'gallery':str(out/'index.html'),'cases':len(rows),'metrics':str(docs/'multiscene_metrics.json')},ensure_ascii=False))
if __name__=='__main__':main()
