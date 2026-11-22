"""Refit the capacity arms and quantify matched source and feature changes."""

from argparse import ArgumentParser
from itertools import combinations
from pathlib import Path
import json
import os
import shutil
import sys
from time import monotonic

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_info

from train_evaluate import (district_clusters, fingerprint, load_inputs, paired_interval, receipt,
                            run_experiment, score, split_masks, xgb)


FEATURES = ('facility_only', 'population', 'socioeconomic', 'socioeconomic_population')
LABELS = dict(zip(FEATURES, ('Facility only', '+ Population', '+ NFHS', '+ Both'), strict=True))
RULES = {'ranked': 'iteration_01', 'newest': 'iteration_03'}
RULE_LABELS = {'ranked': 'Publisher ranked', 'newest': 'Newest report'}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf8')


def preflight(inputs, archived, frame):
    checksums = {name: receipt(inputs / name) for name in archived['input_sha256']}
    if checksums != archived['input_sha256']:
        raise ValueError('Facility snapshot hashes differ from the frozen comparison.')
    duplicate_override = os.getenv('KMP_DUPLICATE_LIB_OK', '').casefold()
    if 'torch' in sys.modules or duplicate_override not in {'', '0', 'false', 'no'}:
        raise RuntimeError('Use a clean CPU process without PyTorch or the OpenMP duplicate-library override.')
    libraries = sorted({r['filepath'] for r in threadpool_info() if r['internal_api'] == 'openmp'})
    if len(libraries) > 1:
        raise RuntimeError(f'Use one OpenMP runtime. Loaded libraries: {libraries}')

    fit = frame.split.eq('train') & frame.training_eligible
    masks = dict(zip(('validation', 'interval_calibration', 'test'), split_masks(frame), strict=True))
    exclusions = ~frame.routing_eligible | frame.capacity_target_defect
    if (fit & exclusions).any():
        raise ValueError('A flagged target or unroutable facility is still eligible for fitting.')
    for a, b in combinations([fit, *masks.values()], 2):
        if (a & b).any():
            raise ValueError('One facility appears in multiple analysis partitions.')
    disputes_by_partition = {}
    for name, mask in masks.items():
        if fingerprint(frame, mask) != archived['holdout'][name]['sha256_facility_and_observed_count']:
            raise ValueError(f'The frozen {name} facilities or labels changed.')
        if (mask & (exclusions | frame.source_selection_review_exposed)).any():
            raise ValueError(f'The {name} cohort contains a flagged or review-exposed bed label.')
        disputes_by_partition[name] = int((mask & frame.capacity_source_disagreement).sum())
        if not frame.loc[mask, 'capacity_observed_status'].eq('reported').all():
            raise ValueError('Evaluation requires original reported bed counts.')

    if disputes_by_partition['test']:
        raise ValueError('This comparison requires a dispute-free common test set.')

    probe = xgb.DMatrix(np.arange(32, dtype=np.float32).reshape(16, 2), label=np.arange(16), nthread=2)
    booster = xgb.train({'objective': 'reg:squarederror', 'tree_method': 'hist', 'nthread': 2,
                         'max_depth': 2}, probe, num_boost_round=3)
    if not np.isfinite(booster.predict(probe)).all():
        raise RuntimeError('The local CPU booster smoke test failed.')
    return masks, {'input_sha256': checksums, 'openmp_libraries': libraries, 'cpu_smoke_passed': True,
                   'openmp_duplicate_override_enabled': False,
                   'xgboost_version': str(xgb.__version__), 'holdout': archived['holdout'],
                   'original_evaluation_disputes': disputes_by_partition}


