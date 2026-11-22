"""Export the supervised inputs used by each capacity model to private CSV files."""

from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from train_evaluate import ITERATIONS, PUBLISHED_METHODS, SPECS, fingerprint, load_inputs, receipt, split_masks


TARGET_RULES = {
    'training': 'Selected whole-facility count and method from the run’s source-selection iteration.',
    'validation': 'Original reported count used to tune model settings and stopping rounds.',
    'interval_calibration': 'Original reported count used to calibrate prediction intervals.',
    'test': 'Original reported count used for the shared held-out evaluation.',
}
METADATA = ['facility_id', 'role', 'capacity_target', 'target_method']


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate_holdouts(frame, masks, holdouts):
    for role, mask in masks.items():
        expected = holdouts[role]
        if int(mask.sum()) != expected['n'] or fingerprint(frame, mask) != expected['sha256_facility_and_observed_count']:
            raise ValueError(f'{role} facilities or original reported targets differ from the recorded run.')


def arm_projection(frame, groups, holdouts, run, results_dir):
    iteration, spec = run['iteration'], run['feature_set']
    run_id = f'{iteration}__{spec}'
    if run['run_id'] != run_id or iteration not in ITERATIONS:
        raise ValueError(f'Unknown model run {run["run_id"]}.')
    fields = [field for block in SPECS[spec] for field in groups[block]]
    aliases = {'numberDoctors': f'numberDoctors_{iteration}'} if 'numberDoctors' in fields else {}
    encoder_path = results_dir / f'{run_id}_encoding.json'
    encoding = read_json(encoder_path)
    if (fields != run['features'] or fields != encoding['fields'] or aliases != encoding['field_aliases']
            or set(fields) & set(METADATA)):
        raise ValueError(f'{run_id} feature columns or aliases differ from the recorded run.')

    selected = frame[f'capacity_{iteration}']
    methods = frame[f'capacity_method_{iteration}']
    train = frame.split.eq('train') & frame.training_eligible.eq(True) & selected.notna()
    if not methods.loc[train].isin(PUBLISHED_METHODS).all() or dict(Counter(methods.loc[train])) != run['y_sources']:
        raise ValueError(f'{run_id} training target methods differ from the recorded run.')
    masks = {'training': train, **holdouts}
    counts = {role: int(mask.sum()) for role, mask in masks.items()}
    if counts != run['counts'] or (train & (holdouts['validation'] | holdouts['interval_calibration'] | holdouts['test'])).any():
        raise ValueError(f'{run_id} labeled row counts or membership differ from the recorded run.')

    keep = train | holdouts['validation'] | holdouts['interval_calibration'] | holdouts['test']
    source_fields = [aliases.get(field, field) for field in fields]
    projection = frame.loc[keep, ['facility_id', *source_fields]].copy()
    projection.columns = ['facility_id', *fields]
    projection['capacity_target'] = frame.loc[keep, 'capacity_observed'].astype('Int64')
    projection['target_method'] = 'fdr_reported'
    training_ids = frame.index[train]
    projection.loc[training_ids, 'capacity_target'] = selected.loc[train].astype('Int64')
    projection.loc[training_ids, 'target_method'] = methods.loc[train]
    for role, mask in masks.items():
        projection.loc[frame.index[mask], 'role'] = role
    for role, available in run['feature_nonmissing'].items():
        export_role = 'training' if role == 'train' else role
        if projection.loc[projection.role.eq(export_role), fields].notna().sum().to_dict() != available:
            raise ValueError(f'{run_id} {export_role} feature coverage differs from the recorded run.')
    if projection[['capacity_target', 'target_method', 'role']].isna().any().any():
        raise ValueError(f'{run_id} supervised export has an unlabeled row.')

    return projection[[*METADATA, *fields]], {
        'run_id': run_id, 'iteration': iteration, 'feature_set': spec, 'counts': counts,
        'features': fields, 'field_aliases': aliases, 'encoder_sha256': receipt(encoder_path),
    }


def export_arms(input_dir, metrics, output_dir):
    if output_dir.exists():
        raise FileExistsError(f'Use a fresh output directory. {output_dir} already exists.')
    report = read_json(metrics)
    input_hashes = {name: receipt(input_dir / name) for name in ('facilities.jsonl', 'feature_groups.json')}
    if input_hashes != report['input_sha256']:
        raise ValueError('Prepared input files differ from the recorded run.')
    frame, groups = load_inputs(input_dir)
    holdouts = dict(zip(('validation', 'interval_calibration', 'test'), split_masks(frame), strict=True))
    validate_holdouts(frame, holdouts, report['holdout'])
    if not report['runs']:
        raise ValueError('The report contains no trained models.')
    planned = {(iteration, spec) for iteration in ITERATIONS for spec in SPECS}
    accounted = [(row['iteration'], row['feature_set']) for row in report['runs'] + report['skipped']]
    if set(accounted) != planned or len(accounted) != len(planned):
        raise ValueError('The report must account for each planned model arm exactly once.')
    audit = {'input_sha256': input_hashes, 'metrics_sha256': receipt(metrics), 'holdout': report['holdout'],
             'target_rules': TARGET_RULES, 'runs': [], 'skipped': report['skipped']}

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.capacity-datasets-', dir=output_dir.parent) as scratch:
        staging = Path(scratch) / 'datasets'
        staging.mkdir()
        for run in report['runs']:
            frame_out, record = arm_projection(frame, groups, holdouts, run, metrics.parent)
            csv_path = staging / f'{record["run_id"]}.csv'
            frame_out.to_csv(csv_path, index=False, encoding='utf-8', lineterminator='\n')
            audit['runs'].append({**record, 'file': csv_path.name, 'sha256': receipt(csv_path)})
        (staging / 'dataset_receipt.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
        staging.rename(output_dir)
    return audit


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path, help='Prepared capacity model inputs.')
    parser.add_argument('--metrics', required=True, type=Path, help='Completed training metrics JSON.')
    parser.add_argument('--output-dir', required=True, type=Path, help='Fresh private export directory outside version control.')
    args = parser.parse_args()
    report = export_arms(args.input_dir, args.metrics, args.output_dir)
    print(json.dumps({'datasets': len(report['runs']), 'receipt': str(args.output_dir / 'dataset_receipt.json')}, indent=2))
