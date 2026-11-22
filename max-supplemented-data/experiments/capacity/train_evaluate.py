"""Fit and test a matched-cohort XGBoost capacity experiment on local data.

The assembler supplies a single row per reconciled facility with its frozen FDR
partition, candidate training labels, field holds, and allowlisted feature blocks.
There is no use of a selected/reconstructed label for model evaluation.
"""

from argparse import ArgumentParser
from collections import Counter
from hashlib import file_digest, sha256
from itertools import combinations
import json
from pathlib import Path
from time import monotonic

import numpy as np
import pandas as pd
import xgboost as xgb


ITERATIONS = ('iteration_01', 'iteration_02')
SPECS = {
    'facility_only': ('facility',),
    'socioeconomic': ('facility', 'socioeconomic'),
    'socioeconomic_population': ('facility', 'socioeconomic', 'population'),
}
RULE_SPECS = {**SPECS, 'population': ('facility', 'population')}
PUBLISHED_METHODS = {'fdr_reported', 'fdr_reported_contested', 'direct_review', 'approved_source_recovery',
                     'dated_external_report', 'undated_external_report', 'explicit_fdr_description',
                     'latest_year_mean', 'recent_two_year_mean'}
SEED = 217
ROUNDS = 300
PATIENCE = 30
ALPHA = .10
MIN_SLICE = 15
BOOTSTRAP_DRAWS = 1500
GRID = tuple({'scale': scale, 'depth': depth} for scale in ('raw_mae', 'log1p_squared') for depth in (3, 5))


def load_inputs(directory):
    with (directory / 'facilities.jsonl').open(encoding='utf8') as handle:
        frame = pd.DataFrame(json.loads(line) for line in handle if line.strip())
    groups = json.loads((directory / 'feature_groups.json').read_text(encoding='utf8'))
    iterations = tuple(groups.get('source_policies', ITERATIONS))
    selected_columns = {f'capacity_{i}' for i in iterations}
    methods = {f'capacity_method_{i}' for i in iterations}
    imputation_masks = {f'imputation_eligible_{i}' for i in iterations}
    filled_doctors = {f'numberDoctors_{i}' for i in iterations}
    required = {'facility_id', 'split', 'capacity_observed', 'training_eligible', 'evaluation_eligible',
                'state', 'district', *selected_columns, *methods, *imputation_masks, *filled_doctors}
    if not required.issubset(frame):
        raise ValueError(f'Assembler columns missing: {sorted(required - set(frame))}')
    if frame.facility_id.isna().any() or frame.facility_id.duplicated().any() or frame.facility_id.eq('').any():
        raise ValueError('Assembler must return unique, nonempty branch IDs.')
    for flag in ('training_eligible', 'evaluation_eligible', *imputation_masks):
        if not frame[flag].isin([True, False]).all():
            raise ValueError(f'{flag} must be a known Boolean for every branch.')

    allowed = []
    for block in ('facility', 'socioeconomic', 'population'):
        features = groups[block]
        if not isinstance(features, list) or len(set(features)) != len(features):
            raise ValueError(f'{block} must be an ordered feature list with unique names.')
        allowed.extend(features)
    blocked_tokens = ('capacity', 'bed_count', 'bedcount', 'target', 'verified_value', 'prediction', 'source_selector',
                      'source_score', 'imputation', 'split', 'facility_id', 'review_result')
    if (not groups['facility'] or len(allowed) != len(set(allowed)) or set(allowed) - set(frame)
            or set(allowed) & required
            or any(token in field.casefold() for field in allowed for token in blocked_tokens)):
        raise ValueError('Feature contract has an empty base, duplicate/missing columns or target-derived columns.')
    types = groups['feature_types']
    if set(allowed) - set(types) or any(types[field] not in {'numeric', 'categorical'} for field in allowed):
        raise ValueError('All features must have a declared numeric or categorical type.')

    frame = frame.sort_values('facility_id', kind='stable').reset_index(drop=True)
    frame = frame.set_index('facility_id', drop=False)
    for field in ('capacity_observed', *selected_columns, *filled_doctors):
        values = pd.to_numeric(frame[field], errors='raise')
        observed = values.dropna().to_numpy(dtype=float)
        if not (np.isfinite(observed) & (observed >= 0) & (observed == np.floor(observed))).all():
            raise ValueError(f'{field} must contain missing or finite nonnegative whole counts.')
        frame[field] = values
    return frame, groups


