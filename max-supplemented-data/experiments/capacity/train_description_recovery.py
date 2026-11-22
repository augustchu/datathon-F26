"""Compare full-width XGBoost fits after recovering fields from source descriptions."""

from argparse import ArgumentParser
from collections import Counter
from datetime import date
from pathlib import Path
from time import monotonic
import json
import sys

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import OneHotEncoder

from source_feature_factorial import preflight, write_json
from train_evaluate import (GRID, PUBLISHED_METHODS, calibrated_radius, district_clusters,
                            fit_candidate, interval_metrics, load_inputs, model_predictions, paired_interval, score)
from wide_description_recovery import ROOT, EXTRACTOR, apply, decide, present, rows, scan_descriptions

from facility_tool.data import _array
from facility_tool.profiles import plausible_phone


RULES = {'ranked': 'iteration_01', 'newest': 'iteration_03',
         'same_year_mean': 'iteration_02', 'two_year_mean': 'iteration_04'}
BENCHMARK = 'iteration_01__population'
PHONE = {'phone_numbers', 'officialPhone'}
# These two columns are held outside the model for every arm.
EXCLUDED = {'capacity', 'description'}


def source_values(row, prepared, groups):
    """Keep the raw source schema with publisher-selected values where available."""
    raw, geo = row['original'], row['prepared_location']
    values = {k: v for k, v in raw.items() if k not in EXCLUDED}
    recovery = row.get('description_recovery', {})
    values.update({
        'numberDoctors': row['count_fields']['numberDoctors']['value'],
        'yearEstablished': (row['year_established'] or {}).get('value'),
        'officialWebsite': (row['website'] or {}).get('value') or raw.get('officialWebsite'),
        'name': geo['name'] or raw.get('name'),
        'address_city': geo['city'] or raw.get('address_city'),
        'address_stateOrRegion': geo['state'] or raw.get('address_stateOrRegion'),
        'address_zipOrPostcode': geo['pin'] or raw.get('address_zipOrPostcode'),
        'latitude': prepared['latitude'], 'longitude': prepared['longitude'],
        'coordinates': ((prepared['latitude'], prepared['longitude'])
                        if pd.notna(prepared['latitude']) and pd.notna(prepared['longitude']) else None),
        'district': geo['district'],
        'documented_services': [item['service'] for item in row['service_evidence']],
    })
    for name in PHONE:
        values[name] = 'listed' if any(map(plausible_phone, _array(raw.get(name)))) else 'not listed'
    if 'equipment' in recovery:
        values['equipment'] = [claim['value'] for claim in recovery['equipment']]
    for field in groups['socioeconomic'] + groups['population']:
        values[field] = prepared[field]
    return values


def arm_inputs(frame, groups, path, rule, claims):
    area = frame.to_dict(orient='index')
    feature_rows, labels, methods, evidence = {}, {}, {}, {}
    counts = Counter()
    for row in rows(path):
        identity = row['facility_id']
        if identity not in area or identity in feature_rows:
            continue
        known = area[identity]
        before = row['count_fields']['capacity']
        base = known[f'capacity_{rule}']
        if ((before['value'] is None and pd.notna(base))
                or (before['value'] is not None and before['value'] != base)
                or before['method'] != known[f'capacity_method_{rule}']):
            raise ValueError('Published and prepared capacity targets disagree.')
        accepted, conflicts = decide(row, claims.get(identity, {}))
        apply(row, accepted, conflicts)
        for field, event in accepted.items():
            counts[field] += 1
            evidence.setdefault(identity, {})[field] = event
        for field in conflicts:
            counts[f'conflict:{field}'] += 1
        decision = row['count_fields']['capacity']
        labels[identity], methods[identity] = decision['value'], decision['method']
        feature_rows[identity] = source_values(row, known, groups)
    if set(feature_rows) != set(frame.index):
        raise ValueError('A source arm omitted or added canonical facilities.')
    if not all(methods[k] in PUBLISHED_METHODS for k, v in labels.items() if v is not None):
        raise ValueError('Every training target must carry approved source provenance.')
    X = pd.DataFrame.from_dict(feature_rows, orient='index').reindex(frame.index)
    y = pd.Series(labels, dtype='float64').reindex(frame.index)
    if set(X) & EXCLUDED or X.columns.duplicated().any():
        raise ValueError('A target or description entered the model matrix.')
    return X, y, counts, evidence


