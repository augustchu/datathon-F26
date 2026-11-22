"""Build a blinded In The Loop queue for a frozen independent capacity sample."""

import argparse
from collections import defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile


PUBLISHERS = {'FDR': 'Virtue Foundation FDR', 'IFFCO Tokio': 'IFFCO Tokio',
              'sbi_general_archived_quality_disclosure': 'SBI General'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_rows(path):
    with path.open(encoding='utf-8') as handle:
        yield from map(json.loads, handle)


def evidence_image(capture, directory):
    key = capture['case_id']
    if capture.get('capture_scope') != 'full_page':
        raise ValueError(f'{key} requires a fresh full-page screenshot.')
    for field in ('source_url', 'address'):
        url = urlsplit(capture.get(field, ''))
        if url.scheme != 'https' or not url.hostname:
            raise ValueError(f'{key} requires HTTPS original and captured source links.')
    file = capture['file']
    if Path(file).name != file or Path(file).suffix != '.png':
        raise ValueError(f'{key} has an invalid full-page image filename.')
    image = directory / file
    if sha256(image.read_bytes()).hexdigest() != capture['sha256']:
        raise ValueError(f'Screenshot digest differs for {key}.')
    return image


def checkpoint_cases(dataset, cases):
    expected = {(row['name'], row['pin']) for row in cases}
    identities = defaultdict(set)
    for row in read_rows(dataset / 'facilities.jsonl'):
        location = row['prepared_location']
        key = (location.get('name'), str(location.get('pin') or ''))
        if key in expected and row['facility_id']:
            identities[key].add(row['facility_id'])
    if set(identities) != expected or any(len(ids) != 1 for ids in identities.values()):
        raise ValueError('A review case failed exact unique branch lookup.')
    wanted = {next(iter(ids)): key for key, ids in identities.items()}
    reports = {}
    for row in read_rows(dataset / 'count_decisions.jsonl'):
        if row['field'] != 'capacity' or row['facility_id'] not in wanted:
            continue
        claims = list(row['eligible_external_claims'])
        if row['original_reported'] is not None:
            claims.append({'source': 'FDR', 'value': row['original_reported'], 'date': '', 'url': ''})
        reports[wanted[row['facility_id']]] = claims
    if set(reports) != expected:
        raise ValueError('The frozen review cases do not match the count decisions.')
    return reports


def build(args):
    cases = read_json(args.evidence)['challenge']
    captures = {row['case_id']: row for row in read_json(args.captures / 'capture_receipt.json')}
    reports = checkpoint_cases(args.dataset, cases)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows, included = [], []
    for case in cases:
        key = case['case_id']
        capture = captures.get(key)
        if not capture or capture['status'] != 'captured':
            continue
        image = evidence_image(capture, args.captures)
        included.append(key)
        claims = reports[(case['name'], case['pin'])]
        for index, claim in enumerate(claims, 1):
            rows.append({'id': f'{key}-{index:02d}', 'title': f'{case["name"]} · {PUBLISHERS[claim["source"]]}',
                         'screenshot_path': args.asset_prefix + image.name, 'source_url': capture['source_url'],
                         'captured_url': capture['address'] if capture['address'] != capture['source_url'] else '',
                         'case_id': key, 'branch_pin': case['pin'], 'candidate_publisher': PUBLISHERS[claim['source']],
                         'candidate_beds': int(claim['value']), 'candidate_as_of': claim['date'] or 'Unspecified',
                         'candidate_source_url': claim.get('url') or '',
                         'field_scope': 'Exact total for the whole branch'})

    if not rows or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Empty or duplicated verification queue.')
    queue_path = args.output_dir / 'capacity-review-queue.csv'
    with queue_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    receipt = {'frozen_cases': len(cases), 'included_cases': included,
               'unavailable_cases': [case['case_id'] for case in cases if case['case_id'] not in included],
               'candidate_rows': len(rows), 'source': 'independent_holdout', 'capture_scope': 'full_page',
               'review_labels_preloaded': False,
               'queue_sha256': sha256(queue_path.read_bytes()).hexdigest()}
    receipt_path = args.output_dir / 'queue_receipt.json'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    with ZipFile(args.output_dir / 'in-the-loop-capacity-review.zip', 'w', compression=ZIP_DEFLATED) as bundle:
        bundle.write(queue_path, queue_path.name)
        bundle.write(receipt_path, receipt_path.name)
        for case in included:
            name = captures[case]['file']
            bundle.write(args.captures / name, 'review-assets/' + name)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'evidence', 'captures', 'output-dir'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    parser.add_argument('--asset-prefix', default='./review-assets/')
    build(parser.parse_args())
