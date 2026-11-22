"""Compare missing-count estimators on the existing FDR facility partitions."""

from datetime import date
from math import ceil

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .eda import COUNT_UNITS, UPDATE_COLUMNS


CATEGORY_NAMES = {
    'facilityTypeId': {**{key: key for key in ('hospital', 'clinic', 'dentist', 'doctor', 'pharmacy')},
                       'farmacy': 'pharmacy'},
    'operatorTypeId': {'public': 'public', 'private': 'private', 'government': 'public'},
}
# Closed allowlist: contacts and any phone-derived fields are display-only.
CANDIDATE_FIELDS = (*CATEGORY_NAMES, *COUNT_UNITS)
MODELS = {'regression': 'log1p ridge', 'ml': 'log1p random forest'}
SPLITS = ('train', 'calibration', 'test')
INTERVAL_LEVEL = 0.9
MIN_SUPPORT = (40, 30, 30)


def _as_count(values):
    numeric = pd.to_numeric(values, errors='coerce').astype(float)
    return numeric.where(np.isfinite(numeric) & numeric.between(0, 2**53) & numeric.mod(1).eq(0) &
                         ~values.map(pd.api.types.is_bool))


def _nonnumeric_status(value):
    return str(value).strip().casefold() if pd.notna(value) else '(missing)'


def _audit_rows(target, audit, other_audit, original_columns):
    schema = list(original_columns)
    if 'countries' not in schema or len(set(schema)) != len(schema):
        raise ValueError('Supply the unique original FDR schema in order, including countries.')
    after_boundary = schema[schema.index('countries') + 1:]
    fields = [field for field in CANDIDATE_FIELDS if field in after_boundary and field != target]
    if 'facilityTypeId' not in fields:
        raise ValueError('The audit needs facilityTypeId among the eligible post-countries fields.')
    required = {'facility_id', 'status', 'value', 'split', *(field for field in fields if field in CATEGORY_NAMES)}
    if not required.issubset(audit.columns) or audit['facility_id'].duplicated().any():
        raise ValueError('Run notebook 03 for this snapshot to produce a unique facility target audit.')
    rows = audit[['facility_id', 'split', 'status', 'value', *[f for f in fields if f in CATEGORY_NAMES]]].reset_index(drop=True)
    if not rows['split'].isin(SPLITS).all():
        raise ValueError('Use the saved train, calibration and test partitions from notebook 03.')
    rows['value'] = _as_count(rows['value']).where(rows['status'].eq('reported'))
    if rows.loc[rows['status'].eq('reported'), 'value'].isna().any():
        raise ValueError('Reported target values must be finite, nonnegative integer counts.')
    reports = []
    for field in (name for name in fields if name in CATEGORY_NAMES):
        original = rows[field].map(_nonnumeric_status)
        mapped = original.map(CATEGORY_NAMES[field])
        status = pd.Series('recognized', index=rows.index).where(mapped.notna(), 'unrecognized')
        status = status.mask(original.isin(['(missing)', '', 'unknown', 'none', 'nan']), 'missing')
        status = status.mask(original.eq('(conflicting)'), 'conflicting')
        counts = pd.crosstab(rows['split'], status).reindex(SPLITS, fill_value=0)
        for split, values in counts.iterrows():
            reports.extend((target, field, split, key, int(n)) for key, n in values.items())
        rows[field] = mapped.fillna('(unknown)')
        rows[field + '_bad_category'] = status.isin(['unrecognized', 'conflicting'])
    other = next(field for field in COUNT_UNITS if field != target)
    rows['other_count_reported'] = False
    if other in fields:
        keys = other_audit[['facility_id', 'split']]
        if not rows[['facility_id', 'split']].set_index('facility_id').sort_index().equals(keys.set_index('facility_id').sort_index()):
            raise ValueError('Both targets must use exactly the same facility IDs and partitions.')
        exact = other_audit.loc[other_audit['status'].eq('reported')].set_index('facility_id')['value']
        rows[other] = _as_count(rows['facility_id'].map(exact))
        rows['other_count_reported'] = rows[other].notna()
        rows[other] = np.log1p(rows[other])
    return rows, fields, reports


def _estimators(seed):
    return {'regression': Ridge(alpha=10.0),
            'ml': RandomForestRegressor(n_estimators=200, min_samples_leaf=15, n_jobs=1, random_state=seed)}


