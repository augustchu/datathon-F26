"""Record sanitized evaluation and model artifacts with the W&B SDK."""

import argparse
from collections.abc import Mapping
import fcntl
from hashlib import file_digest, sha256
import itertools
import json
import math
import os
from pathlib import Path
import re

import wandb


SOURCE_RESULT_FIELDS = ('checkpoint_evidence', 'dataset_diagnostics', 'extension_holdout_iteration_01_only',
                        'independent_holdout', 'native_cross_fit', 'paired_common_cross_fit',
                        'shared_tie_break_publisher_priors', 'xgboost_version')
POPULATION_FIELDS = ('sha256', 'source_rows', 'source_revision', 'grain', 'status', 'nfhs_raw_key_order_sha256',
                     'nfhs_order_reference_sha256', 'raw_cache_order_exact_match', 'nonmissing_source_rows',
                     'derived_keyed_sha256')
RELEASE_FIELDS = ('selected_run', 'source_iteration', 'original_rows_preserved', 'distinct_facilities_imputed',
                  'source_rows_imputed', 'final_methods_by_original_row', 'preserved_original_values',
                  'identity_holds_preserved', 'distinct_facilities_withheld_for_target_quality')
ROW_LEVEL_KEYS = {'facility_id', 'branch_id', 'row_index', 'source_row_index', 'case_id', 'raw_observed',
                  'candidate_beds', 'phone', 'phone_number', 'address', 'source_url', 'identity_source', 'pin',
                  'out_of_fold', 'target_defects', 'capacity_quality_holds', 'path', 'run_files'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def select_fields(value, fields):
    return {key: value[key] for key in fields if key in value}


def check_aggregate(value):
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.casefold() in ROW_LEVEL_KEYS:
                raise ValueError(f'Row-level or local field is in the W&B payload: {key}')
            check_aggregate(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            check_aggregate(item)


def numeric_leaves(value, prefix=''):
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from numeric_leaves(item, f'{prefix}/{key}' if prefix else key)
    elif isinstance(value, (int, float)) and math.isfinite(value):
        yield prefix, value


def safe_name(value):
    return re.sub(r'[^a-zA-Z0-9_.-]+', '-', str(value)).strip('-')


def copy_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix('.tmp')
    staging.write_text(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(staging, path)


def verified_previous(args, name, fingerprint):
    previous = args.completed.get(name)
    if previous is None:
        return None
    if previous['input_sha256'] != fingerprint:
        raise ValueError(f'{name} already has a different logging receipt. Use a separate output directory.')
    if args.mode == 'offline':
        run_dir = Path(previous['run_files']).parent
        if not (run_dir / f'run-{previous["id"]}.wandb').is_file():
            raise ValueError(f'{name} has no saved W&B sync file. Recover it or use a separate output directory.')
    else:
        remote = wandb.Api(timeout=15).run(f'{args.entity}/{args.project}/{previous["id"]}')
        if remote.state != 'finished' or previous['artifact'] not in {asset.name for asset in remote.logged_artifacts()}:
            raise ValueError(f'{name} completed receipt no longer matches the saved W&B run and artifact.')
    return previous


def record_run(args, name, job_type, config, summary, files=(), history=()):
    destination = args.output_dir / 'metadata' / f'{safe_name(name)}.json'
    payload = {'config': config, 'result': summary}
    if history:
        payload['learning_history'] = history
    check_aggregate(payload)
    artifact_files = {}
    for path in files:
        if not path.is_file() or path.suffix not in {'.json', '.ubj'}:
            raise ValueError(f'Invalid model artifact {path}')
        with path.open('rb') as handle:
            artifact_files[path.name] = file_digest(handle, 'sha256').hexdigest()
    fingerprint = sha256(json.dumps({'payload': payload, 'artifact_sha256': artifact_files, 'name': name,
                                    'job_type': job_type, 'mode': args.mode, 'entity': args.entity,
                                    'project': args.project, 'group': args.group},
                                   sort_keys=True, allow_nan=False).encode()).hexdigest()
    previous = verified_previous(args, name, fingerprint)
    if previous is not None:
        return previous
    copy_json(destination, payload)
    with wandb.init(project=args.project, entity=args.entity, mode=args.mode, dir=str(args.output_dir),
                    name=name, group=args.group, job_type=job_type, config=config,
                    settings=wandb.Settings(console='off', disable_git=True, disable_code=True, x_disable_meta=True,
                                            x_disable_machine_info=True, x_disable_stats=True)) as run:
        if history:
            run.define_metric('boosting_round')
            run.define_metric('trial_*', step_metric='boosting_round')
        for step in history:
            run.log(step)
        for key, number in numeric_leaves(summary):
            run.summary[key] = number
        run.summary['evaluation_record'] = summary
        artifact_type = 'model' if files else 'evaluation'
        artifact = wandb.Artifact(f'{safe_name(name)}-{artifact_type}', type=artifact_type)
        artifact.add_file(str(destination), name='evaluation.json')
        for path in files:
            artifact.add_file(str(path), name=f'model/{path.name}')
        artifact = run.log_artifact(artifact)
        artifact_name = artifact.wait(timeout=120).name if args.mode == 'online' else artifact.name
        receipt = {'name': name, 'job_type': job_type, 'id': run.id, 'mode': args.mode,
                   'entity': run.entity or args.entity, 'project': run.project, 'artifact': artifact_name,
                   'url': run.url if args.mode == 'online' else None, 'run_files': str(Path(run.dir).resolve()),
                   'input_sha256': fingerprint}
    args.completed[name] = receipt
    copy_json(args.receipt, {'runs': list(args.completed.values())})
    return receipt


def source_runs(args, comparison):
    for number in ('01', '02'):
        path = args.prior_root / f'iteration-{number}' / 'model'
        original = read_json(path / 'model_evaluation.json')
        capacity = original['fields']['capacity']
        aggregate = select_fields(capacity, ('default', 'group_held_out', 'reason', 'supported_cases', 'xgboost_trained'))
        yield record_run(args, f'source-iteration-{number}', 'source-selector',
                         {'iteration': number, 'field': 'capacity', 'parameters': original['parameters'],
                          'rounds': original['rounds'], 'xgboost_version': original['xgboost_version']},
                         {'original_grouped_evaluation': aggregate, 'review_coverage': original['field_review_coverage'],
                          'insurer_head_to_head': original['iffco_sbi_head_to_head']},
                         files=(path / 'capacity_selector.json',))
    yield record_run(args, 'source-selectors-common-evaluation', 'source-comparison',
                     {'source_checkpoints': ['iteration-01', 'iteration-02']}, comparison)


def learning_history(tuning):
    """Turn XGBoost histories into aligned W&B series indexed by boosting round."""
    steps = {}
    for index, attempt in enumerate(tuning, 1):
        history = attempt.get('history', {})
        if isinstance(history, Mapping):
            for cohort, metrics in history.items():
                for metric, values in metrics.items():
                    for round_index, value in enumerate(values, 1):
                        steps.setdefault(round_index, {'boosting_round': round_index})[
                            f'trial_{index}/{cohort}/{metric}'] = value
    return [steps[index] for index in sorted(steps)]


def input_coverage(audit):
    aggregate = select_fields(audit, ('source_rows_per_iteration', 'canonical_facilities', 'unlinked_source_rows_removed',
                                      'duplicate_linked_rows_collapsed', 'counts_by_split', 'holdout_id_sha256_by_split',
                                      'capacity_target_audit_sha256', 'capacity_split_sha256', 'nfhs'))
    aggregate['population'] = select_fields(audit['population'], POPULATION_FIELDS)
    aggregate['explicit_quality_hold_count'] = len(audit.get('capacity_quality_holds', ()))
    return aggregate


def verification_run(args, comparison):
    review = select_fields(read_json(args.verification_receipt), ('cases', 'candidate_rows', 'status_counts', 'reviewer',
                                                                 'completed_in_app',
                                                                 'screenshot_loaded_for_every_candidate'))
    if (review['completed_in_app'] is not True or review['screenshot_loaded_for_every_candidate'] is not True
            or review['cases'] != comparison['independent_holdout']['reviewed']
            or sum(review['status_counts'].values()) != review['candidate_rows']):
        raise ValueError('The app verification receipt does not cover the complete independent review.')
    return record_run(args, 'in-the-loop-desk-review', 'verification', {'review_type': 'public-evidence-desk-review'},
                      review)


def capacity_runs(args, report, coverage, release):
    shared = select_fields(report, ('xgboost_version', 'seed', 'population_status', 'max_rounds',
                                    'early_stopping_patience', 'point_predictions', 'input_sha256'))
    shared['registered_holdout'] = report['holdout']
    for row in report['runs']:
        run_id = row['run_id']
        config = {**shared, **select_fields(row, ('iteration', 'feature_set', 'features', 'chosen_config'))}
        aggregate = select_fields(row, ('counts', 'y_sources', 'training_targets', 'validation', 'test', 'subgroups',
                                        'baseline', 'interval', 'feature_nonmissing'))
        aggregate['tuning'] = {f'trial_{index}': select_fields(trial, ('scale', 'depth', 'best_round',
                                                                      'elapsed_seconds', 'parameters', 'validation'))
                               for index, trial in enumerate(row['tuning'], 1)}
        model_name, encoding_name = f'{run_id}.ubj', f'{run_id}_encoding.json'
        if (safe_name(run_id) != run_id or row['model_file'] != model_name or row['encoding_file'] != encoding_name):
            raise ValueError(f'Unexpected checkpoint or encoder filename for {run_id}.')
        files = tuple(args.capacity_results.parent / name for name in (model_name, encoding_name))
        yield record_run(args, run_id, 'capacity-regression', config, aggregate, files, learning_history(row['tuning']))
    yield record_run(args, 'capacity-model-comparison', 'experiment-summary', shared,
                     {'comparison': report['comparison'], 'winner': report['winner'], 'skipped': report['skipped'],
                      'input_audit': coverage, 'release_audit': release})


def log_all(args):
    args.receipt = args.output_dir / 'receipt.json'
    args.completed = {row['name']: row for row in read_json(args.receipt)['runs']} if args.receipt.is_file() else {}
    comparison = select_fields(read_json(args.source_results), SOURCE_RESULT_FIELDS)
    capacity = read_json(args.capacity_results)
    coverage = input_coverage(read_json(args.input_audit))
    release = select_fields(read_json(args.release_audit), RELEASE_FIELDS)
    if release.get('selected_run') != capacity['winner']['release_run_id']:
        raise ValueError('The capacity release receipt names a different winning model than the evaluation.')
    first = verification_run(args, comparison)
    for receipt in itertools.chain((first,), source_runs(args, comparison), capacity_runs(args, capacity, coverage, release)):
        print(f'{receipt["id"]}  {receipt["name"]}  {receipt["mode"]}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('source-results', 'capacity-results', 'input-audit', 'release-audit', 'verification-receipt',
                     'prior-root', 'output-dir'):
        parser.add_argument(f'--{argument}', type=Path, required=True)
    parser.add_argument('--mode', choices=('online', 'offline'), default='offline')
    parser.add_argument('--project', default=os.getenv('WANDB_PROJECT', 'datathon-f26'))
    parser.add_argument('--entity', default=os.getenv('WANDB_ENTITY'))
    parser.add_argument('--group', default='max-supplemented-capacity-comparison')
    args = parser.parse_args()
    if args.mode == 'online' and not args.entity:
        parser.error('Provide the W&B entity when publishing online.')
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('WANDB_CACHE_DIR', str(args.output_dir / 'cache'))
    os.environ.setdefault('WANDB_DATA_DIR', str(args.output_dir / 'staging'))
    with (args.output_dir / '.tracking.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log_all(args)


if __name__ == '__main__':
    main()