def build_strict_input(inputs, destination, frame):
    """Change only the training eligibility of source-disputed bed labels."""
    subset = frame.split.eq('train') & frame.training_eligible & frame.capacity_source_disagreement
    affected = set(frame.index[subset])
    destination.mkdir()
    shutil.copyfile(inputs / 'feature_groups.json', destination / 'feature_groups.json')
    with (inputs / 'facilities.jsonl').open() as source, (destination / 'facilities.jsonl').open('w') as target:
        for raw in source:
            row = json.loads(raw)
            if row['facility_id'] in affected:
                row['training_eligible'] = False
                raw = json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'
            target.write(raw)
    strict, _ = load_inputs(destination)
    columns = frame.columns.drop('training_eligible')
    if (not frame[columns].equals(strict[columns])
            or not strict.training_eligible.equals(frame.training_eligible & ~subset)):
        raise ValueError('The stricter input changed more than the planned training mask.')
    return len(affected)


def paired_results(frame, test, trained, folder):
    """Return aggregate contrasts and private row-aligned predictions."""
    data = json.loads((folder / 'metrics.json').read_text())
    saved = pd.read_csv(folder / 'test_predictions.csv', dtype={'facility_id': str})
    predicted = saved.pivot(index='facility_id', columns='run_id', values='capacity_predicted')
    labels = frame.loc[test, 'capacity_observed']
    if set(predicted.index) != set(labels.index) or predicted.isna().any().any():
        raise ValueError('All arms must predict exactly the same held-out facilities.')
    for _, rows in saved.groupby('run_id'):
        if not rows.set_index('facility_id').capacity_observed.reindex(labels.index).equals(labels):
            raise ValueError('An arm used a different test target.')
    actual = labels.to_numpy(dtype=float)
    predicted = predicted.reindex(labels.index)
    clusters = district_clusters(frame.loc[test])
    error = {}
    records = []
    for arm in data['runs']:
        arm_id = arm['run_id']
        errors = np.abs(predicted[arm_id].to_numpy() - actual)
        if not np.isclose(errors.mean(), arm['test']['mae'], rtol=0, atol=1e-10):
            raise ValueError(f'Could not reproduce saved scores for {arm_id}.')
        if arm['iteration'] not in [*RULES.values(), 'raw_fdr']:
            if not np.array_equal(predicted[arm_id], predicted[f"iteration_03__{arm['feature_set']}"]):
                raise ValueError('The empirically identical date rules diverged.')
            continue
        rule = next((k for k, v in RULES.items() if v == arm['iteration']), 'raw_fdr')
        feature = arm['feature_set']
        error[rule, feature] = errors
        records.append({'training_policy': trained, 'rule': rule, 'features': feature,
                        'training_n': arm['counts']['training'], 'test_n': arm['test']['n'],
                        'validation_mae': arm['validation']['mae'], 'test_mae': arm['test']['mae'],
                        'test_rmse': arm['test']['rmse'], 'test_mean_bias': arm['test']['mean_bias'],
                        'test_coverage': arm['interval']['coverage'], 'test_median_width': arm['interval']['median_width']})

    contrasts = []
    def contrast(name, group, base, contender):
        contrasts.append({'training_policy': trained, 'contrast': name, 'factor': group,
                          **paired_interval(error[base], error[contender], clusters)})

    for tier in FEATURES:
        contrast(f'Ranked → newest | {LABELS[tier]}', 'Source rule only', ('ranked', tier), ('newest', tier))

    additions = (('facility_only', 'population', 'Population'), ('socioeconomic', 'socioeconomic_population', 'Population after NFHS'),
                 ('facility_only', 'socioeconomic', 'NFHS'), ('population', 'socioeconomic_population', 'NFHS after population'),
                 ('facility_only', 'socioeconomic_population', 'Both district blocks'))
    for rule, rule_label in RULE_LABELS.items():
        for a, b, label in additions:
            contrast(f'Add {label} | {rule_label}', label.split(' after ')[0] if 'after' in label
                     else label, (rule, a), (rule, b))
    for tier in FEATURES[1:]:
        contrast(f'Newest and {LABELS[tier][2:]} from ranked facility-only', 'Source and features',
                 ('ranked', 'facility_only'), ('newest', tier))
    contrast('Ranked + population → newest + NFHS', 'Source and features',
             ('ranked', 'population'), ('newest', 'socioeconomic'))

    for a, b, label in additions[:2] + additions[2:4]:
        added_ranked = error['ranked', b] - error['ranked', a]
        added_newest = error['newest', b] - error['newest', a]
        contrasts.append({'training_policy': trained, 'contrast': f'Source × {label}', 'factor': 'Interaction',
                          **paired_interval(added_ranked, added_newest, clusters)})

    both = frame.loc[test, 'nfhs_matched'] & frame.loc[test, 'population_matched']
    selectors = {
        'All test facilities': np.ones(len(actual), dtype=bool),
        'Under 50 beds': actual < 50, '50–200 beds': (actual >= 50) & (actual <= 200), 'Over 200 beds': actual > 200,
        'Both district blocks available': both.to_numpy(),
        'One or both district blocks missing': (~both).to_numpy(),
    }
    slices = []
    for name, mask in selectors.items():
        if mask.sum() < 15:
            continue
        for rule, tier in [('ranked', 'population'), ('newest', 'socioeconomic'), ('raw_fdr', 'facility_only')]:
            arm_id = f'{RULES.get(rule, rule)}__{tier}'
            metrics = score(actual[mask], predicted.loc[mask, arm_id].to_numpy())
            slices.append({'training_policy': trained, 'slice': name, 'rule': rule, 'features': tier,
                           'n': metrics['n'], 'mae': metrics['mae'], 'mean_bias': metrics['mean_bias']})
    return {'arms': records, 'contrasts': contrasts, 'slices': slices, 'winner': data['winner'],
            'holdout': data['holdout']}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    inputs, archived_path = args.archive / 'inputs', args.archive / 'training/metrics.json'
    archived = json.loads(archived_path.read_text())
    frame, _ = load_inputs(inputs)
    masks, audit = preflight(inputs, archived, frame)
    args.output.mkdir(parents=False, exist_ok=False)
    strict_inputs = args.output / 'strict_inputs'
    strict_rows_excluded = build_strict_input(inputs, strict_inputs, frame)
    print(f'Preflight passed. Holding {strict_rows_excluded} training rows flagged for bed-count disagreement.', flush=True)

    start = monotonic()
    results = {}
    for policy, directory, strict in [('original_eligibility', inputs, False),
                                      ('exclude_disputed_targets', strict_inputs, True)]:
        output = args.output / policy
        run_experiment(directory, output, rule_cleaning=True, exclude_disputed_evaluation=strict)
        results[policy] = paired_results(frame, masks['test'], policy, output)
        for name, mask in masks.items():
            expected = mask & ~frame.capacity_source_disagreement if strict else mask
            got = results[policy]['holdout'][name]
            if got['sha256_facility_and_observed_count'] != fingerprint(frame, expected) or got['n'] != int(expected.sum()):
                raise ValueError(f'A rerun unexpectedly changed the {name} cohort.')
        print(f'{policy} done. Selected {results[policy]["winner"]["release_run_id"]}.', flush=True)
    audit['strict_training_flags_changed'] = strict_rows_excluded
    audit['training_seconds'] = round(monotonic() - start, 3)
    audit['prior_test_reused'] = True
    archive_predictions = args.archive / 'training/test_predictions.csv'
    new_predictions = args.output / 'original_eligibility/test_predictions.csv'
    audit['same_test_predictions_as_archive'] = receipt(archive_predictions) == receipt(new_predictions)
    fit = frame.split.eq('train') & frame.training_eligible
    test_frame = frame.loc[masks['test']]
    audit['test_predictor_coverage'] = {
        'nfhs': int(test_frame.nfhs_matched.sum()),
        'population': int(test_frame.population_matched.sum()),
        'both': int((test_frame.nfhs_matched & test_frame.population_matched).sum()),
        'district_seen_in_training': int(np.isin(district_clusters(test_frame), district_clusters(frame.loc[fit])).sum()),
    }
    write_json(args.output / 'aggregate-results.json', {'audit': audit, 'results': results})


if __name__ == '__main__':
    main()
