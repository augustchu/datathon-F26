"""Build a frozen, source-attributed supplement for each original facility row."""

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import xgboost as xgb

from model_sources import (AS_OF, FIELDS, PUBLISHERS, best_index, read_json, read_reviews,
                           score_candidates, write_json)


def read_csv(path):
    with path.open(encoding='utf-8', newline='') as handle:
        yield from csv.DictReader(handle)


def read_checkpoint(path):
    with path.open(encoding='utf-8') as handle:
        for number, line in enumerate(handle):
            row = json.loads(line)
            if number != 0 or set(row) != {'columns'}:
                yield row


def int_count(value):
    try:
        parsed = float(value)
        return int(parsed) if 0 < parsed < 1000000 and parsed.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def source_url(row):
    return row.get('source_url') or next(iter(row.get('source_urls') or []), '')


def take_columns(rows, columns):
    return [{column: row.get(column) for column in columns} for row in rows]


def source_order(source, priors):
    return priors.get(source, 0.5), source == 'FDR'


def prefer_claim(candidates, priors):
    if not candidates:
        return None
    return max(candidates, key=lambda row: (*source_order(row['source'], priors), row.get('date') or ''))


def public_url(value):
    if not isinstance(value, str) or value.strip().casefold() in {'none', 'null', 'nan', 'n/a', 'na', '[]', '0'}:
        return None
    candidate = value.strip().rstrip('/')
    url = candidate if '://' in candidate else 'https://' + candidate
    try:
        parsed = urlsplit(url)
        if parsed.scheme in {'https', 'http'} and not parsed.username and parsed.hostname and not re.search(r'\s', url):
            if re.fullmatch(r'[\w.-]+\.[a-zA-Z]{2,}', parsed.hostname):
                return url
    except ValueError:
        pass
    return None


def count_references(branches, count_path):
    claims = defaultdict(list)
    for row in read_csv(count_path):
        identity, field, source = row['facility_id'], row['field'], row['source']
        if identity not in branches or row['scope'] != 'whole_facility_reported_total':
            continue
        if source not in PUBLISHERS or row['fdr_publisher_overlap'] != 'publisher_not_listed_for_fdr_branch':
            continue
        value = int_count(row['external_value'])
        if value is not None:
            claims[(identity, field)].append({'source': source, 'value': value, 'date': row['source_date'],
                                              'url': row['source_url'], 'record_id': row['source_record_id']})
    return claims


def numeric_selection(field, audit, candidates, manual, recovered, desc, routable, model, use_model, priors):
    original = int_count(audit.get('value')) if audit.get('status') == 'reported' else None
    record = {'original_reported': original, 'original_audit_status': audit.get('status', 'unlinked'),
              'value': None, 'method': 'missing', 'source': None, 'source_url': None, 'source_date': None}
    if not routable:
        return {**record, 'method': 'branch_identity_hold'}
    if manual:
        if manual['status'] == 'unresolved':
            return {**record, 'method': 'review_unresolved', 'source_url': manual['evidence_url']}
        return {**record, 'value': manual['verified_value'], 'method': 'direct_review', 'source': 'review evidence',
                'source_url': manual['evidence_url'], 'source_date': manual.get('evidence_date') or None}

    candidates = list(candidates)
    if original is not None:
        candidates.append({'source': 'FDR', 'value': original, 'date': '', 'url': None,
                           'candidate_source_urls': audit.get('candidate_source_urls', [])})
    if recovered:
        provenance = recovered.get('provenance') or ''
        url = provenance.split(' ', 1)[0]
        return {**record, 'value': int_count(recovered['value']), 'source': 'approved source recovery',
                'source_url': public_url(url), 'method': 'approved_source_recovery',
                'excerpt': recovered.get('detail')}
    if audit.get('status') not in {'reported', 'missing', 'unparseable'}:
        return {**record, 'method': 'original_count_hold'}

    pick = prefer_claim(candidates, priors)
    selection = 'publisher_default'
    if len({candidate['value'] for candidate in candidates}) > 1 and model:
        scores = score_candidates(model, field, candidates)
        trial = candidates[best_index(scores, candidates, priors)]
        record['experimental_xgboost_source'] = trial['source']
        record['experimental_xgboost_value'] = trial['value']
        if use_model:
            pick, selection = trial, 'xgboost_source_selector'

    if pick:
        if pick['source'] == 'FDR':
            method = ('fdr_reported_contested' if len({candidate['value'] for candidate in candidates}) > 1
                      else 'fdr_reported')
        else:
            method = 'dated_external_report' if pick.get('date') else 'undated_external_report'
        return {**record, 'value': pick['value'], 'method': method, 'selection': selection,
                'source': pick['source'], 'source_url': pick.get('url'), 'source_date': pick.get('date') or None,
                'candidate_source_urls': pick.get('candidate_source_urls', [])}
    if field == 'capacity' and desc:
        return {**record, 'value': int_count(desc['description_capacity']), 'source': 'FDR description',
                'source_url': desc.get('source_url'), 'source_id': desc.get('source_id'),
                'method': 'explicit_fdr_description', 'excerpt': desc.get('evidence')}
    return record


