"""Draw an unlabeled, facility-level review sample before reading desk-review outcomes."""

from argparse import ArgumentParser
from collections import Counter
import csv
from hashlib import sha256
import json
from math import ceil, sqrt
from pathlib import Path
import random
from statistics import NormalDist
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source_selection'))
from evaluate_selectors import EXCLUDED_METHODS, dataset_cases


CONFIDENCE = .95
TARGET_MARGIN = .05
MAX_DIRECT_CHECKS = 50
SEED = 20261121
Z = NormalDist().inv_cdf((1 + CONFIDENCE) / 2)
PUBLISHER_COLUMNS = {'FDR': 'fdr', 'IFFCO Tokio': 'iffco', 'sbi_general_archived_quality_disclosure': 'sbi'}
EXTERNAL_PUBLISHERS = {'IFFCO Tokio': 'IFFCO Tokio', 'sbi_general_archived_quality_disclosure': 'SBI General'}


def required_count(population):
    uncorrected = Z**2 * .25 / TARGET_MARGIN**2
    return ceil(population * uncorrected / (population + uncorrected - 1))


def margin(population, checks):
    if population == checks:
        return 0.0
    return Z * sqrt(.25 / checks * (population - checks) / (population - 1))


def source_links(urls):
    return '\n'.join(sorted({url for url in urls if isinstance(url, str)
                             and urlsplit(url).scheme in {'http', 'https'} and urlsplit(url).hostname}))


def worklist_row(index, identity, case):
    row = {'review_order': index, 'case_id': f'H{index:03d}', 'facility_id': identity,
           'facility': case['name'], 'pin': case['pin'], 'source_group': case['stratum']}
    for publisher, column in PUBLISHER_COLUMNS.items():
        claims = [item for item in case['candidates'] if item['source'] == publisher]
        # Parallel lines preserve the connection between each figure, date and citation.
        row[f'{column}_beds'] = '\n'.join(str(item['value']) for item in claims)
        row[f'{column}_date'] = '\n'.join(item['date'] or 'Unspecified' for item in claims)
        if publisher != 'FDR':
            row[f'{column}_source_urls'] = '\n'.join(source_links([item.get('url')]) for item in claims)
    row['fdr_facility_source_leads'] = source_links(case.get('candidate_source_urls', []))
    return {**row, 'review_status': 'pending', 'verified_beds': '', 'evidence_as_of': '',
            'evidence_source_url': '', 'full_page_screenshot_path': '', 'review_notes': ''}


def link_review_rows(branch, claims):
    index = 0
    for claim in claims:
        publisher = EXTERNAL_PUBLISHERS.get(claim['source'])
        url = source_links([claim.get('url')])
        if not publisher or not url:
            continue
        index += 1
        yield {'id': f'{branch["case_id"]}-{index:02d}', 'title': f'{branch["facility"]} · {publisher}',
               'source_url': url, 'case_id': branch['case_id'], 'branch_pin': branch['pin'],
               'publisher': publisher, 'published_beds': claim['value'], 'published_as_of': claim['date'] or 'Unspecified'}


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build(local, output):
    previously_sampled = {row['identity'] for iteration in ('iteration-01', 'iteration-02')
                          for row in json.loads((local / iteration / 'sample.json').read_text(encoding='utf8'))}
    cases = dataset_cases(local / 'iteration-02')
    frame = {key: case for key, case in cases.items() if key not in previously_sampled
             and case['method'] not in (EXCLUDED_METHODS | {'direct_review'})
             and case['name'] and case['pin'] and len({item['value'] for item in case['candidates']}) > 1}
    if not frame:
        raise ValueError('No eligible conflicting branches in the supplied checkpoints.')
    size = required_count(len(frame))
    sample = random.Random(SEED).sample(sorted(frame), size)
    rows = [worklist_row(index, identity, frame[identity]) for index, identity in enumerate(sample, start=1)]
    links = [link for branch in rows for link in link_review_rows(branch, frame[branch['facility_id']]['candidates'])]
    output.mkdir(parents=True, exist_ok=False)
    worklist = output / 'human-capacity-review-worklist.csv'
    write_csv(worklist, rows)
    receipt = {'frame_branches': len(frame), 'frame_by_source_group': dict(Counter(case['stratum'] for case in frame.values())),
               'sampling_unit': 'one facility branch and its competing capacity claims', 'seed': SEED,
               'design': 'simple random sample without replacement from sorted branch IDs',
               'confidence': CONFIDENCE, 'target_margin': TARGET_MARGIN, 'required_branches': size,
               'worst_case_margin_at_required_size': margin(len(frame), size), 'browser_check_threshold': MAX_DIRECT_CHECKS,
               'worst_case_margin_at_threshold': margin(len(frame), min(len(frame), MAX_DIRECT_CHECKS)),
               'sample_by_source_group': dict(Counter(frame[key]['stratum'] for key in sample)),
               'desk_review_outcomes_used': False, 'checks_performed': 0,
               'frame_sha256': sha256('\n'.join(sorted(frame)).encode()).hexdigest(),
               'worklist_sha256': sha256(worklist.read_bytes()).hexdigest()}
    if links:
        link_queue = output / 'publisher-links-review-queue.csv'
        write_csv(link_queue, links)
        receipt['source_link_claim_rows'] = len(links)
        receipt['source_link_branches'] = len({claim['case_id'] for claim in links})
        receipt['source_link_queue_sha256'] = sha256(link_queue.read_bytes()).hexdigest()
    (output / 'sampling-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--local', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.local, args.output)
