"""Supplement ranked facility snapshots from explicit source descriptions."""

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'non-ml/src'))

from facility_tool.data import _array
from facility_tool.recovery_extract import COUNTS, extract_candidates

EXTRACTOR = 'explicit_fdr_description'
RECOVERABLE = {'capacity': 'whole_facility_beds', 'numberDoctors': 'whole_facility_doctors',
               'equipment': 'installed_at_branch', 'service': 'branch_service'}
YEAR = re.compile(r'\b(?:this|the|our) (?:hospital|facility|nursing home|medical cent(?:er|re)) '
                  r'(?:was )?(?:established|founded|opened) in ((?:18|19|20)\d{2})\b', re.I)
OTHER_SITE = re.compile(r'\b(?:associated|attached|affiliated|sister|partner)\s+(?:teaching\s+)?'
                        r'(?:hospital|facility|branch|centre|center)\b', re.I)
HISTORICAL_START = re.compile(r'\b(?:began|started|opened|founded|established)\s+as\b', re.I)
BLANK = {'', 'none', 'null', 'na', 'n/a', 'nan', 'unknown', '[]', '{}', '<na>'}


def rows(path):
    with path.open(encoding='utf8') as stream:
        yield from map(json.loads, stream)


def present(value):
    if value is None:
        return False
    return str(value).strip().casefold() not in BLANK


def url_for(raw):
    urls = _array(raw.get('source_urls'))
    return next((s for s in urls if s.startswith(('https://', 'http://'))), None)


def scan_descriptions(path, aliases, known_bad, as_of):
    """Collect exact evidence before any modeling cohort or target is built."""
    claims = defaultdict(lambda: defaultdict(list))
    flags = set()
    scanned = Counter()
    for row in rows(path):
        identity, raw = row['facility_id'], row['original']
        if not identity or not row['prepared_location']['location_routing_eligible']:
            continue
        desc = raw.get('description')
        if not present(desc):
            continue
        if COUNTS['capacity'].search(desc):
            flags.add(identity)
        scanned['descriptions'] += 1
        candidates = extract_candidates(desc, aliases)
        for match in YEAR.finditer(desc):
            year = int(match[1])
            if 1800 <= year <= as_of.year:
                candidates.append({'field': 'yearEstablished', 'value': year,
                                   'stance': 'supports', 'start': match.start(), 'end': match.end(), 'claim_date': ''})
        for claim in candidates:
            field, value = claim['field'], claim['value']
            excerpt = desc[claim['start']:claim['end']]
            if OTHER_SITE.search(excerpt) or field == 'capacity' and HISTORICAL_START.search(excerpt):
                scanned[f'rejected_scope:{field}'] += 1
                continue
            if field != 'yearEstablished' and claim.get('scope') != RECOVERABLE.get(field):
                continue
            if field in {'capacity', 'numberDoctors'} and (not 0 < value <= 10000
                    or field == 'capacity' and identity in known_bad):
                continue
            observed = claim['claim_date']
            if observed and date.fromisoformat(observed) > as_of:
                continue
            claims[identity][field].append({'value': value, 'stance': claim['stance'],
                                           'excerpt': excerpt,
                                           'source_url': url_for(raw), 'source_date': observed or None,
                                           'source_row_index': row['source_row_index']})
    return claims, flags, scanned


def decide(row, claims):
    """Fill blanks after publisher selection, with source copies and holds intact."""
    accepted, conflicts = {}, set()
    for field, candidates in claims.items():
        old = row['count_fields'].get(field)
        if old is not None and (old['value'] is not None or old['method'] != 'missing'):
            continue
        multiple = field in {'equipment', 'service'}
        if field == 'equipment' and present(row['original'].get('equipment')):
            continue
        if field == 'service' and row['service_evidence']:
            continue
        if field == 'yearEstablished' and row['year_established'] is not None:
            continue
        supportive = {claim['value']: claim for claim in candidates if claim['stance'] == 'supports'}
        opposed = {claim['value'] for claim in candidates if claim['stance'] == 'contradicts'}
        if not multiple and len(supportive) > 1:
            conflicts.add(field)
            continue
        usable = [supportive[value] for value in sorted(set(supportive) - opposed)]
        if usable:
            accepted[field] = usable if multiple else usable[0]
    return accepted, conflicts


def apply(row, accepted, conflicts):
    if not accepted and not conflicts:
        return
    row['description_recovery'] = accepted
    row['description_conflicts'] = sorted(conflicts)
    for name in ('capacity', 'numberDoctors'):
        if name in conflicts:
            row['count_fields'][name].update(method='review_unresolved', value=None)
        if name in accepted:
            evidence = accepted[name]
            row['count_fields'][name].update(evidence, method=EXTRACTOR, source='FDR description')
    if found := accepted.get('yearEstablished'):
        row['year_established'] = {**found, 'method': EXTRACTOR, 'source': 'FDR description'}
    if found := accepted.get('service'):
        row['service_evidence'] = [{'service': claim['value'], 'verification_status': 'single_direct_source',
                                    'supporting_urls': [claim['source_url']] if claim['source_url'] else [],
                                    'evidence_scopes': ['branch_service'], 'scope_caveat': None} for claim in found]