def split_masks(frame):
    """Freeze the common reported-only test set and split old calibration in two."""
    evaluate = frame.evaluation_eligible.eq(True) & frame.capacity_observed.notna()
    test = evaluate & frame.split.eq('test')
    calibration = evaluate & frame.split.eq('calibration')
    if min(test.sum(), calibration.sum()) < 4:
        raise ValueError('Need reported labels in both saved calibration and test partitions.')
    # Predeclare this second split from the branch key only. Neither labels nor
    # predictions decide whether a row can tune the model or calibrate intervals.
    candidates = sorted(frame.index[calibration], key=lambda key: sha256(f'capacity-v1:{key}'.encode()).digest())
    tune = frame.index.isin(candidates[::2])
    interval = frame.index.isin(candidates[1::2])
    return tune, interval, test.to_numpy()


def fingerprint(frame, mask):
    rows = frame.loc[mask, ['facility_id', 'capacity_observed']]
    content = ''.join(f'{branch}:{int(value)}\n' for branch, value in rows.itertuples(index=False, name=None))
    return sha256(content.encode()).hexdigest()


def receipt(path):
    with path.open('rb') as file:
        return file_digest(file, 'sha256').hexdigest()


def fit_encoding(frame, fields, types, aliases):
    # Code one hot levels using only the training facilities. The serialized
    # levels make the same transformation available in batch inference.
    levels = {field: sorted(frame[aliases.get(field, field)].dropna().astype(str).unique()) for field in fields
              if types[field] == 'categorical'}
    if any(len(values) > 80 for values in levels.values()):
        raise ValueError('Categorical feature has high cardinality. Check for an identity or raw text column.')
    return {'fields': fields, 'levels': levels, 'field_aliases': aliases}


def encoder_matrix(frame, encoding):
    columns = []
    for field in encoding['fields']:
        source = encoding['field_aliases'].get(field, field)
        if field in encoding['levels']:
            values = frame[source].astype('string')
            columns.extend(values.eq(level).fillna(False).to_numpy(dtype=np.float32)
                           for level in encoding['levels'][field])
            columns.append(values.isna().to_numpy(dtype=np.float32))
        else:
            values = pd.to_numeric(frame[source], errors='raise').to_numpy(dtype=np.float32, na_value=np.nan)
            columns.append(np.where(np.isfinite(values), values, np.nan))
    return np.column_stack(columns)


def convert_beds(prediction, scale):
    values = np.expm1(prediction.astype(float)) if scale == 'log1p_squared' else prediction.astype(float)
    if not np.isfinite(values).all():
        raise ValueError('XGBoost produced nonfinite bed values.')
    return np.maximum(1, np.rint(values))


def model_predictions(model, matrix, scale):
    return convert_beds(model.predict(xgb.DMatrix(matrix, nthread=2),
                                      iteration_range=(0, model.best_iteration + 1)), scale)


def score(actual, predicted):
    residual = predicted - actual
    errors = np.abs(residual)
    variance = np.sum((actual - actual.mean()) ** 2)
    return {'n': len(actual), 'mae': float(np.mean(errors)), 'median_ae': float(np.median(errors)),
            'p90_ae': float(np.quantile(errors, .90)), 'rmse': float(np.sqrt(np.mean(residual ** 2))),
            'r2': float(1 - np.sum(residual ** 2) / variance) if variance > 0 else None,
            'mean_bias': float(residual.mean()), 'max_ae': float(errors.max())}


