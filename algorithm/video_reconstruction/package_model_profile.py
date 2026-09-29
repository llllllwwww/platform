"""Bind an audited segmentation experiment's checkpoint, threshold and evaluation provenance."""
import argparse
import hashlib
import json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiment',required=True,type=Path);args=p.parse_args();folder=args.experiment
    protocol=json.loads((folder/'protocol.json').read_text('utf-8'));training=json.loads((folder/'training.json').read_text('utf-8'));holdout=json.loads((folder/'holdout.json').read_text('utf-8'))
    if 'excluded_training_ids_sharing_holdout_prefix' not in protocol:raise ValueError('Source-prefix exclusion audit is required for this deployment profile')
    checkpoint=folder/'best.pt';digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if digest!=holdout['checkpoint_sha256']:raise ValueError('Model changed after evaluation')
    if training['best']['validation']['threshold']!=holdout['adapted_validation_selected']['threshold']:raise ValueError('Threshold changed after validation')
    profile={'schema_version':1,'name':'U-Net + SE / CTCD source-group separated adaptation','checkpoint':'best.pt','checkpoint_sha256':digest,
        'decision_threshold':training['best']['validation']['threshold'],'selected_epoch':training['best']['epoch'],
        'training_images':len(protocol['training_ids']),'validation_images':len(protocol['validation_ids']),'holdout_images':holdout['images'],
        'excluded_training_tiles_sharing_holdout_prefix':len(protocol['excluded_training_ids_sharing_holdout_prefix']),
        'dataset_source':'https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset',
        'model_source':'https://github.com/Ishaan1402/crack-seg','initial_checkpoint_sha256':protocol['initial_checkpoint_sha256'],
        'pixel_holdout_metrics':holdout['adapted_validation_selected'],'baseline_pixel_metrics':holdout['baseline_fixed'],
        'metric_scope':'Native 256x256 CTCD images before connected-component filtering; this is not confirmed video-defect precision.',
        'normalization':'RGB/ImageNet; grayscale replicated to three channels','label_decoding':protocol['label_decoding'],
        'limits':holdout['limits']+['Source-prefix groups are separated; physical tunnel IDs remain unavailable.',
            'External architecture and initial weights are not bundled or relicensed by this project.']}
    (folder/'model_profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(profile,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
