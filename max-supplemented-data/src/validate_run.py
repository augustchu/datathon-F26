"""Check source preservation, held branches, review outcomes and export counts."""

import argparse
import csv
from hashlib import sha256
from pathlib import Path

from build_dataset import TOTAL_SCOPE, read_checkpoint
from model_sources import FIELDS, read_json, write_json


def validate(iteration, raw_path):
    data = iteration / 'dataset'
    model = iteration / 'model'
    summary = read_json(data / 'dataset_summary.json')
    sample = read_json(iteration / 'sample.json')
    ledger = read_json(model / 'review_ledger.json')
    adjudications = {row['case_id']: row for row in ledger}
    assert len(adjudications) == len(sample) == len(ledger)
    assert set(adjudications) == {row['case_id'] for row in sample}

    releases = {}
    for row in read_checkpoint(data / 'count_decisions.jsonl'):
        key = (row['facility_id'], row['field'])
        assert key not in releases
        releases[key] = row
        if row['method'] in {'branch_identity_hold', 'review_unresolved'}:
            assert row['value'] is None
        if row['method'] in {'dated_external_report', 'undated_external_report', 'direct_review',
                             'approved_source_recovery'}:
            assert row['source_url'] and row['value'] > 0
            if row['method'] == 'dated_external_report':
                assert row['source_date']
        if 'external_count_history' in row:
            counts = {claim['value'] for claim in row['external_count_history'] if claim['scope'] == TOTAL_SCOPE}
            if row['original_reported'] is not None:
                counts.add(row['original_reported'])
            assert row['source_value_disagreement'] == (len(counts) > 1)
            assert all(claim in row['external_count_history'] and claim['scope'] == TOTAL_SCOPE
                       and claim['fdr_publisher_overlap'] == 'publisher_not_listed_for_fdr_branch'
                       for claim in row['eligible_external_claims'])
        if review := row.get('release_review'):
            assert row['method'] in {'review_unresolved', 'direct_review', 'branch_identity_hold'}
            if row['method'] != 'branch_identity_hold':
                assert row['value'] == review['verified_value']
                assert row['method'] == ('review_unresolved' if review['status'] == 'unresolved' else 'direct_review')

    for case in sample:
        if case['field'] in FIELDS:
            decision = releases[(case['identity'], case['field'])]
            review = decision.get('release_review', adjudications[case['case_id']])
            if decision.get('release_review') and decision['method'] == 'branch_identity_hold':
                continue
            assert decision['value'] == review['verified_value']

    n_rows = 0
    for index, (original, row) in enumerate(zip(read_checkpoint(raw_path),
                                                read_checkpoint(data / 'facilities.jsonl'), strict=True)):
        assert row['source_row_index'] == index
        assert row['original'] == original
        fields = row['count_fields']
        if row['prepared_location']['location_routing_eligible'] is not True:
            assert not any(fields[field]['value'] is not None for field in FIELDS)
            assert row['prepared_location']['lat'] is None and row['prepared_location']['lon'] is None
        n_rows += 1
    with (data / 'facility_values.csv').open(encoding='utf-8', newline='') as handle:
        assert n_rows == sum(1 for _ in csv.DictReader(handle)) == summary['fdr_rows_preserved']
    for field in FIELDS:
        assert summary['effective_count_nonmissing_by_linked_branch'][field] == sum(
            row['value'] is not None for row in releases.values() if row['field'] == field)
    assert len(releases) == len(FIELDS) * summary['linked_branches']
    assert summary.get('release_field_reviews', 0) == sum('release_review' in row for row in releases.values())

    model_eval = read_json(model / 'model_evaluation.json')
    assert sum(row['sampled'] for row in model_eval['field_review_coverage'].values()) == len(sample)
    result = {'checks': 'passed', 'source_rows': n_rows, 'review_cases': len(sample),
              'branch_field_decisions': len(releases),
              'sample_sha256': sha256((iteration / 'sample.json').read_bytes()).hexdigest()}
    write_json(iteration / 'validation.json', result)
    print(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iteration', type=Path, required=True)
    parser.add_argument('--raw', type=Path, required=True)
    args = parser.parse_args()
    validate(args.iteration, args.raw)