def fit_candidate(config, x_train, y_train, x_tune, y_tune):
    scale = config['scale']
    train_label = np.log1p(y_train) if scale == 'log1p_squared' else y_train
    tune_label = np.log1p(y_tune) if scale == 'log1p_squared' else y_tune
    train = xgb.DMatrix(x_train, label=train_label, nthread=2)
    tune = xgb.DMatrix(x_tune, label=tune_label, nthread=2)

    def mae_beds(prediction, matrix):
        label = matrix.get_label()
        actual = np.rint(np.expm1(label.astype(float))) if scale == 'log1p_squared' else label
        return 'mae_beds', float(np.mean(np.abs(convert_beds(prediction, scale) - actual)))

    start = monotonic()
    history = {}
    parameters = {
        'objective': 'reg:squarederror' if scale == 'log1p_squared' else 'reg:absoluteerror', 'tree_method': 'hist',
         'max_depth': config['depth'], 'eta': .07, 'min_child_weight': 5, 'subsample': .90,
        'colsample_bytree': .90, 'lambda': 3, 'nthread': 2, 'seed': SEED, 'disable_default_eval_metric': 1,
    }
    model = xgb.train(
        parameters,
        train, num_boost_round=ROUNDS, evals=[(train, 'train'), (tune, 'validation')], evals_result=history,
        custom_metric=mae_beds, maximize=False, early_stopping_rounds=PATIENCE, verbose_eval=False)
    prediction = model_predictions(model, x_tune, scale)
    return model, {**config, 'best_round': model.best_iteration + 1, 'elapsed_seconds': round(monotonic() - start, 3),
                   'validation': score(y_tune, prediction), 'history': history, 'parameters': parameters}


def calibrated_radius(actual, predicted):
    error = np.sort(np.abs(actual - predicted))
    rank = int(np.ceil((len(error) + 1) * (1 - ALPHA)))
    if rank > len(error):
        raise ValueError('Insufficient untouched calibration labels for the requested interval level.')
    return float(error[rank - 1])


def interval_metrics(actual, predicted, radius):
    lower = np.maximum(0, predicted - radius)
    upper = predicted + radius
    return {'coverage': float(np.mean((actual >= lower) & (actual <= upper))),
            'median_width': float(np.median(upper - lower))}


def test_subgroups(frame, actual, predicted, median, radius, groups, iteration, seen_districts):
    selectors = {'state': frame.state.fillna('unknown').astype(str).to_numpy(),
                 'actual_bed_band': np.select([actual < 50, actual <= 200], ['0–49', '50–200'], default='201+'),
                 'reported_doctor_count': np.where(frame['numberDoctors'].notna(), 'present', 'missing')
                 if 'numberDoctors' in frame else np.repeat('unavailable', len(frame)),
                 'filled_doctor_count': np.where(frame[f'numberDoctors_{iteration}'].notna(), 'present', 'missing')}
    clusters = district_clusters(frame)
    selectors['district_seen_in_training'] = np.where(np.char.startswith(clusters, 'facility:'), 'unknown',
                                                       np.where(np.isin(clusters, sorted(seen_districts)), 'yes', 'no'))
    for block in ('socioeconomic', 'population'):
        if groups[block]:
            present = frame[groups[block]].notna().any(axis=1)
            selectors[f'{block}_coverage'] = np.where(present, 'present', 'missing')
    result, suppressed = {}, {}
    for dimension, values in selectors.items():
        result[dimension], suppressed[dimension] = {}, 0
        for label in sorted(set(values)):
            select = values == label
            if select.sum() < MIN_SLICE:
                suppressed[dimension] += 1
                continue
            result[dimension][label] = {**score(actual[select], predicted[select]),
                                        **interval_metrics(actual[select], predicted[select], radius),
                                        'median_baseline_mae': float(np.mean(np.abs(actual[select] - median)))}
    return {'metrics': result, 'minimum_n': MIN_SLICE, 'suppressed_groups': suppressed}


def paired_interval(reference_error, contender_error, clusters):
    """District-cluster bootstrap of the per-facility difference in MAE."""
    _, labels = np.unique(clusters, return_inverse=True)
    changes = contender_error - reference_error
    sums = np.bincount(labels, weights=changes)
    counts = np.bincount(labels)
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(counts), (BOOTSTRAP_DRAWS, len(counts)))
    samples = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return {'delta_mae': float(changes.mean()), 'lower_95': float(np.quantile(samples, .025)),
            'upper_95': float(np.quantile(samples, .975)), 'clusters': len(counts), 'draws': BOOTSTRAP_DRAWS}


def district_clusters(frame):
    # Missing geography supplies no basis for assigning that branch to a cluster.
    return np.array([f'{state}|{district}' if pd.notna(state) and pd.notna(district) and state and district
                     else f'facility:{identity}' for identity, state, district in
                     frame[['facility_id', 'state', 'district']].itertuples(index=False, name=None)])


