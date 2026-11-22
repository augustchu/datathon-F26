"""Link cited facility evidence and compare it with the fields in the FDR snapshot."""

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from match_external_capacity import (build_branch_index, fdr_source_hosts, key, place_key, publisher_overlap,
                                     read_checkpoint, resolve_branch, write_rows)


OUTPUT_COLUMNS = (
    'facility_id', 'fdr_name', 'fdr_pin', 'fdr_state', 'fdr_city', 'field', 'value', 'scope',
    'fdr_value', 'comparison', 'linkage_basis', 'review_reason', 'fdr_publisher_overlap',
    'source', 'source_date', 'source_record_id', 'name', 'state', 'city', 'pin', 'address', 'excerpt',
    'source_url', 'identity_source_url',
)
EMAIL = re.compile(r'[\w.%+\-]+@[\w.\-]+\.[a-z]{2,}', re.IGNORECASE)
HOST = re.compile(r'(?<![@\w.-])(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/[^\s,;<>]*)?', re.IGNORECASE)
PHONE = re.compile(r'(?<!\d)(?:\+?\d[\d() .-]{6,17}\d)(?!\d)')
YEAR = re.compile(r'(?:1[6-9]\d{2}|20\d{2})(?:\.0+)?')
IMAGING = {
    'ct_scanners': r'\b(?:ct|cat)[ -]*(?:scan|scanner)\b|\bcomputed tomography\b',
    'mri_scanners': r'\bmri\b|\bmagnetic resonance imaging\b',
    'service_ct': r'\b(?:ct|cat)[ -]*(?:scan|scanner)\b|\bcomputed tomography\b',
    'service_mri': r'\bmri\b|\bmagnetic resonance imaging\b',
}


def raw_by_branch(raw_path, location_records):
    raw = list(read_checkpoint(raw_path))
    records = defaultdict(list)
    for location in read_checkpoint(location_records):
        if location.get('source') != 'fdr':
            continue
        position = int(location['input_position'])
        if position < 0 or position >= len(raw):
            raise ValueError('FDR location row position lies outside the supplied raw snapshot')
        row = raw[position]
        if str(row.get('unique_id')) != str(location.get('source_record_id')):
            raise ValueError('The FDR and location snapshots are not aligned')
        if location.get('facility_id'):
            records[location['facility_id']].append(row)
    return records


def contact_keys(field, raw):
    if field == 'website':
        return {urlsplit(token if '://' in token else 'https://' + token).hostname.removeprefix('www.')
                for token in HOST.findall(raw)}
    if field == 'email':
        return {token.casefold() for token in EMAIL.findall(raw)}
    phones = {re.sub(r'\D', '', token) for token in PHONE.findall(raw)}
    return {number[2:] if len(number) == 12 and number.startswith('91') else number.lstrip('0')
            for number in phones if 8 <= len(number) <= 12}


def original_comparison(field, external, records):
    def cell_values(columns):
        return [str(record[column]) for record in records for column in columns if record.get(column)]

    if field == 'yearEstablished':
        raw = cell_values(('yearEstablished',))
        years = {str(int(float(value))) for value in raw if YEAR.fullmatch(value.strip())}
        status = ('candidate_for_missing' if not years else 'external_agreement' if years == {external}
                  else 'external_conflict')
        return ', '.join(sorted(years)), status
    contacts = {'website': ('websites', 'officialWebsite'), 'phone': ('phone_numbers', 'officialPhone'),
                'email': ('email',)}
    if field in contacts:
        original = contact_keys(field, ' '.join(cell_values(contacts[field])))
        evidence = contact_keys(field, external)
        status = ('unusable_contact' if not evidence else 'candidate_for_missing' if not original else
                  'external_agreement' if original & evidence else 'different_published_contact')
        return ', '.join(sorted(original)), status
    if field in IMAGING:
        columns = ('capability', 'procedure', 'specialties') if field.startswith('service') else ('equipment',)
        present = any(re.search(IMAGING[field], raw, re.IGNORECASE) for raw in cell_values(columns))
        # Free text can contain negations or program-level claims. A mention is a lead, not agreement.
        return ('term present in FDR field' if present else '',
                'fdr_mention_found' if present else 'additional_published_evidence')
    return '', 'additional_published_detail'


def source_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'source', 'source_url', 'source_record_id', 'name', 'field', 'value', 'scope', 'excerpt'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f'Missing evidence columns in {path}')
        for number, row in enumerate(reader, 2):
            if None in row or not all(row.get(field) for field in required):
                raise ValueError(f'Incomplete evidence row {number} in {path}')
            parsed = urlsplit(row['source_url'])
            if not parsed.hostname or parsed.scheme not in {'http', 'https'}:
                raise ValueError(f'Invalid public source link on row {number} in {path}')
            yield row


