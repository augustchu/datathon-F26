"""Package the selected rule-cleaning run with verification and a pipeline overlay."""

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlsplit

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'non-ml' / 'src'))
from facility_tool.updates import COLUMNS, load_estimates
from release_capacity import add_predictions, read_predictions, read_quality_holds


HOLD_METHODS = {'review_unresolved', 'branch_identity_hold', 'original_count_hold', 'target_quality_hold'}
IMPORTANT_FIELDS = ('branch', 'phone', 'services', 'beds', 'doctors')
NICE_FEATURE_SET = {'facility_only': 'Facility only', 'socioeconomic': 'Socioeconomic',
                    'population': 'Population', 'socioeconomic_population': 'Socioeconomic + population'}
NICE_SOURCE_POLICY = {'ranked': 'Publisher ranked', 'latest_year_mean': 'Same-year average',
                      'newest': 'Newest report', 'recent_two_year_mean': 'Two-year average',
                      'original_reported_no_supplementation': 'Original FDR'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_rows(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if set(row) != {'columns'}:
                yield row


def count_verification(decision, evidence):
    method, value = decision['method'], decision['value']
    if method in HOLD_METHODS:
        return 'review unresolved'
    if method == 'xgboost_estimate':
        return 'model estimate'
    if value is None:
        return 'missing'
    if method in {'direct_review', 'approved_source_recovery'} and decision.get('source_url'):
        return 'confident'
    external = evidence.get('eligible_external_claims', [])
    families = {claim.get('publisher_family') or claim['source'] for claim in external}
    wholly_agrees = bool(external) and all(claim['value'] == value for claim in external)
    if wholly_agrees and (len(families) > 1 or evidence.get('original_reported') == value):
        return 'confident'
    return 'source disagreement' if evidence.get('source_value_disagreement') else 'reported'


def phone_corroboration(path):
    publishers = defaultdict(set)
    for row in read_rows(path):
        if (row['field'] != 'phone_numbers' or row['source'] == 'fdr'
                or row['link_status'] not in {'anchor', 'linked'} or not row.get('source_url')):
            continue
        domain = urlsplit(row['source_url']).hostname
        if domain:
            publishers[row['facility_id'], row['value']].add(domain.removeprefix('www.'))
    return {key for key, hosts in publishers.items() if len(hosts) > 1}


def pack_facility(row, count_rows, area, verified_phones):
    key, geo = row['facility_id'], row['prepared_location']
    beds, doctors = row['final_capacity'], row['count_fields']['numberDoctors']
    bed_evidence, doctor_evidence = (count_rows.get((key, field), {}) for field in ('capacity', 'numberDoctors'))
    bed_status, doc_status = count_verification(beds, bed_evidence), count_verification(doctors, doctor_evidence)
    route = geo['location_routing_eligible'] is True
    phone = next(iter(row['contact_evidence'].get('phone_numbers', [])), {})
    services = row['service_evidence']
    present = (route and bool(geo['name'] and geo['pin']), bool(phone.get('value')), bool(services),
               beds['value'] is not None, doctors['value'] is not None)
    verified = (
        present[0] and any(evidence.get('external_count_history') or evidence.get('release_review')
                           for evidence in (bed_evidence, doctor_evidence)),
        (key, phone.get('value')) in verified_phones,
        any(item['verification_status'] in {'corroborated', 'approved_source_recovery'} for item in services),
        bed_status == 'confident', doc_status == 'confident',
    )
    if any(checked and not available for checked, available in zip(verified, present, strict=True)):
        raise ValueError('Verified fields must also be populated.')
    known = [status for status in (bed_status, doc_status) if status not in {'missing', 'model estimate'}]
    if 'review unresolved' in known:
        verification = 'review unresolved'
    elif 'confident' in known:
        verification = 'confident' if all(status == 'confident' for status in known) else 'partly corroborated'
    elif 'source disagreement' in known:
        verification = 'source disagreement'
    else:
        verification = 'reported' if known else bed_status
    factor = len(IMPORTANT_FIELDS)
    nominal = 70 * sum(verified) / factor + 30 * sum(present) / factor
    return {
        'facility': geo['name'] or row['original'].get('name'), 'PIN': geo['pin'], 'state': geo['state'],
        'district': geo['district'], 'beds_final': beds['value'], 'doctors_selected': doctors['value'],
        'verification': verification, 'facility_confidence_score': nominal,
        'important_fields_verified': sum(verified), 'important_fields_populated': sum(present),
        'bed_verification': bed_status, 'doctor_verification': doc_status,
        'capacity_status': beds['method'], 'doctor_status': doctors['method'],
        'bed_estimate_lower': beds.get('lower'), 'bed_estimate_upper': beds.get('upper'),
        'original_beds': row['original'].get('capacity'), 'original_doctors': row['original'].get('numberDoctors'),
        'district_population': math.expm1(area['log_population']) if area.get('log_population') is not None else None,
        'routing_eligible': route, 'latitude': geo['lat'], 'longitude': geo['lon'], 'address': geo['address'],
        'published_phone': phone.get('value'), 'published_phone_source': phone.get('source_url'),
        'documented_services': ' | '.join(item['service'] for item in services) or None,
        'service_source_URLs': ' | '.join(sorted({url for item in services
                                                 for url in item.get('supporting_urls') or []})) or None,
        'facility_id': key, 'source_row_index': row['source_row_index'],
        'bed_source': beds.get('source'), 'bed_observation_date': beds.get('source_date'),
        'bed_source_URL': beds.get('source_url'), 'doctor_source': doctors.get('source'),
        'doctor_observation_date': doctors.get('source_date'), 'doctor_source_URL': doctors.get('source_url'),
        'verified_field_names': ', '.join(field for field, check in zip(IMPORTANT_FIELDS, verified, strict=True) if check),
    }


def evidence_rows(count_rows):
    for row in count_rows.values():
        for claim in row.get('external_count_history', []):
            yield [row['facility_id'], row['field'], claim['value'], claim['scope'], claim['date'],
                   claim['source'], claim['fdr_publisher_overlap'], claim['url'], claim['record_id']]
        if review := row.get('release_review'):
            yield [row['facility_id'], row['field'], review['verified_value'], review.get('scope'),
                   review.get('evidence_date'), 'Public document review', review['status'], review['evidence_url'],
                   review['case_id']]
            for other in review.get('additional_reported_observations', []):
                yield [row['facility_id'], row['field'], other['value'], other['scope'], other['observation_date'],
                       'Review reference observation', 'retained for context', other['source_url'], review['case_id']]


def package(args):
    run = args.run
    metrics = read_json(run / 'training/metrics.json')
    winner = metrics['winner']['release_run_id']
    selected = next(record for record in metrics['runs'] if record['run_id'] == winner)
    iteration = selected['iteration'].replace('_', '-')
    dataset = run / iteration / 'dataset'
    predictions = read_predictions(run / 'training/imputation_predictions.csv', winner)
    if len(predictions) != metrics['winner']['imputation_count']:
        raise ValueError('Predictions do not reconcile with the winning run receipt.')
    release = run / 'release'
    audit = add_predictions(dataset, release, predictions, winner, iteration,
                            read_quality_holds(run / 'inputs/input_audit.json'))
    evidence = {(row['facility_id'], row['field']): row for row in read_rows(dataset / 'count_decisions.jsonl')}
    area = {row['facility_id']: row for row in read_rows(run / 'inputs/facilities.jsonl')}
    phones = phone_corroboration(args.contact_evidence)
    rows = [pack_facility(row, evidence, area.get(row['facility_id'], {}), phones)
            for row in read_rows(release / 'facilities.jsonl')]
    if len(rows) != audit['original_rows_preserved']:
        raise ValueError('The spreadsheet lost original rows.')
    for row in rows:
        if row['verification'] == 'confident' and 'confident' not in (row['bed_verification'], row['doctor_verification']):
            raise ValueError('The verification label requires independent count support.')
    groups = read_json(run / 'inputs/feature_groups.json')
    enrichment = [field for field in selected['features'] if field in groups['socioeconomic']]
    for row in rows:
        row.update({field: area.get(row['facility_id'], {}).get(field) for field in enrichment})

    overlay = [{
        'facility_id': key, 'field': 'capacity', 'method': 'ml', 'value': estimate['value'], 'unit': 'beds',
        'lower': estimate['lower'], 'upper': estimate['upper'],
        'provenance': f'{run.name} XGBoost {winner}. Selected by validation MAE on the frozen comparison.',
        'detail': (f'Whole-facility bed estimate. 90% nominal split-conformal interval. '
                   f'Test MAE {selected["test"]["mae"]:.2f} beds across {selected["test"]["n"]} reported labels. '
                   'Occupancy requires separate data.'),
    } for key, estimate in sorted(predictions.items())]
    with (release / 'load_distribution_capacity_overlay.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(overlay)
    verified_overlay = load_estimates(release / 'load_distribution_capacity_overlay.csv', area, services={})
    if len(verified_overlay) != len(predictions) or not (verified_overlay.value.ge(1)).all():
        raise ValueError('Notebook 05 import differs from the model release.')
    df = pd.DataFrame(rows)
    spreadsheet_safe = df.map(lambda value: "'" + value if isinstance(value, str)
                              and value.startswith(('=', '+', '@', '-')) else value)
    spreadsheet_safe.to_csv(release / 'facilities_with_verification.csv', index=False)
    policies = metrics['source_policies']
    score_table = [[NICE_SOURCE_POLICY[policies[x['iteration']]], NICE_FEATURE_SET[x['feature_set']], x['counts']['training'],
                    x['validation']['mae'], x['test']['mae'], x['test']['rmse'], x['test']['r2'],
                    x['interval']['coverage'], 'Selected' if x['run_id'] == winner else ''] for x in metrics['runs']]
    counts = Counter(row['verification'] for row in rows)
    payload = {'rows': rows, 'evidence': list(evidence_rows(evidence)), 'score_table': score_table,
               'selected_policy': NICE_SOURCE_POLICY[policies[selected['iteration']]],
               'selected_tier': NICE_FEATURE_SET[selected['feature_set']], 'test_n': selected['counts']['test'],
               'counts': dict(counts), 'release': audit}
    (release / 'workbook-input.json').write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False) + '\n',
                                                 encoding='utf-8')
    receipt = {'spreadsheet_rows': len(rows), 'verification_status_counts': dict(counts),
               'verified_bed_rows': int(df.bed_verification.eq('confident').sum()),
               'verified_doctor_rows': int(df.doctor_verification.eq('confident').sum()),
               'average_facility_confidence': float(df.facility_confidence_score.mean()),
               'overlay_rows_accepted_by_notebook_05_loader': len(verified_overlay), 'release_run': winner}
    (release / 'verification_summary.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--contact-evidence', type=Path, required=True)
    print(json.dumps(package(parser.parse_args()), indent=2))