def text_value(value):
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ''
    if pd.isna(value):
        return ''
    return str(value) if present(value) else ''


def encode(X, train):
    """Fit only the encodings required to give XGBoost numeric inputs."""
    blocks, manifest = [], []
    for name in X:
        text = X[name].map(text_value)
        numeric = pd.to_numeric(text, errors='coerce')
        training = text.loc[train]
        nonempty = int(training.ne('').sum())
        unique = int(training.nunique(dropna=False))
        if nonempty and numeric.loc[train].notna().sum() / nonempty >= .95:
            array = numeric.to_numpy(dtype=np.float32, copy=True).reshape(-1, 1)
            array[~np.isfinite(array)] = np.nan
            encoding = 'numeric'
        elif unique <= 40:
            categorical = OneHotEncoder(handle_unknown='ignore', sparse_output=False, dtype=np.float32)
            categorical.fit(training.to_numpy().reshape(-1, 1))
            array = categorical.transform(text.to_numpy().reshape(-1, 1))
            encoding = 'one-hot'
        else:
            vectorizer = TfidfVectorizer(max_features=96, min_df=2, dtype=np.float32,
                                         token_pattern=r'(?u)\b\w+\b')
            try:
                vectorizer.fit(training)
                array = vectorizer.transform(text).toarray()
                encoding = 'TF-IDF'
            except ValueError as exc:
                if 'empty vocabulary' not in str(exc) and 'no terms remain' not in str(exc).lower():
                    raise
                array = np.zeros((len(X), 1), dtype=np.float32)
                encoding = 'no repeated token'
        blocks.append(array)
        manifest.append({'field': name, 'encoding': encoding, 'columns': array.shape[1],
                         'observed_training': nonempty})
    return np.concatenate(blocks, axis=1), manifest


