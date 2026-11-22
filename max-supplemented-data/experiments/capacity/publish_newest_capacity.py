"""Apply the frozen newest-source XGBoost fit and export aligned release CSVs."""

from argparse import ArgumentParser
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import json
import math
import re

import numpy as np
import pandas as pd

from publish_rule_comparison import add_predictions, count_verification, read_quality_holds
from source_feature_factorial import preflight, write_json
from train_description_recovery import ROOT, RULES, arm_inputs, encode
from train_evaluate import load_inputs, model_predictions, receipt, xgb
from wide_description_recovery import apply, present, rows, scan_descriptions
from facility_tool.data import _array, _key


RULE = RULES['newest']
MODEL_ID = 'newest_source_wide_xgboost_description_v3'
FIELDS = ('branch', 'phone', 'services', 'beds', 'doctors')
CREDIT = {'verified_unchanged': 4, 'verified_corrected': 3, 'verified_filled': 3,
          'corrected_unverified': 1, 'populated_unverified': 1, 'model_estimate': 0, 'empty': 0}
OUTPUT_DATA = 'facilities_newest_capacity.csv'
OUTPUT_CONFIDENCE = 'facility_confidence.csv'
CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def read_json(path):
    return json.loads(path.read_text(encoding='utf8'))


def prepare_predictions(archive, run, as_of):
    inputs = archive / 'inputs'
    frame, groups = load_inputs(inputs)
    comparison = read_json(run / 'metrics.json')
    selected = comparison['runs']['newest']
    masks, _ = preflight(inputs, read_json(archive / 'training/metrics.json'), frame)
    if comparison['validation_leader'] != 'newest' or comparison['audit']['input_sha256'] != {
            name: receipt(inputs / name) for name in comparison['audit']['input_sha256']}:
        raise ValueError('The frozen comparison does not correspond to the selected inputs.')
    quality = read_quality_holds(inputs / 'input_audit.json')
    aliases = read_json(ROOT / 'non-ml/config/project.json')['service_aliases']
    dataset = archive / RULE.replace('_', '-') / 'dataset'
    claims, _, _ = scan_descriptions(archive / 'iteration-01/dataset/facilities.jsonl', aliases, set(quality), as_of)
    X, published, counts, evidence = arm_inputs(frame, groups, dataset/'facilities.jsonl', RULE, claims)
    saved_evidence = {r['facility_id']: r['fields'] for r in rows(run/'newest-description-evidence.jsonl')}
    if evidence != saved_evidence or dict(counts) != selected['description_recoveries']:
        raise ValueError('Description evidence differs from the fit that was evaluated.')
    if counts.get('capacity') or counts.get('conflict:capacity'):
        raise ValueError('This frozen release requires the evaluated capacity ledger.')
    train = (frame.split.eq('train') & frame.training_eligible & published.notna()).to_numpy()
    matrix, encoding = encode(X, train)
    if (encoding != read_json(run/'newest-feature-encodings.json')
            or int(train.sum()) != selected['counts']['training']):
        raise ValueError('Could not reproduce the training encoder.')
    model = xgb.Booster()
    model_path = run/'newest.ubj'
    model.load_model(model_path)
    if model.best_iteration + 1 != selected['fit']['best_round'] or model.num_features() != matrix.shape[1]:
        raise ValueError('The saved model differs from the evaluated fit.')
    test = masks['test']
    test_saved = pd.read_csv(run/'test-predictions.csv', dtype={'facility_id': str}).set_index('facility_id')
    predicted = model_predictions(model, matrix[test], selected['fit']['scale'])
    if (list(test_saved.index) != list(frame.index[test])
            or not np.array_equal(test_saved.reported_beds, frame.loc[test, 'capacity_observed'])
            or not np.array_equal(test_saved.newest, predicted)):
        raise ValueError('The saved model did not reproduce the held-out predictions exactly.')

    eligible = (frame[f'imputation_eligible_{RULE}'] & published.isna()).to_numpy()
    if (eligible & (frame.capacity_observed.notna() | ~frame.routing_eligible)).any():
        raise ValueError('A nonmissing original count or unresolved branch entered model filling.')
    estimated = model_predictions(model, matrix[eligible], selected['fit']['scale'])
    radius = int(selected['test']['interval_radius'])
    if radius != selected['test']['interval_radius'] or not np.all(estimated == np.rint(estimated)):
        raise ValueError('Release expects integer beds and integer interval calibration.')
    predictions = {}
    for identity, estimate in zip(frame.index[eligible], estimated, strict=True):
        point = int(estimate)
        predictions[identity] = {'value': point, 'point_estimate': point, 'lower': max(0, point-radius),
                                 'upper': point+radius, 'method': 'xgboost_estimate', 'source': MODEL_ID,
                                 'source_url': None, 'interval_basis': 'held_out_calibration_absolute_residuals'}
    audit = {'model_sha256': receipt(model_path), 'encoder_manifest_sha256': receipt(run/'newest-feature-encodings.json'),
             'archive_inputs_verified': comparison['audit']['input_sha256'],
             'exact_test_predictions_reproduced': len(test_saved), 'test_mae': selected['test']['mae'],
             'test_rmse': selected['test']['rmse'], 'nominal_interval': .90, 'interval_radius_beds': radius,
             'reported_test_interval_coverage': selected['test']['coverage'],
             'replayed_description_recoveries': dict(counts)}
    return frame, groups, dataset, evidence, quality, predictions, aliases, audit