def contact_selection(contacts, priors):
    groups = defaultdict(dict)
    for row in contacts:
        if row['link_status'] not in {'anchor', 'linked'} or not source_url(row):
            continue
        kind = row['field']
        candidate = {'value': row['value'], 'source': 'FDR' if row['source'] == 'fdr' else row['source'],
                     'source_url': source_url(row)}
        previous = groups[kind].get(row['value'])
        if not previous or source_order(candidate['source'], priors) > source_order(previous['source'], priors):
            groups[kind][row['value']] = candidate
    return {kind: sorted(rows.values(), key=lambda row: source_order(row['source'], priors), reverse=True)
            for kind, rows in groups.items()}


def best_website(row, reports, review, priors):
    if review and review['status'] == 'supported':
        return {'value': 'https://' + review['verified_value'], 'source': 'review evidence',
                'method': 'direct_review', 'source_url': review['evidence_url']}
    official = public_url(row.get('officialWebsite'))
    candidates = [{'value': official, 'source': 'FDR', 'url': official}] if official else []
    candidates.extend({'value': report['value'], 'source': report['source'], 'url': report['source_url'],
                       'date': report['source_date']} for report in reports if report['field'] == 'website'
                      and report['comparison'] != 'unusable_contact' and public_url(report['value']))
    pick = prefer_claim(candidates, priors)
    return ({'value': pick['value'], 'source': pick['source'], 'source_url': pick['url'],
             'method': 'reported_website_candidate'} if pick else None)


def records_by_branch(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['facility_id']].append(row)
    return grouped


def original_works(snapshot, expected_length):
    inputs = {}
    for row in read_checkpoint(snapshot / 'location_records.jsonl'):
        if row['source'] == 'fdr':
            position = int(row['input_position'])
            if position in inputs:
                raise ValueError('FDR location checkpoint has duplicate row positions')
            inputs[position] = row
    if len(inputs) != expected_length or set(inputs) != set(range(expected_length)):
        raise ValueError('Raw and location checkpoints disagree about the row count')
    return inputs


