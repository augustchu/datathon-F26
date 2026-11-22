"""Freeze precision and recall samples from the archived count disclosures."""

import argparse
from collections import Counter
import csv
from hashlib import file_digest
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
import subprocess
from urllib.parse import quote


IFFCO_URL = 'https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters'
SBI_URL = 'https://content.sbigeneral.in/uploads/f22596609234455e8b24df7859027ce6.pdf'
FIELDS = ('beds', 'doctors')
SEED = 'scraper-fidelity-20261121-v1'
ROWS_PER_STRATUM = 6
SBI_SERIAL = re.compile(r'^ {0,10}(\d{1,5}) {2,}')
SBI_COUNT_CELLS = re.compile(r' {2,}(?:\d{6}|NA) {2,}(\d{2}-\d{2}-\d{4}|NA) {2,}([\d,]+|NA) {2,}([\d,]+|NA)(?= {2,})')


class IffcoDisclosureTable(HTMLParser):
    """Read the explicitly named table without running the production scraper."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table' and dict(attrs).get('id') == 'hospita-list':
            self.in_table = True
        if self.in_table and tag == 'tr':
            self.row = []
        if self.row is not None and tag in {'td', 'th'}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.cell is not None and tag in {'td', 'th'}:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        if self.row is not None and tag == 'tr':
            self.rows.append(self.row)
            self.row = None
        if tag == 'table':
            self.in_table = False


def iffco_frame(path):
    document = IffcoDisclosureTable()
    document.feed(path.read_text(encoding='utf-8'))
    header, *rows = document.rows
    expected = ['HOSPITAL NAME', 'Rohini Id.', 'Address', 'INFORMATION AS AT (Date)',
                'Total Bed strength of the Hospital', 'No. of Doctors']
    if header[:len(expected)] != expected:
        raise ValueError('The IFFCO disclosure has unexpected columns.')
    frame = {}
    for index, row in enumerate(rows, start=2):
        name, rohini, address, date, beds, doctors = row[:len(expected)]
        frame[f'{rohini}:{index}'] = {'source_url': f'{IFFCO_URL}#:~:text={quote(name, safe="")}',
                                    'table_row': index, 'name': name, 'address': address,
                                    'date': date, 'beds': beds, 'doctors': doctors}
    return frame


def sbi_frame(path):
    # This fixed-width reading of the printed columns is independent of the
    # production script's PDF word bounding boxes and horizontal table rules.
    document = subprocess.run(['pdftotext', '-layout', str(path), '-'], check=True, capture_output=True).stdout.decode()
    frame = {}
    for page_number, page in enumerate(document.split('\f'), start=1):
        for line in page.splitlines():
            serial = SBI_SERIAL.match(line)
            if not serial:
                continue
            count_cells = SBI_COUNT_CELLS.search(line)
            if not count_cells or serial[1] in frame:
                raise ValueError(f'Could not independently read SBI row {serial[1]}.')
            date, beds, doctors = count_cells.groups()
            frame[serial[1]] = {'source_url': f'{SBI_URL}#page={page_number}', 'pdf_page': page_number,
                                'date': date, 'beds': beds, 'doctors': doctors}
    if list(frame) != [str(i) for i in range(1, 17530)]:
        raise ValueError('The source-first SBI frame must contain every printed serial exactly once.')
    return frame


def scraped_frame(path):
    with path.open(newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    frame = {row['source_record_id']: row for row in rows}
    if len(frame) != len(rows):
        raise ValueError(f'{path.name} has duplicate source IDs.')
    return frame


def count(value):
    return int(value.replace(',', '')) if re.fullmatch(r'[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+', value) else 0


def freeze(sample_dir):
    claims = []
    metadata = {}
    observations = {}
    for publisher, snapshot_name, output_name, source_frame in (
        ('IFFCO Tokio', 'iffco.html', 'iffco.csv', iffco_frame),
        ('SBI General 2022', 'sbi-2022.pdf', 'sbi-2022.csv', sbi_frame),
    ):
        snapshot, output = sample_dir / snapshot_name, sample_dir / output_name
        source_rows, scraped_rows = source_frame(snapshot), scraped_frame(output)
        metadata[publisher] = {'source_rows': len(source_rows), 'scraped_rows': len(scraped_rows), 'frames': {}}
        for role, filename in (('source_snapshot', snapshot), ('scraper_output', output)):
            with filename.open('rb') as handle:
                metadata[publisher][role] = {'file': filename.name, 'sha256': file_digest(handle, 'sha256').hexdigest()}
        for metric, frame in (('precision', scraped_rows), ('recall', source_rows)):
            for field in FIELDS:
                eligible = sorted(record_id for record_id, row in frame.items() if count(row[field]) > 0)
                metadata[publisher]['frames'][f'{metric}_{field}'] = len(eligible)
                salt = f'{SEED}/{publisher}/{metric}/{field}'
                for record_id in random.Random(salt).sample(eligible, ROWS_PER_STRATUM):
                    case = f'A{len(claims) + 1:02d}'
                    source = source_rows.get(record_id, {})
                    scraped = scraped_rows.get(record_id, {})
                    sample_url = frame[record_id]['source_url']
                    claims.append({'case_id': case, 'publisher': publisher, 'metric': metric, 'field': field,
                                   'record_id': record_id, 'source_url': sample_url})
                    observations[case] = {'case_id': case, 'publisher': publisher, 'metric': metric, 'field': field,
                                          'record_id': record_id, 'source_url': sample_url,
                                          'original': source, 'scraped': scraped}
    return {'seed': SEED, 'rows_per_stratum': ROWS_PER_STRATUM, 'publishers': metadata, 'sample': claims}, observations


def score(manifest, sampled, reference_values):
    from scipy.stats import binomtest

    if set(sampled) != set(reference_values):
        raise ValueError('A source observation is needed for every frozen case.')
    ledger = []
    strata = {}
    for case in manifest['sample']:
        source = sampled[case['case_id']]['original']
        prediction = sampled[case['case_id']]['scraped']
        observed = reference_values[case['case_id']]
        if observed is not None and (type(observed) is not int or observed < 0):
            raise ValueError('Read the original as a nonnegative integer, or null if ambiguous.')
        emitted = count(prediction.get(case['field'], '')) or None
        eligible = case['metric'] == 'precision' or bool(observed)
        if observed is None or not eligible:
            decision = 'inconclusive'
        elif observed == emitted and source.get('source_url') == prediction.get('source_url'):
            decision = 'match'
        else:
            decision = 'false_positive' if case['metric'] == 'precision' else 'false_negative'
        row = {**case, 'published_count': observed, 'scraped_count': emitted, 'decision': decision}
        ledger.append(row)
        key = (case['publisher'], case['metric'], case['field'])
        strata.setdefault(key, Counter())[decision] += 1

    summaries = []
    for (publisher, metric, field), tally in strata.items():
        denominator = sum(value for decision, value in tally.items() if decision != 'inconclusive')
        interval = binomtest(tally['match'], denominator).proportion_ci(method='exact') if denominator else None
        summaries.append({'publisher': publisher, 'metric': metric, 'field': field,
                          'checks': tally.total(), 'matches': tally['match'],
                          'false_positives': tally['false_positive'], 'false_negatives': tally['false_negative'],
                          'inconclusive': tally['inconclusive'], 'denominator': denominator,
                          'estimate': tally['match'] / denominator if denominator else None,
                          'exact_binomial_95_interval': [interval.low, interval.high] if interval else None})
    return {'results': summaries, 'observations': ledger}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path, help='Folder holding both original snapshots and their released CSV extracts.')
    parser.add_argument('output', type=Path, help='Write a frozen, unlabeled audit sampling manifest.')
    parser.add_argument('--inspect', choices=('IFFCO Tokio', 'SBI General 2022'), help='Print original source fields and locators for inspection.')
    parser.add_argument('--observed-values', type=Path, help='JSON map of frozen case IDs to manually read source counts.')
    parser.add_argument('--audit-report', type=Path, help='Write aggregate rates and the sampled evidence ledger.')
    args = parser.parse_args()
    if bool(args.observed_values) != bool(args.audit_report):
        parser.error('--observed-values and --audit-report must be provided together')
    manifest, observations = freeze(args.archive)
    args.output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if args.observed_values:
        reference_values = json.loads(args.observed_values.read_text(encoding='utf-8'))
        report = score(manifest, observations, reference_values)
        args.audit_report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.inspect:
        for row in observations.values():
            if row['publisher'] != args.inspect:
                continue
            original = row['original']
            print('\t'.join(str(value) for value in (
                row['case_id'], row['metric'], row['record_id'], row['field'], original.get(row['field'], ''),
                original.get('table_row') or original.get('pdf_page'), original.get('name') or row['scraped'].get('name'),
                original.get('date'), row['source_url'],
            )))
    else:
        print(Counter((row['publisher'], row['metric'], row['field']) for row in manifest['sample']))


if __name__ == '__main__':
    main()