def previous_phone_checks(path):
    """Reuse the audited phone comparison, which is independent of count selection."""
    with path.open(encoding='utf8', newline='') as file:
        return {int(r['source_row_index']): (r['facility_id'], r['published_phone'],
                                           'phone' in r['verified_field_names'].split(', '))
                for r in csv.DictReader(file)}


def phone_digits(raw):
    return re.sub(r'\D', '', str(raw or ''))


def canonical_phone(raw):
    digits = phone_digits(raw)
    if len(digits) == 12 and digits.startswith('91'):
        return digits[2:]
    if len(digits) == 11 and digits.startswith('0'):
        return digits[1:]
    return digits


def raw_count_equals(original, selected):
    try:
        return Decimal(str(original).strip().replace(',', '')) == Decimal(selected)
    except (InvalidOperation, TypeError):
        return False


def classify(available, verified, original_present, same):
    if not available:
        return 'empty'
    if verified:
        if same:
            return 'verified_unchanged'
        return 'verified_corrected' if original_present else 'verified_filled'
    return 'corrected_unverified' if original_present and not same else 'populated_unverified'


def confidence_states(row, count_evidence, phone_check, aliases):
    raw, geo = row['original'], row['prepared_location']
    identity = row['facility_id']
    beds, doctors = row['final_capacity'], row['count_fields']['numberDoctors']
    published_phone = next(iter(row['contact_evidence'].get('phone_numbers', [])), {}).get('value')
    prior_id, prior_phone, phone_verified = phone_check
    if (prior_id != (identity or '') or phone_digits(prior_phone) != phone_digits(published_phone)):
        raise ValueError('Phone evidence differs across the two count-policy snapshots.')

    original_pin = re.search(r'\b[1-9][0-9]{5}\b', str(raw.get('address_zipOrPostcode') or ''))
    branch_original = bool(present(raw.get('name')) and original_pin)
    branch_available = bool(geo['location_routing_eligible'] and geo['name'] and geo['pin'])
    branch_same = bool(branch_original and original_pin[0] == str(geo['pin'])
                       and _key(raw['name']) == _key(geo['name']))
    bed_evidence, doctor_evidence = (count_evidence.get((identity, field), {}) for field in ('capacity', 'numberDoctors'))
    branch_verified = any(ev.get('external_count_history') or ev.get('release_review')
                          for ev in (bed_evidence, doctor_evidence))
    old_phones = {canonical_phone(p) for name in ('phone_numbers', 'officialPhone')
                  for p in _array(raw.get(name)) if canonical_phone(p)}
    service_cells = [raw.get(key) for key in ('specialties', 'procedure', 'capability', 'description')]
    old_service_text = ' ' + _key(' '.join(str(v) for v in service_cells if present(v))) + ' '
    services = row['service_evidence']
    service_seen = [any(' '+_key(synonym)+' ' in old_service_text
                        for synonym in (s['service'], *aliases.get(s['service'], [])) if _key(synonym))
                    for s in services]
    service_verified = all(s['verification_status'] in {'corroborated', 'approved_source_recovery'}
                           and bool(s['supporting_urls']) for s in services)
    states = {
        'branch': classify(branch_available, branch_verified, branch_original, branch_same),
        'phone': classify(bool(published_phone), phone_verified, bool(old_phones),
                          canonical_phone(published_phone) in old_phones),
        'services': classify(bool(services), service_verified, any(service_seen), all(service_seen)),
    }
    for name, key, cell, evidence in (('beds', 'capacity', beds, bed_evidence),
                                       ('doctors', 'numberDoctors', doctors, doctor_evidence)):
        if cell['method'] == 'xgboost_estimate':
            states[name] = 'model_estimate'
        else:
            states[name] = classify(cell['value'] is not None, count_verification(cell, evidence) == 'confident',
                                    present(raw.get(key)), raw_count_equals(raw.get(key), cell['value']))
    # 20 points per field at full credit. Each step in CREDIT is 5 points.
    points = 5 * sum(CREDIT[state] for state in states.values())
    return states, points


