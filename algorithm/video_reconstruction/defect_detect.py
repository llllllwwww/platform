"""隧道病害（裂缝）检测：复用开源模型做像素级分割，输出三维定位所需的像素坐标。

模型与代码来源（第三方，非本项目自研）：
  - 权重 ishaan1402/crack-seg (HuggingFace)，U-Net + SE + 深监督，DeepCrack 测试集 Dice 0.866
  - 代码 github.com/Ishaan1402/crack-seg（滑窗推理 + 高斯加权融合）
  ⚠️ 该仓库**未声明 LICENSE**（默认保留全部权利）→ 仅用于研究验证；
     进交付物前必须替换为自研或明确许可的模型。
  域差异提醒：该模型在桥梁/路面/无人机近景裂缝上训练，隧道内壁属跨域，
  精度必须按实测评估，不得直接引用其论文指标。

接口纪律（与 localize_defects.py 的契约）：
  输出的像素必须是**原始图像像素**。model 内部做的滑窗/归一化不改变输出尺寸，
  但一旦将来加入 resize/letterbox，必须在此处反变换，否则射线求交结果全错且不报错。
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np


def load_predictor(repo: Path, checkpoint: Path, device_name: str | None):
    sys.path.insert(0, str(repo))
    import torch
    from src.config.schema import SystemSettings
    from src.models.checkpoint import load_unet_checkpoint
    from src.inference.sliding_window import SlidingWindowPredictor

    settings = SystemSettings.load_from_yaml(str(repo / 'config' / 'config.yaml'))
    if device_name:
        device = torch.device(device_name)
    elif torch.cuda.is_available():
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')
    model, features = load_unet_checkpoint(str(checkpoint), device)
    params = sum(p.numel() for p in model.parameters()) / 1e6
    print(f'[模型] features={features} 参数={params:.1f}M 设备={device}', flush=True)
    return SlidingWindowPredictor(model, settings, device), device


def components(mask: np.ndarray, min_area: int):
    """连通域标记（自带实现，不引入 cv2 的额外依赖差异）。"""
    from scipy import ndimage
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    out = []
    for index in range(1, count + 1):
        ys, xs = np.nonzero(labels == index)
        if len(ys) < min_area:
            continue
        out.append((ys, xs))
    return out


def sample_pixels(ys, xs, cap):
    """把掩码像素按均匀步长抽稀到 cap 个，控制射线求交的规模。"""
    n = len(ys)
    if n <= cap:
        return np.column_stack([xs, ys]).astype(float) + 0.5
    stride = int(np.ceil(n / cap))
    return np.column_stack([xs[::stride], ys[::stride]]).astype(float) + 0.5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', required=True, help='原始帧目录（须与重建所用图像一致）')
    parser.add_argument('--out', required=True, help='输出目录')
    parser.add_argument('--repo', default='third_party/crack-seg')
    parser.add_argument('--checkpoint', default='data/crack_model/unet_v3.pth')
    parser.add_argument('--scene', help='scene.json，用于取其 scene_id 写入检测结果')
    parser.add_argument('--threshold', type=float, default=0.5)
    parser.add_argument('--min-area', type=int, default=60, help='小于该像素数的连通域视为噪声')
    parser.add_argument('--max-points', type=int, default=120, help='每个病害最多保留的像素点')
    parser.add_argument('--max-images', type=int, default=0, help='0 表示不限制')
    parser.add_argument('--overlay', action='store_true', help='额外输出叠加图便于目检')
    parser.add_argument('--device', choices=['cpu', 'cuda'])
    args = parser.parse_args()
    if not 0 < args.threshold < 1 or args.min_area < 1 or args.max_points < 1:
        raise ValueError('threshold must be in (0,1), min-area and max-points positive')

    import cv2
    root = Path(__file__).resolve().parent
    images_dir, out_dir = Path(args.images), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / 'detections.json').exists():
        raise FileExistsError('Refusing to overwrite existing inference results')
    (out_dir / 'masks').mkdir(exist_ok=True)
    if args.overlay:
        (out_dir / 'overlay').mkdir(exist_ok=True)

    scene_id = None
    if args.scene:
        scene = json.loads(Path(args.scene).read_text('utf-8'))
        scene_id = scene['scene_id']
        print('[契约] scene_id =', scene_id, flush=True)

    predictor, device = load_predictor(root / args.repo, root / args.checkpoint, args.device)

    files = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in {'.png', '.jpg', '.jpeg'})
    if args.max_images:
        files = files[:args.max_images]
    print(f'[输入] {len(files)} 张图像', flush=True)

    detections, stats = [], []
    import time
    start = time.time()
    for i, path in enumerate(files, 1):
        bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError(f'无法读取图像: {path}')
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        probs, mask, ratio = predictor.predict_large_image(rgb, threshold=args.threshold)
        if probs.shape != rgb.shape[:2] or not np.isfinite(probs).all():
            raise ValueError('Model probability map must be finite and match original image size')
        mask = (mask > 0).astype(np.uint8)
        cv2.imencode('.png', mask * 255)[1].tofile(out_dir / 'masks' / (path.stem + '.png'))
        regions = components(mask, args.min_area)
        for j, (ys, xs) in enumerate(regions):
            confidence = float(probs[ys, xs].mean())
            pixels = sample_pixels(ys, xs, args.max_points)
            if not np.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError('Non-finite or out-of-range model probability')
            detections.append({'id': f'{path.stem}-d{j}', 'label': 'crack_candidate',
                               'review_status': 'unreviewed',
                               'confidence': round(confidence, 4),
                               'image_name': path.name, 'pixels': pixels.tolist(),
                               'mask_area_px': int(len(ys))})
        stats.append({'image': path.name, 'area_ratio': float(ratio),
                      'regions': len(regions), 'pixels_kept': int(sum(len(sample_pixels(*r, args.max_points)) for r in regions))})
        if args.overlay:
            overlay = bgr.copy()
            overlay[mask > 0] = (0, 0, 255)
            cv2.imencode('.png', cv2.addWeighted(bgr, 0.6, overlay, 0.4, 0))[1].tofile(out_dir / 'overlay' / path.name)
        if i % 10 == 0 or i == len(files):
            print(f'  [{i}/{len(files)}] {time.time()-start:.0f}s 累计病害 {len(detections)}', flush=True)

    result = {'coordinate_space': 'original_image_pixels', 'scene_id': scene_id,
              'detector': {'name': 'crack-seg U-Net (third-party, UNLICENSED)',
                           'weights': str(args.checkpoint), 'threshold': args.threshold,
                           'device': str(device), 'min_area_px': args.min_area},
              'detections': detections,
              'per_image': stats,
              'limits': ['模型为桥梁/路面近景裂缝训练，隧道内壁属跨域，指标不可直接引用。',
                         'confidence 是掩码内平均概率，不代表定位精度。',
                         '像素已抽稀到每病害 %d 点，用于射线求交。' % args.max_points]}
    (out_dir / 'detections.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(f'\n[完成] 病害 {len(detections)} 个，涉及 {len([s for s in stats if s["regions"]])} 帧，'
          f'耗时 {time.time()-start:.0f}s -> {out_dir / "detections.json"}', flush=True)


if __name__ == '__main__':
    main()
