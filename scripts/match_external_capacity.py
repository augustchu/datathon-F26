"""Compare dated outside bed or doctor reports with a private FDR checkpoint."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit


TOTAL_SCOPE = 'whole_facility_reported_total'
SCOPE_ALIASES = {
    'total': TOTAL_SCOPE, 'whole_facility': TOTAL_SCOPE, 'whole_facility_beds': TOTAL_SCOPE,
    'whole_facility_doctors': TOTAL_SCOPE, 'total_beds': TOTAL_SCOPE, 'total_doctors': TOTAL_SCOPE,
    'reported_total': TOTAL_SCOPE, 'whole_facility_total': TOTAL_SCOPE,
    'sanctioned': 'sanctioned_beds', 'licensed': 'licensed_beds', 'operating': 'operating_beds',
    'staffed': 'staffed_beds',
}
PLACE_ALIASES = {
    'delhi nct': 'delhi', 'nct delhi': 'delhi', 'nct of delhi': 'delhi', 'new delhi': 'delhi',
    'national capital territory of delhi': 'delhi', 'orissa': 'odisha', 'pondicherry': 'puducherry',
    'tamilnadu': 'tamil nadu', 'uttaranchal': 'uttarakhand', 'u p': 'uttar pradesh',
    'bengaluru': 'bangalore', 'gurugram': 'gurgaon', 'belagavi': 'belgaum',
}
REVIEW_NAME_PREFIXES = {
    'gh': ('general hospital', 'government hospital'), 'dh': ('district hospital',),
    'thqh': ('taluk headquarters hospital',), 'thq': ('taluk headquarters hospital',),
    'th': ('taluk hospital',), 'chc': ('community health centre',),
    'phc': ('primary health centre',), 'mch': ('medical college hospital',),
}
REVIEW_NAME_WORDS = {'govt': 'government', 'center': 'centre', 'hq': 'headquarters'}
PIN_PATTERN = re.compile(r'(?<!\d)([1-9]\d{5})(?!\d)')
COUNT_PATTERN = re.compile(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.0+)?')
CLAIM_COLUMNS = (
    'source', 'publisher_family', 'source_record_id', 'source_record_ids', 'source_url', 'source_urls',
    'source_date', 'reporting_period', 'registration_valid_until', 'name', 'state', 'city', 'raw_source_pin',
    'pin', 'address', 'facility_id',
    'fdr_name', 'fdr_state', 'fdr_city', 'fdr_pin', 'fdr_address', 'fdr_location_hold_reason', 'linkage_basis',
    'field', 'scope', 'raw_source_value', 'raw_source_values', 'external_value', 'fdr_status',
    'fdr_value', 'comparison', 'period_comparability', 'other_external_reports', 'fdr_publisher_overlap',
)
QUEUE_COLUMNS = (*CLAIM_COLUMNS, 'review_reason')


def key(value):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', str(value or '')).casefold()))


def place_key(value):
    value = key(value)
    return PLACE_ALIASES.get(value, value)


def address_key(value):
    normalized = key(PIN_PATTERN.sub('', value or ''))
    tokens = normalized.split()
    if len(tokens) >= 5 or (len(tokens) >= 4 and any(re.search(r'\d', token) for token in tokens)):
        return normalized
    return ''


def review_name_keys(name_key):
    words = name_key.replace('head quarters', 'headquarters').split()
    if len(words) < 2:
        return set()
    prefixes = REVIEW_NAME_PREFIXES.get(words[0], (words[0],))
    return {tuple(sorted(REVIEW_NAME_WORDS.get(word, word) for word in (*prefix.split(), *words[1:])))
            for prefix in prefixes}


def read_checkpoint(path):
    with path.open(encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            if line_number == 1 and set(row) == {'columns'}:
                continue
            yield row


def index_checkpoint(path):
    indexed = {}
    for row in read_checkpoint(path):
        identity = row['facility_id']
        if identity in indexed:
            raise ValueError(f'Duplicate facility_id {identity} in {path}')
        indexed[identity] = row
    return indexed


def fdr_source_hosts(path):
    by_branch = defaultdict(set)
    for record in read_checkpoint(path):
        identity = record.get('facility_id')
        if identity and record.get('source') == 'fdr':
            for url in record.get('source_urls') or []:
                try:
                    host = urlsplit(url).hostname
                except (TypeError, ValueError):
                    continue
                if host:
                    by_branch[identity].add(host.removeprefix('www.'))
    return by_branch


def publisher_overlap(claim, source_hosts):
    cited = source_hosts.get(claim['facility_id']) if source_hosts is not None else None
    if not cited:
        return 'fdr_publisher_provenance_unknown'
    host = urlsplit(claim['source_url']).hostname.removeprefix('www.')
    repeats = any(host == site or host.endswith('.' + site) or site.endswith('.' + host) for site in cited)
    return 'publisher_already_cited_by_fdr' if repeats else 'publisher_not_listed_for_fdr_branch'


def find_pin(postcode, address):
    postcode = str(postcode or '').strip()
    explicit = re.fullmatch(r'([1-9]\d{5})(?:\.0+)?', postcode)
    in_address = set(PIN_PATTERN.findall(address or ''))
    if postcode and not explicit:
        return '', 'invalid_explicit_pin'
    if explicit:
        value = explicit.group(1)
        return (value, '') if not in_address - {value} else (value, 'pin_address_conflict')
    if len(in_address) == 1:
        return in_address.pop(), ''
    return '', 'multiple_address_pins' if in_address else ''


def count_value(raw):
    if not COUNT_PATTERN.fullmatch(raw):
        return None
    value = int(raw.replace(',', '').partition('.')[0])
    return value if value > 0 else None


def source_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = csv.DictReader(handle)
        header = set(rows.fieldnames or [])
        required = {'source', 'source_url', 'source_record_id', 'name'}
        if not required <= header or not {'beds', 'doctors'} & header:
            raise ValueError(f'{path} needs source, source_url, source_record_id, name and beds or doctors')
        for line_number, raw in enumerate(rows, 2):
            if None in raw:
                raise ValueError(f'{path}:{line_number} has more values than the header')
            row = {field: (value or '').strip() for field, value in raw.items()}
            host = urlsplit(row['source_url']).hostname
            if (not all(row[field] for field in required) or not host
                    or urlsplit(row['source_url']).scheme not in {'http', 'https'}):
                raise ValueError(f'{path}:{line_number} needs a source, record ID, facility and public URL')
            if row.get('source_date'):
                date.fromisoformat(row['source_date'])
            row['publisher_family'] = row.get('publisher_family') or host.removeprefix('www.')
            row['reporting_period'] = (row.get('reporting_period') or row.get('source_period')
                                       or row.get('source_date', ''))
            yield row


def geog_conflicts(source, branch):
    source_state = place_key(source.get('state'))
    source_city = place_key(source.get('city'))
    branch_state = place_key(branch.get('state') or branch.get('postal_state'))
    branch_places = {place_key(branch.get(field)) for field in ('city', 'district', 'postal_district')} - {''}
    if source_state and branch_state and source_state != branch_state:
        return 'state_disagreement'
    if source_city and branch_places and source_city not in branch_places:
        return 'city_disagreement'
    return ''


def build_branch_index(branches):
    by_name = defaultdict(list)
    by_pin = defaultdict(list)
    for branch in sorted(branches, key=lambda record: record['facility_id']):
        name_key = key(branch.get('name'))
        if not name_key:
            continue
        pin, issue = find_pin(branch.get('pin'), branch.get('address'))
        indexed = {**branch, 'name_key': name_key, 'pin_key': pin, 'pin_issue': issue,
                   'address_key': address_key(branch.get('address')), 'review_name_keys': review_name_keys(name_key)}
        by_name[name_key].append(indexed)
        if pin:
            by_pin[pin].append(indexed)
    return by_name, by_pin


def resolve_branch(source, by_name, by_pin):
    name = key(source['name'])
    candidates = by_name.get(name, [])
    pin, pin_issue = find_pin(source.get('pin'), source.get('address'))
    address = address_key(source.get('address'))
    exact_pin = [branch for branch in candidates if pin and branch['pin_key'] == pin]
    exact_address = [branch for branch in candidates if address and branch['address_key'] == address]
    if exact_pin and exact_address and not any(branch in exact_pin for branch in exact_address):
        return None, '', [('conflicting_exact_identity_signals', branch) for branch in exact_pin + exact_address]
    basis = 'exact_name_and_pin' if len(exact_pin) == 1 else 'exact_name_and_address'
    exact = exact_pin if len(exact_pin) == 1 else exact_address
    if len(exact) == 1:
        branch = exact[0]
        different_pin = bool(pin and branch['pin_key'] and pin != branch['pin_key'])
        hold = (pin_issue or branch['pin_issue'] or ('different_pin' if different_pin else '')
                or geog_conflicts(source, branch)
                or ('fdr_location_hold' if str(branch.get('location_routing_eligible')).casefold() != 'true' else ''))
        return (None, '', [(hold, branch)]) if hold else (branch, basis, [])
    if exact_pin:
        return None, '', [('ambiguous_name_and_pin', branch) for branch in exact_pin]
    if exact_address:
        return None, '', [('ambiguous_name_and_address', branch) for branch in exact_address]
    if source.get('city') and source.get('state'):
        same_place = [branch for branch in candidates if branch.get('state') and branch.get('city')
                      and not geog_conflicts(source, branch)]
        if same_place:
            pending = [(pin_issue or ('different_pin' if pin and branch['pin_key'] else 'no_matching_pin'), branch)
                       for branch in same_place]
            return None, '', pending
    aliases = review_name_keys(name)
    compatible = [branch for branch in by_pin.get(pin, []) if not geog_conflicts(source, branch)]
    alias_matches = [('similar_name_same_pin', branch) for branch in compatible
                     if SequenceMatcher(None, name, branch['name_key']).ratio() >= 0.82]
    if aliases:
        alias_matches = [('name_alias_same_pin', branch) for branch in compatible
                         if aliases & branch['review_name_keys']] or alias_matches
    return None, '', alias_matches


def source_claims(source):
    for field, column, scope_column in (('capacity', 'beds', 'bed_scope'),
                                         ('numberDoctors', 'doctors', 'doctor_scope')):
        raw = source.get(column, '')
        if raw:
            scope_key = key(source.get(scope_column)).replace(' ', '_')
            yield field, SCOPE_ALIASES.get(scope_key, scope_key) or TOTAL_SCOPE, raw, count_value(raw)


def comparison_status(scope, external, status, observed):
    if external is None:
        return 'invalid_external_count'
    if scope != TOTAL_SCOPE:
        return 'different_count_scope'
    if status == 'reported':
        return 'external_agreement' if external == observed else 'external_conflict'
    if status in {'missing', 'unparseable'}:
        return 'candidate_for_' + status
    return 'no_comparable_fdr_audit_value'


def evidence_row(source, branch, field, scope, raw, external, audits, basis=''):
    identity = branch['facility_id']
    observed = audits.get(field, {}).get(identity, {})
    status = observed.get('status', 'not_loaded')
    return {
        **{column: source.get(column, '') for column in (
            'source', 'publisher_family', 'source_record_id', 'source_url', 'source_date', 'reporting_period',
            'registration_valid_until',
            'name', 'state', 'city', 'address',
        )},
        'raw_source_pin': source.get('pin', ''),
        'pin': find_pin(source.get('pin'), source.get('address'))[0], 'facility_id': identity,
        **{f'fdr_{column}': branch.get(column, '') for column in ('name', 'state', 'city', 'address')},
        'fdr_pin': branch['pin_key'], 'fdr_location_hold_reason': branch.get('location_hold_reason', ''),
        'linkage_basis': basis,
        'field': field, 'scope': scope, 'raw_source_value': raw, 'external_value': external,
        'fdr_status': status, 'fdr_value': observed.get('value'),
        'comparison': comparison_status(scope, external, status, observed.get('value')),
        'period_comparability': 'unknown_fdr_reporting_period',
    }


def deduplicate(claims):
    groups = defaultdict(list)
    for claim in claims:
        identity = tuple(claim[column] for column in (
            'source', 'publisher_family', 'facility_id', 'field', 'scope', 'external_value',
            'source_date', 'reporting_period', 'registration_valid_until',
        ))
        groups[identity].append(claim)
    for duplicates in groups.values():
        claim = min(duplicates, key=lambda row: (row['source_record_id'], row['source_url']))
        yield {**claim, **{
            output: json.dumps(sorted({row[column] for row in duplicates}), ensure_ascii=False)
            for output, column in (('source_record_ids', 'source_record_id'), ('source_urls', 'source_url'),
                                   ('raw_source_values', 'raw_source_value'))
        }}


def summarize_external_reports(claims):
    groups = defaultdict(list)
    for claim in claims:
        groups[(claim['facility_id'], claim['field'], claim['scope'])].append(claim)
    for group in groups.values():
        for claim in group:
            others = [other for other in group if other is not claim and
                      (other['publisher_family'] != claim['publisher_family'] or
                       other['external_value'] != claim['external_value'])]
            comparable = [other for other in others if claim['reporting_period'] and
                          other['reporting_period'] == claim['reporting_period']]
            if any(other['external_value'] != claim['external_value'] for other in comparable):
                status = 'different_values_same_reported_period'
            elif any(other['external_value'] != claim['external_value'] for other in others):
                status = 'different_values_periods_may_differ'
            elif comparable:
                status = 'matching_values_same_reported_period'
            else:
                status = 'matching_values_period_unconfirmed' if others else 'no_other_publisher_report'
            claim['other_external_reports'] = status


def write_rows(path, rows, columns):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(entities, target_paths, sources, output, location_records=None):
    audits = {field: index_checkpoint(path) for field, path in target_paths.items()}
    eligible_ids = set().union(*(set(audit) for audit in audits.values()))
    branches = [branch for branch in read_checkpoint(entities) if branch['facility_id'] in eligible_ids]
    by_name, by_pin = build_branch_index(branches)
    source_hosts = fdr_source_hosts(location_records) if location_records else None
    source_counts = defaultdict(Counter)
    claims, queue = [], []
    for path in sources:
        for source in source_rows(path):
            tally = source_counts[source['source']]
            tally['input_rows'] += 1
            values = list(source_claims(source))
            if not values:
                tally['rows_without_count'] += 1
                continue
            branch, basis, pending = resolve_branch(source, by_name, by_pin)
            if branch:
                tally['exact_branch_rows'] += 1
                for field, scope, raw, value in values:
                    row = evidence_row(source, branch, field, scope, raw, value, audits, basis)
                    if value is None:
                        queue.append({**row, 'review_reason': 'invalid_external_count'})
                    else:
                        claims.append(row)
                continue
            tally['candidate_review_rows' if pending else 'unlinked_rows'] += 1
            for reason, candidate in pending:
                for field, scope, raw, value in values:
                    row = evidence_row(source, candidate, field, scope, raw, value, audits)
                    queue.append({**row, 'comparison': 'branch_link_unresolved', 'review_reason': reason})
    matched = list(deduplicate(claims))
    summarize_external_reports(matched)
    for row in [*matched, *queue]:
        row['fdr_publisher_overlap'] = publisher_overlap(row, source_hosts)
    for row in matched:
        reasons = []
        if row['comparison'] in {'external_conflict', 'different_count_scope'}:
            reasons.append(row['comparison'])
        if row['other_external_reports'].startswith('different_values_'):
            reasons.append(row['other_external_reports'])
        if reasons:
            queue.append({**row, 'review_reason': '|'.join(reasons)})
    summary = {
        'fdr_facilities_in_target_checkpoints': len(eligible_ids),
        'fdr_facilities_with_branch_checkpoint': len(branches),
        'distinct_matched_branches': len({row['facility_id'] for row in matched}),
        'distinct_branches_with_missing_capacity_candidates': len({
            row['facility_id'] for row in matched if row['field'] == 'capacity'
            and row['comparison'] == 'candidate_for_missing'
        }),
        'comparison_claims': dict(Counter(row['comparison'] for row in matched)),
        'matched_claims_by_fdr_publisher_overlap': dict(Counter(row['fdr_publisher_overlap'] for row in matched)),
        'missing_branches_with_new_publisher_by_field': {
            field: len({row['facility_id'] for row in matched if row['field'] == field
                        and row['comparison'] == 'candidate_for_missing'
                        and row['fdr_publisher_overlap'] == 'publisher_not_listed_for_fdr_branch'})
            for field in sorted(audits)
        },
        'linkage_basis': dict(Counter(row['linkage_basis'] for row in matched)),
        'review_queue_claims': dict(Counter(row['review_reason'] for row in queue)),
        'distinct_facilities_for_review': len({row['facility_id'] for row in queue}),
        'duplicate_claim_rows_collapsed': len(claims) - len(matched),
        'by_source': {},
    }
    for source, tally in sorted(source_counts.items()):
        source_claims_matched = [row for row in matched if row['source'] == source]
        missing_ids = {row['facility_id'] for row in source_claims_matched if row['field'] == 'capacity'
                       and row['comparison'] == 'candidate_for_missing'}
        summary['by_source'][source] = {
            **tally, 'matched_claims': len(source_claims_matched),
            'distinct_matched_branches': len({row['facility_id'] for row in source_claims_matched}),
            'distinct_branches_with_missing_capacity_candidates': len(missing_ids),
            'comparison_claims_by_field': {
                field: dict(Counter(row['comparison'] for row in source_claims_matched if row['field'] == field))
                for field in sorted({row['field'] for row in source_claims_matched})
            },
        }
    output.mkdir(parents=True, exist_ok=True)
    write_rows(output / 'matched_claims.csv', matched, CLAIM_COLUMNS)
    write_rows(output / 'review_queue.csv', queue, QUEUE_COLUMNS)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entities', type=Path, required=True)
    parser.add_argument('--targets', type=Path, required=True, help='FDR bed-count audit checkpoint')
    parser.add_argument('--doctor-targets', type=Path, help='Optional FDR doctor-count audit checkpoint')
    parser.add_argument('--location-records', type=Path, help='Optional location checkpoint for FDR publisher overlap')
    parser.add_argument('--source', type=Path, required=True, action='append')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    targets = {'capacity': args.targets}
    if args.doctor_targets:
        targets['numberDoctors'] = args.doctor_targets
    summary = run(args.entities, targets, args.source, args.output_dir, args.location_records)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