def _transformer(fields):
    categories = [field for field in fields if field in CATEGORY_NAMES]
    counts = [field for field in fields if field in COUNT_UNITS]
    columns = []
    if categories:
        columns.append(('category', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categories))
    if counts:
        columns.append(('reported_count', SimpleImputer(strategy='median', keep_empty_features=True), counts))
        columns.append(('reported_flag', 'passthrough', ['other_count_reported']))
    return ColumnTransformer(columns)


def _fit(matrix, train, seed):
    predictors = matrix.transform(train)
    target = np.log1p(train['value'].to_numpy(dtype=float))
    return {name: model.fit(predictors, target) for name, model in _estimators(seed).items()}


def _point(model, predictors):
    return np.maximum(0, np.rint(np.expm1(model.predict(predictors))))


def _candidate_evidence(target, train, candidates):
    evidence = []
    for field in candidates:
        if field in CATEGORY_NAMES:
            known = train.loc[train[field].ne('(unknown)')]
            levels = known.groupby(field)['value'].agg(['size', 'median'])
            medians = levels.loc[levels['size'].ge(10), 'median']
            eligible = len(medians) >= 2 and medians.nunique() >= 2 and len(known) >= .05 * len(train)
            detail = f'{len(medians)} type levels with ≥10 labels; {medians.nunique()} distinct target medians'
        else:
            known = train.loc[train[field].notna()]
            association = known[field].rank().corr(known['value'].rank()) if len(known) >= 3 else np.nan
            agreement = np.isclose(np.expm1(known[field]), known['value']).mean() if len(known) else np.nan
            eligible = (len(known) >= max(30, .05 * len(train)) and abs(association) >= .10 and agreement < .95)
            detail = f'Paired-label Spearman {association:.3f}; exact agreement {agreement:.1%}'
        evidence.append(dict(field=target, candidate=field, known_train_labels=len(known),
                             usable_fraction=len(known) / len(train), passes_eda=bool(eligible), reason=detail))
    return pd.DataFrame(evidence)


def _cross_validate(train, fields, folds, seed):
    errors = {method: [] for method in MODELS}
    for train_index, check_index in folds:
        fit = train.iloc[train_index]
        check = train.iloc[check_index]
        if fields:
            matrix = _transformer(fields).fit(fit)
            models = _fit(matrix, fit, seed)
            held = matrix.transform(check)
            points = {method: _point(model, held) for method, model in models.items()}
        else:
            points = dict.fromkeys(MODELS, np.rint(fit['value'].median()))
        for method, point in points.items():
            errors[method].append(float(np.abs(check['value'].to_numpy() - point).mean()))
    return {method: np.asarray(values) for method, values in errors.items()}


def _select_features(target, train, candidates, seed):
    """Use only training labels for a common, stable predictor set."""
    ordered = train.sort_values('facility_id')
    folds = list(KFold(n_splits=5, shuffle=True, random_state=seed).split(ordered))
    evidence = _candidate_evidence(target, ordered, candidates)
    remaining = list(evidence.loc[evidence['passes_eda'], 'candidate'])
    selected, trials, fold_scores = [], [], []
    baseline = _cross_validate(ordered, [], folds, seed)
    while remaining:
        round_trials = []
        for field in remaining:
            added = _cross_validate(ordered, [*selected, field], folds, seed)
            index = len(trials)
            gains = []
            for method in MODELS:
                old, new = baseline[method], added[method]
                gain = (old.mean() - new.mean()) / old.mean() if old.mean() else 0.0
                wins = int((new < old).sum())
                gains.append(gain if wins >= 4 else -np.inf)
                for fold, (before, after) in enumerate(zip(old, new), 1):
                    fold_scores.append((target, len(selected) + 1, field, method, fold, before, after))
                trials.append({'field': target, 'step': len(selected) + 1, 'candidate': field, 'method': method,
                               'existing_features': ', '.join(selected) or 'training median',
                               'previous_mean_fold_mae': old.mean(), 'added_mean_fold_mae': new.mean(),
                               'relative_mae_gain': gain, 'improved_folds': wins, 'included_at_step': False})
            if min(gains) >= .01:
                round_trials.append((float(np.mean(gains)), field, added, index))
        if not round_trials:
            break
        _, winner, baseline, index = max(round_trials, key=lambda entry: entry[0])
        for row in trials[index:index + len(MODELS)]:
            row['included_at_step'] = True
        selected.append(winner)
        remaining.remove(winner)
    evidence['selected'] = evidence['candidate'].isin(selected)
    columns = ['field', 'step', 'candidate', 'method', 'fold', 'previous_mae', 'added_mae']
    return selected, evidence, pd.DataFrame(trials), pd.DataFrame(fold_scores, columns=columns)