def fit_rule(matrix, label, train, tune, cal, test, observed, clusters, reference, mentioned):
    training = label.loc[train].to_numpy(dtype=float)
    candidates = []
    for params in GRID:
        model, trial = fit_candidate(params, matrix[train], training, matrix[tune], observed[tune])
        candidates.append((model, trial))
        print(f'  {trial["scale"]}/{trial["depth"]}: {trial["validation"]["mae"]:.2f} validation MAE', flush=True)
    model, chosen = min(candidates, key=lambda item: (item[1]['validation']['mae'],
                                                         item[1]['depth'], item[1]['best_round']))
    predict = lambda mask: model_predictions(model, matrix[mask], chosen['scale'])
    radius = calibrated_radius(observed[cal], predict(cal))
    pred = predict(test)
    actual = observed[test]
    err = np.abs(pred - actual)
    reference_err = np.abs(reference - actual)
    slices = {}
    for name, mask in [('numeric bed wording detected', mentioned), ('no detected bed wording', ~mentioned),
                       ('over 200 beds', actual > 200)]:
        if mask.sum() >= 15:
            slices[name] = {**score(actual[mask], pred[mask]), 'benchmark_mae': float(reference_err[mask].mean())}
    result = {'counts': {'training': int(train.sum()), 'validation': int(tune.sum()),
                         'interval': int(cal.sum()), 'test': int(test.sum())},
              'fit': {k: chosen[k] for k in ('scale', 'depth', 'best_round')},
              'validation': chosen['validation'],
              'test': {**score(actual, pred), **interval_metrics(actual, pred, radius), 'interval_radius': radius},
              'paired_mae_vs_previous_release': paired_interval(reference_err, err, clusters),
              'slices': slices, 'trials': [trial for _, trial in candidates]}
    return result, model, pred


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat)
    args = parser.parse_args()
    start = monotonic()
    inputs, output = args.archive / 'inputs', args.output
    archival = json.loads((args.archive/'training/metrics.json').read_text())
    frame, groups = load_inputs(inputs)
    masks, audit = preflight(inputs, archival, frame)
    uncontested = ~frame.capacity_source_disagreement.to_numpy(dtype=bool)
    tune = masks['validation'] & uncontested
    cal = masks['interval_calibration'] & uncontested
    test = masks['test']
    observed = frame.capacity_observed.to_numpy(dtype=float)
    cluster = district_clusters(frame.loc[test])
    raw_hold = json.loads((inputs/'input_audit.json').read_text())['capacity_quality_holds']
    known_bad = {row['facility_id'] for row in raw_hold}
    config = json.loads((ROOT/'non-ml/config/project.json').read_text())
    ranked = args.archive / 'iteration-01/dataset'
    claims, mentioned, scanned = scan_descriptions(ranked/'facilities.jsonl', config['service_aliases'], known_bad, args.as_of)
    worded = frame.index[test].isin(mentioned)
    old = pd.read_csv(args.archive/'training/test_predictions.csv', dtype={'facility_id': str})
    old = old.loc[old.run_id.eq(BENCHMARK)].set_index('facility_id').reindex(frame.index[test])
    if not np.array_equal(old.capacity_observed, observed[test]):
        raise ValueError('Earlier release did not use the same test facilities and labels.')
    reference = old.capacity_predicted.to_numpy(dtype=float)
    output.mkdir(exist_ok=False)
    print(f'Description scan completed on {scanned["descriptions"]:,} source rows.', flush=True)

    results, test_predictions = {}, {}
    # Nearby averaging variants currently have identical source decisions. Check before reusing encodings.
    newest = None
    for name, rule in RULES.items():
        source = args.archive / rule.replace('_','-') / 'dataset' / 'facilities.jsonl'
        X, y, recoveries, evidence = arm_inputs(frame, groups, source, rule, claims)
        train = (frame.split.eq('train') & frame.training_eligible & y.notna()).to_numpy()
        if np.any(train & (tune | cal | test)) or (frame.capacity_target_defect & train).any():
            raise ValueError('Held-out facilities and known wrong counts are ineligible for fitting.')
        print(f'{name}: {dict(recoveries)} before fitting on {int(train.sum())} training labels.', flush=True)
        if newest is not None and np.array_equal(train, newest[2]) and X.equals(newest[0]):
            matrix, manifest = newest[3:]
        else:
            matrix, manifest = encode(X, train)
        if name == 'newest':
            newest = (X, y, train, matrix, manifest)
        saved, model, pred = fit_rule(matrix, y, train, tune, cal, test, observed, cluster, reference, worded)
        prior_labels = frame[f'capacity_{rule}'].notna().to_numpy()
        saved['new_training_bed_labels'] = int((train & ~prior_labels).sum())
        saved['description_recoveries'] = dict(recoveries)
        saved['published_bed_labels_before_model'] = int(y.notna().sum())
        saved['source_field_count'] = len(X.columns)
        saved['encoded_feature_count'] = int(matrix.shape[1])
        results[name], test_predictions[name] = saved, pred
        model.save_model(output/f'{name}.ubj')
        write_json(output/f'{name}-feature-encodings.json', manifest)
        with (output/f'{name}-description-evidence.jsonl').open('w', encoding='utf8') as stream:
            for facility_id, fields in evidence.items():
                stream.write(json.dumps({'facility_id': facility_id, 'fields': fields},
                                        ensure_ascii=False, allow_nan=False)+'\n')
        print(f'{name} done: {saved["test"]["mae"]:.2f} test MAE.', flush=True)

    ranked_error = np.abs(test_predictions['ranked'] - observed[test])
    for name, pred in test_predictions.items():
        results[name]['paired_mae_vs_ranked'] = paired_interval(ranked_error, np.abs(pred - observed[test]), cluster)
    panel = pd.DataFrame({'facility_id': frame.index[test], 'reported_beds': observed[test],
                          'prior_release_beds': reference, 'numeric_bed_wording': worded, **test_predictions})
    panel.to_csv(output/'test-predictions.csv', index=False)
    result = {'runs': results, 'audit': audit, 'excluded_source_fields': sorted(EXCLUDED),
              'description_rows_scanned': scanned['descriptions'], 'bed_wording_test_count': int(worded.sum()),
              'description_scan_counts': dict(scanned),
              'training_seconds': round(monotonic() - start, 3), 'previous_release': score(observed[test], reference),
              'validation_leader': min(results, key=lambda item: results[item]['validation']['mae']),
              'test_mae_leader': min(results, key=lambda item: results[item]['test']['mae']),
              'date_rule_predictions_equal': all(np.array_equal(test_predictions['newest'], test_predictions[name])
                                                 for name in ('same_year_mean', 'two_year_mean'))}
    write_json(output/'metrics.json', result)

if __name__ == '__main__':
    main()