def fit_arm(frame, groups, iteration, feature_set, fields, tune, interval, test):
    target = frame[f'capacity_{iteration}']
    method = frame[f'capacity_method_{iteration}']
    train = frame.split.eq('train') & frame.training_eligible.eq(True) & target.notna()
    if not method.loc[train].isin(PUBLISHED_METHODS).all():
        raise ValueError('A training target has an unsupported or estimated provenance.')
    if min(train.sum(), tune.sum(), interval.sum(), test.sum()) < 10 or (train & (tune | interval | test)).any():
        raise ValueError('Training and held-out branch partitions must be disjoint and supplied.')
    aliases = {'numberDoctors': f'numberDoctors_{iteration}'} if 'numberDoctors' in fields else {}
    encoding = fit_encoding(frame.loc[train], fields, groups['feature_types'], aliases)
    matrix = encoder_matrix(frame, encoding)
    observed = frame.capacity_observed.to_numpy(dtype=float)
    y_train, y_tune = target.loc[train].to_numpy(dtype=float), observed[tune]
    contenders = [fit_candidate(config, matrix[train], y_train, matrix[tune], y_tune) for config in GRID]
    model, chosen = min(contenders, key=lambda candidate: (candidate[1]['validation']['mae'],
                                                          candidate[1]['depth'], candidate[1]['best_round']))
    radius = calibrated_radius(observed[interval], model_predictions(model, matrix[interval], chosen['scale']))
    run_id = f'{iteration}__{feature_set}'
    record = {
        'run_id': run_id, 'iteration': iteration, 'feature_set': feature_set, 'features': fields,
        'model_file': f'{run_id}.ubj', 'encoding_file': f'{run_id}_encoding.json',
        'counts': {'training': int(train.sum()), 'validation': int(tune.sum()),
                   'interval_calibration': int(interval.sum()), 'test': int(test.sum())},
        'y_sources': dict(Counter(method.loc[train])), 'tuning': [details for _, details in contenders],
        'training_targets': {'min': int(y_train.min()), 'max': int(y_train.max())},
        'chosen_config': {key: chosen[key] for key in ('scale', 'depth', 'best_round')},
        'validation': chosen['validation'], 'interval': {'nominal': 1 - ALPHA, 'radius': radius},
        'feature_nonmissing': {part: {field: int(frame.loc[mask, aliases.get(field, field)].notna().sum())
                                      for field in fields}
                               for part, mask in [('train', train), ('validation', tune), ('test', test)]},
        'baseline': {'training_median_beds': max(1.0, float(np.rint(np.median(y_train))))},
    }
    return record, model, encoding, matrix, set(district_clusters(frame.loc[train]))


def write_rule_dataset(frame, run, tune, interval, test, output_dir):
    iteration, fields = run['iteration'], run['features']
    aliases = {'numberDoctors': f'numberDoctors_{iteration}'}
    selected = frame[f'capacity_{iteration}']
    train = frame.split.eq('train') & frame.training_eligible.eq(True) & selected.notna()
    keep = tune | interval | test
    dataset = frame[[aliases.get(field, field) for field in fields]].copy()
    dataset.columns = fields
    dataset.insert(0, 'facility_id', frame.facility_id)
    dataset['role'] = np.select([train, tune, interval, test], ['training', 'validation', 'interval_calibration', 'test'],
                                default='unused')
    dataset['capacity_selected'] = selected.astype('Int64')
    dataset['capacity_selected_method'] = frame[f'capacity_method_{iteration}']
    dataset['capacity_target'] = selected.where(train).where(~keep, frame.capacity_observed).astype('Int64')
    if dataset['role'].value_counts().drop('unused', errors='ignore').to_dict() != run['counts']:
        raise ValueError(f'The projection differs from the fitted cohort for {run["run_id"]}.')
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'{run["run_id"]}.csv'
    dataset.to_csv(path, index=False)
    return {'filename': path.name, 'sha256': receipt(path), 'facility_rows': len(dataset), 'feature_fields': fields,
            'cohort_counts': run['counts']}