def evidence(source, branch, basis, audit_source_hosts, originals, review_reason=''):
    identity = branch['facility_id'] if branch else ''
    original, comparison = '', 'unlinked_source_row'
    if branch and basis:
        original, comparison = original_comparison(source['field'], source['value'], originals[identity])
    if branch and not basis:
        comparison = 'branch_link_unresolved'
    return {
        **{column: source.get(column, '') for column in OUTPUT_COLUMNS},
        'facility_id': identity, 'fdr_name': branch.get('name', '') if branch else '',
        'fdr_pin': branch.get('pin', '') if branch else '', 'fdr_state': branch.get('state', '') if branch else '',
        'fdr_city': branch.get('city', '') if branch else '', 'fdr_value': original,
        'comparison': comparison, 'linkage_basis': basis, 'review_reason': review_reason,
        'identity_source_url': source.get('identity_source_url') or source.get('identity_url', ''),
        'fdr_publisher_overlap': publisher_overlap({**source, 'facility_id': identity}, audit_source_hosts) if branch else '',
    }


def disputed_establishment_years(claims):
    groups = defaultdict(list)
    for claim in claims:
        if claim['field'] != 'yearEstablished' or not claim['name']:
            continue
        if claim['facility_id']:
            identity = claim['facility_id']
        elif claim['state'] and claim.get('city') and claim['identity_source_url']:
            identity = tuple(key(claim[field]) for field in ('name', 'city', 'state'))
        else:
            continue
        groups[identity].append(claim)
    for group in groups.values():
        if len({row['value'] for row in group}) > 1:
            for row in group:
                yield {**row, 'review_reason': 'different_published_establishment_years'}


def run(args):
    audit_ids = {row['facility_id'] for row in read_checkpoint(args.targets)}
    branches = {row['facility_id']: row for row in read_checkpoint(args.entities) if row['facility_id'] in audit_ids}
    by_name, by_pin = build_branch_index(branches.values())
    originals = raw_by_branch(args.raw, args.location_records)
    hosts = fdr_source_hosts(args.location_records)
    existing_links = {}
    with args.count_links.open(encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            identity = row['facility_id']
            record_key = (row['source'], row['source_record_id'])
            value = (identity, key(row['name']))
            if existing_links.setdefault(record_key, value) != value:
                raise ValueError(f'Conflicting branch attachments for source row {record_key}')
    matches, reviews, unlinked = [], [], []
    seen = set()
    for path in args.source:
        for source in source_rows(path):
            claim_key = tuple(source.get(field, '') for field in (
                'source', 'source_record_id', 'source_date', 'field', 'value', 'scope'))
            if claim_key in seen:
                continue
            seen.add(claim_key)
            identity, name = existing_links.get((source['source'], source['source_record_id']), (None, ''))
            if identity in branches and name == key(source['name']):
                branch, basis, candidates = branches[identity], 'same_disclosure_row_as_count_match', []
            elif 'group' in source['scope'].casefold():
                branch, basis, candidates = None, '', []
            else:
                branch, basis, candidates = resolve_branch(source, by_name, by_pin)
                if not branch and not candidates and not source.get('pin') and source.get('state'):
                    candidates = [('same_name_and_state_without_pin', candidate)
                                  for candidate in by_name.get(key(source['name']), ())
                                  if place_key(candidate.get('state')) == place_key(source['state'])]
            if branch:
                record = evidence(source, branch, basis, hosts, originals)
                matches.append(record)
                if record['comparison'] in {'external_conflict', 'different_published_contact'}:
                    reviews.append({**record, 'review_reason': record['comparison']})
            elif candidates:
                reviews.extend(evidence(source, candidate, '', hosts, originals, reason)
                               for reason, candidate in candidates)
            else:
                unlinked.append(evidence(source, None, '', hosts, originals))

    reviews.extend(disputed_establishment_years([*matches, *unlinked]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, rows in (('field_matches.csv', matches), ('field_review.csv', reviews),
                           ('source_only_fields.csv', unlinked)):
        write_rows(args.output_dir / filename, rows, OUTPUT_COLUMNS)
    summary = {group: dict(sorted(Counter(record['field'] for record in records).items()))
               for group, records in (('linked', matches), ('review', reviews), ('unlinked', unlinked))}
    summary['comparison'] = {
        field: dict(Counter(row['comparison'] for row in matches if row['field'] == field))
        for field in summary['linked']
    }
    (args.output_dir / 'field_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('entities', 'targets', 'raw', 'location-records', 'count-links', 'output-dir'):
        parser.add_argument(f'--{arg}', type=Path, required=True)
    parser.add_argument('--source', type=Path, action='append', required=True)
    run(parser.parse_args())