def csv_cell(value):
    if isinstance(value, (dict, tuple, list)):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, str):
        value = CONTROL_CHARS.sub('', value)
        cleaned = value.lstrip()
        if cleaned.startswith(('=', '+', '@', '-')) and not re.fullmatch(r'-?\d+(?:\.\d+)?', cleaned):
            return "'" + value
    return value


def export_csvs(release, output, archive, dataset, frame, groups, recoveries, aliases):
    count_evidence = {(r['facility_id'], r['field']): r for r in rows(dataset/'count_decisions.jsonl')}
    phone_checks = previous_phone_checks(archive/'release/facilities_with_verification.csv')
    areas = frame.to_dict(orient='index')
    counts, score_counts, by_field = Counter(), Counter(), {f: Counter() for f in FIELDS}
    states_cols = [f'{field}_evidence_status' for field in FIELDS]
    status_cols = [f'{key}_cells' for key in CREDIT]
    context_cols = ['capacity_observation_date', 'doctor_observation_date', 'original_capacity', 'original_numberDoctors',
                    'district_population', 'service_source_urls', 'description_recovery_claims']
    input_rows = rows(release/'facilities.jsonl')
    with ((release/'facility_values.csv').open(encoding='utf8', newline='') as flat_file,
          (output/OUTPUT_DATA).open('w', encoding='utf8', newline='') as data,
          (output/OUTPUT_CONFIDENCE).open('w', encoding='utf8', newline='') as confidence):
        flat_rows = csv.DictReader(flat_file)
        confidence_writer = csv.writer(confidence)
        confidence_writer.writerow(['confidence_score'])
        writer = None
        for index, (row, flat) in enumerate(zip(input_rows, flat_rows, strict=True)):
            if index != row['source_row_index'] or index != int(flat['row_index']):
                raise ValueError('CSV and source-row order differ.')
            raw, identity = row['original'], row['facility_id']
            if writer is None:
                columns = list(flat) + [k for k in raw if k not in flat]
                writer = csv.DictWriter(data, fieldnames=[*columns, *groups['socioeconomic'], *context_cols,
                                                         *states_cols, *status_cols])
                writer.writeheader()
            accepted = recoveries.get(identity, {})
            apply(row, accepted, set())
            states, value = confidence_states(row, count_evidence, phone_checks[index], aliases)
            state_count = Counter(states.values())
            score_counts[value] += 1
            counts.update(state_count)
            for field, state in states.items():
                by_field[field][state] += 1
            area = areas.get(identity, {})
            population = area.get('log_population')
            data_row = {**raw, **flat,
                        'documented_services': ' | '.join(s['service'] for s in row['service_evidence']),
                        'capacity_observation_date': row['final_capacity'].get('source_date'),
                        'doctor_observation_date': row['count_fields']['numberDoctors'].get('source_date'),
                        'original_capacity': raw.get('capacity'), 'original_numberDoctors': raw.get('numberDoctors'),
                        'district_population': round(math.expm1(population)) if pd.notna(population) else None,
                        'service_source_urls': ' | '.join(sorted({url for service in row['service_evidence']
                                                                  for url in service['supporting_urls'] or []})),
                        'description_recovery_claims': accepted or None,
                        **{name: area[name] if name in area and pd.notna(area[name]) else None
                           for name in groups['socioeconomic']},
                        **{f'{field}_evidence_status': state for field, state in states.items()},
                        **{f'{key}_cells': state_count[key] for key in CREDIT}}
            if (year := accepted.get('yearEstablished')):
                data_row['yearEstablished'] = year['value']
            if 'equipment' in accepted:
                data_row['equipment'] = [claim['value'] for claim in accepted['equipment']]
            writer.writerow({k: csv_cell(v) for k, v in data_row.items()})
            confidence_writer.writerow([value])
    if sum(score_counts.values()) != len(phone_checks):
        raise ValueError('Missing source rows in the confidence export.')
    return {'row_count': sum(score_counts.values()), 'scored_fields': FIELDS, 'point_multipliers': CREDIT,
            'confidence_distribution': dict(sorted(score_counts.items())), 'cell_states': dict(counts),
            'cell_states_by_field': by_field}