def _radius(y, point):
    scores = np.abs(np.log1p(y) - np.log1p(point))
    order = ceil((len(scores) + 1) * INTERVAL_LEVEL)
    return np.partition(scores, order - 1)[order - 1] if 1 <= order <= len(scores) else None


def _bounds(pred, radius):
    if radius is None:
        return np.full_like(pred, np.nan), np.full_like(pred, np.nan)
    center = np.log1p(pred)
    return np.maximum(0, np.floor(np.expm1(center - radius))), np.ceil(np.expm1(center + radius))


def _metrics(target, method, run, true, point, low, high, tail):
    errors = np.abs(point - true)
    covered = (true >= low) & (true <= high)
    has_interval = len(low) > 0 and np.isfinite(low).all()
    return {'field': target, 'method': method, 'run': run, 'test_facilities': len(true),
            'mae': errors.mean(), 'median_absolute_error': np.median(errors), 'p90_absolute_error': np.quantile(errors, .9),
            'tail_threshold': tail, 'tail_facilities': int((true >= tail).sum()),
            'tail_mae': errors[true >= tail].mean() if (true >= tail).any() else np.nan,
            'coverage': covered.mean() if has_interval else np.nan,
            'median_interval_width': np.median(high - low) if has_interval else np.nan}


def fit_count_models(target, audit, other_audit, original_columns, *, as_of, seed=42):
    """Fit and evaluate two fixed log-count models and a training-median baseline.

    `audit` and `other_audit` are notebook 03 target_audit tables for the same
    linked facilities. Reported numeric tails stay in the primary fit and all
    test metrics. The sensitivity fit drops >training-p99 labels from training
    only, keeping the same preprocessing, calibration and test cohorts.
    Each returned primary pipeline predicts log(1 + count) from prepared inputs.
    """
    if target not in COUNT_UNITS:
        raise ValueError('Choose capacity or numberDoctors.')
    date.fromisoformat(as_of)
    rows, candidates, quality = _audit_rows(target, audit, other_audit, original_columns)
    labeled = rows.loc[rows['value'].notna()].copy()
    data = {split: labeled.loc[labeled['split'].eq(split)] for split in SPLITS}
    if any(len(data[split]) < minimum for split, minimum in zip(SPLITS, (10, 9, 2))):
        raise ValueError('Need at least 10 training, nine calibration and two test counts in the saved partitions.')
    train, calibration, test = (data[split] for split in SPLITS)
    predictors, candidate_evidence, ablation, fold_scores = _select_features(target, train, candidates, seed)
    lower_tail_cut = float(train['value'].quantile(.9))
    upper_tail_cut = float(train['value'].quantile(.99))
    trimmed = train.loc[train['value'].le(upper_tail_cut)]
    y_cal, y_test = (frame['value'].to_numpy(dtype=float) for frame in (calibration, test))
    median = float(np.rint(train['value'].median()))
    baseline = np.full_like(y_test, median)
    minmax = _bounds(baseline, _radius(y_cal, np.full_like(y_cal, median)))
    baseline_metrics = _metrics(target, 'median', 'all_exact_labels', y_test, baseline, *minmax, lower_tail_cut)
    metrics = [baseline_metrics]
    cohorts = []
    outputs = []
    group_uses_companion = any(field in COUNT_UNITS for field in predictors)
    rows['group_has_companion'] = group_uses_companion & rows['other_count_reported']
    score_groups = ('facilityTypeId', 'group_has_companion')
    quality_flags = [field + '_bad_category' for field in candidates if field in CATEGORY_NAMES]
    other = next(field for field in COUNT_UNITS if field != target)
    models = {}
    fitted_pipelines = {}
    coefficients = pd.DataFrame(columns=['field', 'encoded_feature', 'coefficient_on_log1p_count'])
    if predictors:
        input_columns = [*predictors, *(['other_count_reported'] if group_uses_companion else [])]
        matrix = _transformer(predictors).fit(train[input_columns])
        models = _fit(matrix, train, seed)
        fitted_pipelines = {method: Pipeline([('preprocessor', matrix), ('model', model)])
                            for method, model in models.items()}
        sensitivity = _fit(matrix, trimmed, seed)
        x_cal, x_test = (matrix.transform(frame) for frame in (calibration, test))
        coefficients = pd.DataFrame({'field': target, 'encoded_feature': matrix.get_feature_names_out(),
                                     'coefficient_on_log1p_count': models['regression'].coef_})
    for method, model in models.items():
        cal_pred, test_pred = _point(model, x_cal), _point(model, x_test)
        cut = _radius(y_cal, cal_pred)
        metrics.append(_metrics(target, method, 'all_exact_labels', y_test, test_pred, *_bounds(test_pred, cut), lower_tail_cut))
        slim_cal, slim_test = _point(sensitivity[method], x_cal), _point(sensitivity[method], x_test)
        metrics.append(_metrics(target, method, 'train_above_p99_removed', y_test, slim_test,
                                *_bounds(slim_test, _radius(y_cal, slim_cal)), lower_tail_cut))
        improved = metrics[-2]['mae'] < baseline_metrics['mae']
        for (kind, has_count), group in rows.groupby(list(score_groups), sort=True):
            reported = group.loc[group['value'].notna() & ~group[quality_flags].any(axis=1)]
            ids = {split: reported.index[reported['split'].eq(split)] for split in SPLITS}
            counts = [len(ids[split]) for split in SPLITS]
            mask_cal, mask_test = calibration.index.isin(ids['calibration']), test.index.isin(ids['test'])
            group_radius = _radius(y_cal[mask_cal], cal_pred[mask_cal])
            true, point = y_test[mask_test], test_pred[mask_test]
            mae = np.abs(true - point).mean() if len(true) else np.nan
            group_train = train.loc[ids['train']]
            group_median = float(np.rint(group_train['value'].median()))
            base_mae = min(np.abs(true - median).mean(), np.abs(true - group_median).mean()) if len(true) else np.nan
            low, high = _bounds(point, group_radius)
            coverage = ((true >= low) & (true <= high)).mean() if len(true) and group_radius is not None else np.nan
            eligible = (kind != '(unknown)' and improved and all(n >= minimum for n, minimum in zip(counts, MIN_SUPPORT))
                        and mae < base_mae and coverage >= .8)
            missing = group.loc[group['status'].eq('missing') & ~group[quality_flags].any(axis=1)]
            # Require an operator level seen in the group's training data. For measured
            # companion counts, abstain outside the observed training range.
            if 'operatorTypeId' in predictors:
                seen = group_train['operatorTypeId'].value_counts()
                missing = missing.loc[missing['operatorTypeId'].isin(seen.loc[seen.ge(10)].index)]
            if has_count:
                low_support, high_support = group_train[other].min(), group_train[other].max()
                missing = missing.loc[missing[other].between(low_support, high_support)]
            published = len(missing) if eligible else 0
            cohorts.append({'field': target, 'method': method, 'facility_type': kind, 'companion_count_reported': bool(has_count),
                            'train_labels': counts[0], 'calibration_labels': counts[1], 'test_labels': counts[2],
                            'mae': mae, 'median_baseline_mae': base_mae, 'coverage': coverage,
                            'eligible': bool(eligible), 'missing_cases_in_support': len(missing), 'estimates_written': published})
            if published:
                point = _point(model, matrix.transform(missing))
                low, high = _bounds(point, group_radius)
                suffix = 'with reported companion count' if has_count else 'with missing companion count'
                for ident, guess, minimum, maximum in zip(missing['facility_id'], point, low, high):
                    outputs.append({'facility_id': ident, 'field': target, 'method': method, 'value': guess,
                                    'unit': COUNT_UNITS[target], 'lower': minimum, 'upper': maximum,
                                    'provenance': f'FDR {as_of} | {MODELS[method]} | facility-held split seed {seed}',
                                    'detail': f'90% nominal split-conformal interval in {kind} group {suffix}. '
                                              f'Group test MAE {mae:.1f} vs stronger median baseline {base_mae:.1f}, n={len(true)}. '
                                              f'Selected inputs: {", ".join(predictors)}. '
                                              'Estimates a reported resource count for validation on observed-label facilities.'})
    counts = pd.DataFrame([dict(field=target, split=part, labeled_facilities=len(frame),
                                zeros=int(frame['value'].eq(0).sum()), maximum=frame['value'].max(),
                                median=frame['value'].median(), train_p99=upper_tail_cut,
                                labels_above_train_p99=int(frame['value'].gt(upper_tail_cut).sum())) for part, frame in data.items()])
    return {'metrics': pd.DataFrame(metrics), 'group_metrics': pd.DataFrame(cohorts),
            'category_quality': pd.DataFrame(quality, columns=['field', 'predictor', 'split', 'status', 'count']),
            'label_quality': counts, 'coefficients': coefficients,
            'candidate_evidence': candidate_evidence, 'ablation': ablation, 'fold_scores': fold_scores,
            'fitted_pipelines': fitted_pipelines,
            'updates': pd.DataFrame(outputs, columns=UPDATE_COLUMNS)}