def build(args):
    report = read_json(args.model_dir / 'model_evaluation.json')
    priors = {source: row['smoothed_hit_rate'] for source, row in report['pooled_publisher_scores'].items()}
    models = {}
    for field, result in report['fields'].items():
        if result['xgboost_trained']:
            model = xgb.Booster()
            model.load_model(args.model_dir / f'{field}_selector.json')
            models[field] = model

    reviews = read_reviews(args.reviews)
    reviewed = {(sample['identity'], sample['field']): reviews[sample['case_id']]
                for sample in read_json(args.sample)}
    raw = list(read_checkpoint(args.snapshot / 'raw_facilities.jsonl'))
    anchors = original_works(args.snapshot, len(raw))
    anchors_by_branch = records_by_branch(row for row in anchors.values() if row.get('facility_id'))
    branches = {row['facility_id']: row for row in read_checkpoint(args.snapshot / 'entities.jsonl')
                if row['facility_id'] in anchors_by_branch}
    previous = read_json(args.previous_reviews)
    for decision in previous:
        matches = [identity for identity, row in branches.items() if row['name'] == decision['name']
                   and str(row.get('pin')) == decision['pin']]
        if len(matches) != 1:
            raise ValueError(f'Previous review is missing its original branch: {decision["name"]}')
        value = decision.get('supported_beds')
        reviewed.setdefault((matches[0], 'capacity'), {'status': 'supported' if value else 'unresolved',
                                                       'verified_value': value, 'evidence_url': decision['source_url']})
    claims = count_references(branches, args.count_links)
    audits = {field: {row['facility_id']: row for row in read_checkpoint(args.snapshot / path)}
              for field, path in zip(FIELDS, ('capacity_target_audit.jsonl', 'number_doctors_target_audit.jsonl'))}
    contacts = records_by_branch(read_checkpoint(args.snapshot / 'contact_evidence.jsonl'))
    services = records_by_branch(read_checkpoint(args.snapshot / 'service_verification.jsonl'))
    estimates = records_by_branch(read_checkpoint(args.snapshot / 'count_model_estimates.jsonl'))
    recovered_services = records_by_branch(
        row for row in read_checkpoint(args.approved_services) if row['stance'] == 'supports')
    other = records_by_branch(read_csv(args.field_links))
    recoveries = {(row['facility_id'], row['field']): row for row in read_checkpoint(args.approved_updates)
                  if row['method'] == 'source_recovered' and int_count(row.get('value'))}
    descriptions = {row['facility_id']: row for row in read_json(args.description_capacity)['records']
                    if row['extraction_status'] == 'extracted' and row['reported_capacity'] is None
                    and int_count(row.get('description_capacity'))}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    selection_counts = {field: Counter() for field in FIELDS}
    decisions = {}
    for identity, branch in branches.items():
        urls = sorted({url for anchor in anchors_by_branch[identity] for url in anchor.get('source_urls') or [] if url})
        for field in FIELDS:
            decision = numeric_selection(field, {**audits[field].get(identity, {}), 'candidate_source_urls': urls},
                                         claims[(identity, field)], reviewed.get((identity, field)),
                                         recoveries.get((identity, field)), descriptions.get(identity),
                                         branch.get('location_routing_eligible') is True, models.get(field),
                                         report['fields'][field]['default'] == 'xgboost', priors)
            decisions[(identity, field)] = decision
            selection_counts[field][decision['method']] += 1

    flat_path = args.output_dir / 'facility_values.csv'
    facility_path = args.output_dir / 'facilities.jsonl'
    provenance_path = args.output_dir / 'count_decisions.jsonl'
    ancillary = Counter()
    with flat_path.open('w', newline='', encoding='utf-8') as table, facility_path.open('w', encoding='utf-8') as output:
        writer = csv.writer(table)
        writer.writerow(('row_index', 'facility_id', 'name', 'state', 'district', 'city', 'address', 'pin',
                         'routing_eligible', 'latitude', 'longitude', 'capacity', 'capacity_status', 'capacity_source',
                         'capacity_source_url', 'numberDoctors', 'doctor_status', 'doctor_source', 'doctor_source_url',
                         'yearEstablished', 'website', 'published_phone', 'phone_source_url', 'published_email',
                         'documented_services'))
        for index, row in enumerate(raw):
            anchor = anchors[index]
            if str(row.get('unique_id')) != str(anchor.get('source_record_id')):
                raise ValueError(f'Raw FDR row {index} has a different record ID from its location checkpoint')
            identity = anchor.get('facility_id')
            branch = branches.get(identity, {})
            geo = {key: branch.get(key) for key in ('name', 'state', 'city', 'district', 'pin', 'address',
                                                    'lat', 'lon', 'location_routing_eligible', 'location_hold_reason',
                                                    'location_review_flags', 'location_source_urls')}
            routable = geo['location_routing_eligible'] is True
            if not routable:
                geo['lat'] = geo['lon'] = None
            published_contacts = contact_selection(contacts[identity], priors)
            website = best_website(row, other[identity], reviewed.get((identity, 'website')), priors) if routable else None
            year = int_count(row.get('yearEstablished')) if routable else None
            if year and not 1600 <= year <= AS_OF.year:
                year = None
            count_fields = {field: decisions.get((identity, field), {'value': None, 'method': 'branch_identity_hold'})
                            for field in FIELDS}
            public_services = take_columns((s for s in services[identity] if s['verification_status'] in
                                            {'single_direct_source', 'corroborated'}),
                                           ('service', 'verification_status', 'supporting_urls',
                                            'evidence_scopes', 'scope_caveat'))
            known_services = {item['service'] for item in public_services}
            for claim in recovered_services[identity]:
                if claim['service'] not in known_services:
                    public_services.append({'service': claim['service'], 'verification_status': 'approved_source_recovery',
                                            'supporting_urls': claim['source_urls'], 'evidence_scopes': [claim['scope']],
                                            'excerpt': claim['excerpt']})
                    known_services.add(claim['service'])
            record = {'source_row_index': index, 'facility_id': identity, 'link_status': anchor.get('link_status'),
                      'original': row, 'prepared_location': geo, 'count_fields': count_fields,
                      'year_established': {'value': year, 'source': 'FDR', 'method': 'fdr_reported'} if year else None,
                      'website': website, 'contact_evidence': published_contacts if routable else {},
                      'service_evidence': public_services if routable else [],
                      'other_published_evidence': take_columns(other[identity], ('field', 'value', 'scope', 'source',
                                                                                 'source_date', 'source_url',
                                                                                 'fdr_publisher_overlap')) if routable else [],
                      'prior_model_estimates': take_columns(estimates[identity], ('field', 'method', 'value', 'lower',
                                                                                 'upper', 'provenance')) if routable else []}
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
            phones = record['contact_evidence'].get('phone_numbers', [])
            emails = record['contact_evidence'].get('email_addresses', [])
            ancillary.update({'year_established_fdr_rows': bool(year), 'website_fdr_rows': bool(website),
                              'phone_fdr_rows': bool(phones), 'email_fdr_rows': bool(emails),
                              'documented_service_fdr_rows': bool(record['service_evidence'])})
            csv_row = [index, identity, geo['name'] or row.get('name'), geo['state'], geo['district'], geo['city'],
                       geo['address'], geo['pin'], routable, geo['lat'] if routable else None,
                       geo['lon'] if routable else None]
            for field in FIELDS:
                decision = count_fields[field]
                csv_row.extend((decision['value'], decision['method'], decision.get('source'), decision.get('source_url')))
            csv_row.extend((year, website['value'] if website else None, phones[0]['value'] if phones else None,
                            phones[0]['source_url'] if phones else None, emails[0]['value'] if emails else None,
                            ' | '.join(item['service'] for item in record['service_evidence'])))
            writer.writerow(["'" + value if isinstance(value, str) and value.startswith(('=', '+', '@', '-')) else value
                             for value in csv_row])
    with provenance_path.open('w', encoding='utf-8') as output:
        for (identity, field), decision in sorted(decisions.items()):
            output.write(json.dumps({'facility_id': identity, 'field': field, **decision,
                                     'eligible_external_claims': claims[(identity, field)]},
                                    ensure_ascii=False, allow_nan=False) + '\n')
    outside = [row for row in read_csv(args.unlinked_fields) if row['source'] != 'IFFCO Tokio']
    with (args.output_dir / 'unlinked_public_evidence.jsonl').open('w', encoding='utf-8') as output:
        for row in outside:
            output.write(json.dumps(row, ensure_ascii=False) + '\n')
    summary = {'fdr_rows_preserved': len(raw), 'linked_branches': len(branches),
               'unlinked_public_claims_kept_separately': len(outside), 'other_field_coverage': ancillary,
               'count_methods_by_linked_branch': selection_counts,
               'effective_count_nonmissing_by_linked_branch': {
                   field: sum(row['value'] is not None for (identity, name), row in decisions.items() if name == field)
                   for field in FIELDS},
               'original_reported_routable_by_linked_branch': {
                   field: sum(decisions[(identity, field)]['original_reported'] is not None
                              and branches[identity].get('location_routing_eligible') is True for identity in branches)
                   for field in FIELDS}}
    write_json(args.output_dir / 'dataset_summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('snapshot', 'count-links', 'field-links', 'unlinked-fields', 'approved-updates', 'approved-services',
                'description-capacity', 'previous-reviews',
                'sample', 'reviews', 'model-dir', 'output-dir'):
        parser.add_argument(f'--{arg}', type=Path, required=True)
    build(parser.parse_args())