def verify_pair(folder, predictions, expected_rows):
    models = Counter()
    with ((folder/OUTPUT_DATA).open(encoding='utf8', newline='') as filled,
          (folder/OUTPUT_CONFIDENCE).open(encoding='utf8', newline='') as confidence):
        data, scores = csv.DictReader(filled), csv.DictReader(confidence)
        if scores.fieldnames != ['confidence_score']:
            raise ValueError('Confidence CSV must have exactly one column.')
        row_count = 0
        for index, (record, rating) in enumerate(zip(data, scores, strict=True)):
            values = [record[f'{field}_evidence_status'] for field in FIELDS]
            expected = 5 * sum(CREDIT[status] for status in values)
            if int(record['row_index']) != index or int(rating['confidence_score']) != expected:
                raise ValueError('Confidence and filled CSVs are misaligned.')
            row_count += 1
            if record['capacity_status'] == 'xgboost_estimate':
                prediction = predictions[record['facility_id']]
                columns = ('capacity', 'capacity_prediction_lower', 'capacity_prediction_upper')
                if [int(record[c]) for c in columns] != [prediction[k] for k in ('value', 'lower', 'upper')]:
                    raise ValueError('Published estimate differs from the verified model output.')
                if record['beds_evidence_status'] != 'model_estimate':
                    raise ValueError('A model estimate gained verification credit.')
                models[record['facility_id']] += 1
    if row_count != expected_rows or models.keys() != predictions.keys():
        raise ValueError('A source row or model estimate disappeared in export.')
    return {'distinct_facilities_imputed': len(models), 'source_rows_imputed': sum(models.values())}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--audit-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.output.exists() or args.audit_dir.exists():
        raise FileExistsError('Use fresh paths for the release and audit directories.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_dir.mkdir(parents=True)
    frame, groups, base, recoveries, holds, estimates, aliases, model_audit = prepare_predictions(
        args.archive, args.run, args.as_of)
    release = args.audit_dir/'base_capacity_release'
    audit = add_predictions(base, release, estimates, MODEL_ID, RULE.replace('_', '-'), holds)
    with TemporaryDirectory(prefix='.capacity-export-', dir=args.output.parent) as tmp:
        staging = Path(tmp)/'delivery'
        staging.mkdir()
        confidence = export_csvs(release, staging, args.archive, base, frame, groups, recoveries, aliases)
        export_audit = verify_pair(staging, estimates, audit['original_rows_preserved'])
        if export_audit != {key: audit[key] for key in export_audit}:
            raise ValueError('Export counts do not match the release ledger.')
        hashes = {n: receipt(staging/n) for n in (OUTPUT_DATA, OUTPUT_CONFIDENCE)}
        write_json(args.audit_dir/'delivery_receipt.json',
                   {'release': audit, 'model': model_audit, 'confidence': confidence, 'files_sha256': hashes})
        staging.rename(args.output)
    print(json.dumps({'files': str(args.output), 'rows': confidence['row_count'], **export_audit,
                      'cell_states': confidence['cell_states'], 'hashes': hashes}, indent=2))


if __name__ == '__main__':
    main()