def run_experiment(input_dir, output_dir, *, rule_cleaning=False, exclude_disputed_evaluation=False):
    if output_dir.exists():
        raise FileExistsError(f'Choose a fresh results directory: {output_dir}')
    frame, groups = load_inputs(input_dir)
    plan = {iteration: SPECS for iteration in ITERATIONS}
    policies = groups.get('source_policies', {'iteration_01': 'ranked', 'iteration_02': 'latest_year_mean'})
    if rule_cleaning:
        if any(not groups[block] for block in ('socioeconomic', 'population')):
            raise ValueError('The rule-cleaning comparison requires both enrichment blocks.')
        if (policies.get('iteration_01') != 'ranked' or policies.get('iteration_02') != 'latest_year_mean'
                or len(set(policies.values())) != len(policies)):
            raise ValueError('Prepared rule datasets must identify their distinct source policies.')
        frame['capacity_raw_fdr'] = frame['capacity_observed']
        frame['capacity_method_raw_fdr'] = np.where(frame.capacity_observed.notna(), 'fdr_reported', 'missing')
        frame['numberDoctors_raw_fdr'] = frame['numberDoctors']
        plan = {iteration: RULE_SPECS for iteration in policies}
        plan['raw_fdr'] = {'facility_only': SPECS['facility_only']}
    tune, interval, test = split_masks(frame)
    if exclude_disputed_evaluation:
        uncontested = ~frame.capacity_source_disagreement.to_numpy(dtype=bool)
        tune, interval, test = tune & uncontested, interval & uncontested, test & uncontested
    outcome = {'runs': [], 'skipped': [], 'holdout': {}, 'comparison': {}, 'xgboost_version': xgb.__version__,
               'seed': SEED, 'population_status': groups.get('population_status'), 'max_rounds': ROUNDS,
               'early_stopping_patience': PATIENCE, 'point_predictions': 'round to nearest whole bed; minimum 1',
               'input_sha256': {name: receipt(input_dir / name) for name in ('facilities.jsonl', 'feature_groups.json')}}
    for part, mask in [('validation', tune), ('interval_calibration', interval), ('test', test)]:
        targets = frame.loc[mask, 'capacity_observed']
        outcome['holdout'][part] = {'n': int(mask.sum()), 'min_capacity': int(targets.min()),
                                   'max_capacity': int(targets.max()),
                                   'sha256_facility_and_observed_count': fingerprint(frame, mask)}

    # Lock every fitted arm and the validation champion before testing.
    artifacts = {}
    for iteration, specifications in plan.items():
        for name, blocks in specifications.items():
            missing_block = next((block for block in blocks if not groups[block]), None)
            if missing_block:
                outcome['skipped'].append({'iteration': iteration, 'feature_set': name,
                                           'reason': f'No authenticated {missing_block} predictors.'})
                continue
            fields = [field for block in blocks for field in groups[block]]
            record, model, encoding, matrix, districts = fit_arm(
                frame, groups, iteration, name, fields, tune, interval, test)
            artifacts[record['run_id']] = (model, encoding, matrix, districts)
            outcome['runs'].append(record)
    clean_runs = [run for run in outcome['runs'] if run['iteration'] != 'raw_fdr']
    validation_winner = min(clean_runs, key=lambda run: (run['validation']['mae'], len(run['features']), run['run_id']))

    actual_test = frame.loc[test, 'capacity_observed'].to_numpy(dtype=float)
    clusters = district_clusters(frame.loc[test])
    all_errors, test_outputs = {}, []
    for run in outcome['runs']:
        model, _, matrix, districts = artifacts[run['run_id']]
        test_prediction = model_predictions(model, matrix[test], run['chosen_config']['scale'])
        baseline = run['baseline']['training_median_beds']
        run['test'] = score(actual_test, test_prediction)
        run['baseline']['test'] = score(actual_test, np.full(len(actual_test), baseline))
        run['interval'].update(interval_metrics(actual_test, test_prediction, run['interval']['radius']))
        run['subgroups'] = test_subgroups(frame.loc[test], actual_test, test_prediction,
                                          baseline, run['interval']['radius'], groups, run['iteration'], districts)
        all_errors[run['run_id']] = np.abs(actual_test - test_prediction)
        run['baseline']['test_mae_difference_model_minus_median'] = paired_interval(
            np.abs(actual_test - baseline), all_errors[run['run_id']], clusters)
        test_outputs.append(pd.DataFrame({'facility_id': frame.index[test], 'capacity_observed': actual_test,
                                          'capacity_predicted': test_prediction.astype(int), 'run_id': run['run_id']}))
    test_winner = min(clean_runs, key=lambda run: (run['test']['mae'], len(run['features']), run['run_id']))
    winner = validation_winner if rule_cleaning else test_winner
    outcome['winner'] = {'release_run_id': winner['run_id'],
                         'release_selected_on': 'validation_mae' if rule_cleaning else 'locked_test_mae',
                         'validation_winner_run_id': validation_winner['run_id'],
                         'lowest_test_mae_run_id': test_winner['run_id'], 'refit_after_test': False,
                         'validation_winner_also_lowest_test_mae': test_winner is validation_winner}
    outcome['comparison']['test_mae_difference_run_minus_release'] = {
        run['run_id']: paired_interval(all_errors[winner['run_id']], all_errors[run['run_id']], clusters)
        for run in outcome['runs'] if run is not winner}
    policy_iterations = tuple(policies) if rule_cleaning else ITERATIONS
    tiers = RULE_SPECS if rule_cleaning else SPECS
    feature_pairs = [('facility_only', 'socioeconomic'), ('socioeconomic', 'socioeconomic_population')]
    if rule_cleaning:
        feature_pairs += [('facility_only', 'population'), ('population', 'socioeconomic_population')]
    contrasts = [(f'{iteration}__{a}', f'{iteration}__{b}') for iteration in policy_iterations for a, b in feature_pairs]
    contrasts.extend((f'{a}__{name}', f'{b}__{name}') for a, b in combinations(policy_iterations, 2) for name in tiers)
    if rule_cleaning:
        contrasts.extend(('raw_fdr__facility_only', run['run_id']) for run in clean_runs)
        outcome['source_policies'] = {**policies, 'raw_fdr': 'original_reported_no_supplementation'}
    outcome['comparison']['planned_test_contrasts'] = [
        {'reference_run_id': reference, 'contender_run_id': contender,
         **paired_interval(all_errors[reference], all_errors[contender], clusters)}
        for reference, contender in contrasts if reference in all_errors and contender in all_errors]

    iteration = winner['iteration']
    imputable = (frame[f'imputation_eligible_{iteration}'].eq(True) & frame[f'capacity_{iteration}'].isna()
                 & frame.capacity_observed.isna()).to_numpy()
    model, encoding, matrix, _ = artifacts[winner['run_id']]
    radius = winner['interval']['radius']
    prediction = model_predictions(model, matrix[imputable], winner['chosen_config']['scale']) if imputable.any() else np.array([])
    imputation = pd.DataFrame({'facility_id': frame.index[imputable], 'capacity_predicted': prediction.astype(int),
                               'capacity_lower': np.maximum(0, prediction - radius).astype(int),
                               'capacity_upper': (prediction + radius).astype(int), 'run_id': winner['run_id']})
    outcome['winner']['imputation_count'] = len(imputation)
    outcome['winner']['imputation_summary'] = ({'median_beds': float(np.median(prediction)),
                                               'p90_beds': float(np.quantile(prediction, .9)),
                                               'maximum_beds': int(prediction.max())} if len(prediction) else {})

    output_dir.mkdir(parents=True, exist_ok=True)
    for run in outcome['runs']:
        model, encoding, _, _ = artifacts[run['run_id']]
        model.save_model(output_dir / run['model_file'])
        (output_dir / run['encoding_file']).write_text(json.dumps(encoding, indent=2) + '\n', encoding='utf8')
        if rule_cleaning:
            run['dataset_projection'] = write_rule_dataset(frame, run, tune, interval, test, output_dir / 'datasets')
    imputation.to_csv(output_dir / 'imputation_predictions.csv', index=False)
    pd.concat(test_outputs).to_csv(output_dir / 'test_predictions.csv', index=False)
    (output_dir / 'metrics.json').write_text(json.dumps(outcome, indent=2, allow_nan=False) + '\n', encoding='utf8')
    return outcome['winner']


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path, help='Authenticated feature assembly output directory.')
    parser.add_argument('--output-dir', required=True, type=Path, help='Private model results and predictions directory.')
    parser.add_argument('--rule-cleaning', action='store_true',
                        help='Fit four feature tiers per deterministic policy and the raw FDR control.')
    args = parser.parse_args()
    print(json.dumps(run_experiment(args.input_dir, args.output_dir, rule_cleaning=args.rule_cleaning), indent=2))
