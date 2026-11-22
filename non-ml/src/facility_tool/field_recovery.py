"""Prepare reviewable, branch-scoped evidence and publish approved sidecars."""

from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re

import pandas as pd

from .data import CLAIM_COLUMNS, _array
from .eda import COUNT_UNITS, _audit_input, _facility_target_audit
from .geography import pin_key, place_key
from .recovery_extract import extract_candidates
from .updates import COLUMNS as UPDATE_COLUMNS
from .verification import _claim_evidence, _document, _publisher


VERSION = 'branch-field-recovery-v1'
FDR_PASSAGES = {'description', 'capability', 'procedure', 'equipment'}
PIN_TOKEN = re.compile(r'(?<!\d)[1-9]\d{5}(?!\d)')
COUNT_SCOPES = {
    'capacity': ('whole_facility_beds', 'licensed_beds', 'operating_beds', 'staffed_beds'),
    'numberDoctors': ('whole_facility_doctors', 'on_staff_doctors', 'employed_doctors',
                      'full_time_doctors', 'visiting_doctors', 'permanent_doctors', 'resident_doctors'),
}
EVIDENCE_COLUMNS = (
    'claim_id', 'facility_id', 'source_record_id', 'source', 'evidence_field', 'field',
    'raw_value', 'normalized_value', 'unit', 'scope', 'subtype', 'stance', 'source_url', 'candidate_source_urls',
    'excerpt', 'char_start', 'char_end', 'page', 'passage_index', 'passage_sha256', 'document_id',
    'document_sha256', 'document_date', 'claim_date', 'retrieved_on', 'snapshot_as_of',
    'valid_from', 'valid_through', 'publisher', 'publisher_family', 'host_family',
    'publication_scope', 'branch_anchor', 'linkage_status', 'linkage_basis', 'extraction_method', 'rule',
)
DECISION_COLUMNS = ('claim_id', 'facility_id', 'field', 'normalized_value', 'scope', 'stance',
                    'review_status', 'release_status', 'structured_status', 'reviewer', 'reviewed_on', 'review_reason')
SERVICE_COLUMNS = (*CLAIM_COLUMNS, 'publisher', 'publisher_key', 'reviewed_on', 'recovery_independence_checked')


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _required(row, key):
    value = _text(row.get(key))
    if not value:
        raise ValueError(f'Recovery input requires a nonempty {key}.')
    return value


def _date(value):
    return date.fromisoformat(value) if value else None


def _digest(value):
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(packed.encode('utf-8')).hexdigest()


def _check_frame(frame, required, name, key=None):
    if not frame.columns.is_unique or not set(required).issubset(frame.columns):
        raise ValueError(f'{name} needs unique columns including {sorted(required)}. Rerun its source notebook.')
    if key and (frame[key].duplicated().any() or not frame[key].map(_text).astype(bool).all()):
        raise ValueError(f'{name} needs unique, nonempty {key} values.')


def _published_url(value):
    return bool(_document(value))


def _document_meta(row, by_name, cutoff):
    doc = {key: _required(row, key) for key in (
        'document_id', 'facility_name', 'publisher', 'publisher_family', 'source_url', 'retrieved_on', 'branch_anchor',
    )}
    doc.update({key: _text(row.get(key)) for key in ('document_date', 'valid_from', 'valid_through')})
    doc['publication_scope'] = _text(row.get('publication_scope')) or _text(row.get('record_type')) or 'Branch publication'
    pin, address, city = pin_key(row.get('pin')), place_key(row.get('address_contains')), place_key(row.get('city'))
    if not (pin or address) or not _published_url(doc['source_url']):
        raise ValueError('A recovery document needs a branch PIN or address and a valid public URL.')
    dates = {key: _date(doc[key]) for key in ('retrieved_on', 'document_date', 'valid_from', 'valid_through')}
    if dates['valid_from'] and dates['valid_through'] and dates['valid_from'] > dates['valid_through']:
        raise ValueError('Document validity dates are reversed.')
    passages = row.get('passages')
    if not isinstance(passages, list) or not passages:
        raise ValueError('A recovery document needs a list of saved passages.')
    contents = []
    for passage in passages:
        if not isinstance(passage, dict) or not _text(passage.get('text')):
            raise ValueError('Provide nonempty verbatim passage text.')
        page = passage.get('page')
        if page is not None and (type(page) is not int or page < 1):
            raise ValueError('A passage page must be a positive integer.')
        contents.append((page, passage['text']))
    name = place_key(doc['facility_name'])
    anchor_key = f" {place_key(doc['branch_anchor'])} "
    anchor_valid = (name and f' {name} ' in anchor_key
                    and (not pin or f' {pin} ' in anchor_key)
                    and (not address or f' {address} ' in anchor_key)
                    and any(doc['branch_anchor'] in text for _, text in contents))
    options = [branch for branch in by_name[name]
               if (not pin or pin_key(branch.get('pin')) == pin)
               and (not city or place_key(branch.get('city')) == city)
               and (not address or f' {address} ' in f" {place_key(branch.get('address'))} ")]
    status = 'linked' if len(options) == 1 else 'ambiguous_branch' if options else 'unmatched_branch'
    if not anchor_valid:
        status = 'unverified_branch_anchor'
    elif dates['retrieved_on'] > cutoff or dates['document_date'] and dates['document_date'] > cutoff:
        status = 'source_date_in_future'
    host = _document(doc['source_url'])[0]
    matched_pin = pin_key(options[0].get('pin')) if len(options) == 1 else pin
    doc.update(source='saved_public_document', source_record_id=doc['document_id'], evidence_field='document_text',
               candidate_source_urls=[doc['source_url']], host_family=_publisher(host),
               facility_id=options[0]['facility_id'] if status == 'linked' else '', linkage_status=status,
               linkage_basis='Unique normalized branch name and published PIN or address in saved anchor',
               document_sha256=_digest(contents), matched_pin=matched_pin)
    return doc, contents


def _source_passages(records, links):
    link_index = links.set_index('record_id').to_dict(orient='index')
    for record in records.loc[records['source'].eq('fdr')].to_dict(orient='records'):
        key = record['record_id']
        link = link_index.get(key, {})
        facility_id = link.get('facility_id') if link.get('link_status') in ('anchor', 'linked') else ''
        urls = sorted({url for url in _array(record.get('source_urls')) if _published_url(url)})
        terms = record.get('service_texts')
        contents = [(field, text) for field, text in terms if field in FDR_PASSAGES and _text(text)] if isinstance(terms, (list, tuple)) else []
        document = dict(document_id=key, document_sha256=_digest(contents), source='fdr',
                        source_record_id=_text(record.get('source_record_id')) or key,
                        facility_id=facility_id if _text(facility_id) else '',
                        linkage_status='linked' if _text(facility_id) else 'unlinked_source',
                        linkage_basis=_text(link.get('link_reason')), source_url=urls[0] if len(urls) == 1 else '',
                        candidate_source_urls=urls, publisher='Virtue Foundation FDR', publisher_family='fdr',
                        publication_scope='Original FDR facility listing')
        for index, (field, text) in enumerate(contents):
            yield {**document, 'evidence_field': field}, index, None, text


def _claim_rows(meta, index, page, text, aliases, cutoff):
    for candidate in extract_candidates(text, aliases):
        field = candidate['field']
        if meta['evidence_field'] == 'equipment' and field != 'equipment':
            continue
        start, end = candidate['start'], candidate['end']
        if not 0 <= start < end <= len(text):
            raise ValueError('The extractor returned an invalid evidence span.')
        excerpt = text[start:end]
        identity = (VERSION, meta['document_id'], meta['document_sha256'], meta['facility_id'], meta.get('source_url', ''),
                    meta.get('publisher_family', ''), meta.get('branch_anchor', ''), meta.get('document_date', ''),
                    meta.get('retrieved_on', ''), meta.get('valid_from', ''), meta.get('valid_through', ''),
                    meta.get('publication_scope', ''), index, page, start, end, field, candidate['value'],
                    candidate['unit'], candidate['scope'], candidate.get('subtype', ''), candidate['stance'], candidate['rule'])
        yield {**{key: meta.get(key, '') for key in EVIDENCE_COLUMNS},
               'claim_id': 'recovery:' + _digest(identity)[:32], 'field': field,
               'raw_value': candidate.get('raw_value', excerpt), 'normalized_value': candidate['value'],
               'unit': candidate['unit'], 'scope': candidate['scope'], 'subtype': candidate.get('subtype') or '',
               'stance': candidate['stance'],
               'excerpt': excerpt, 'char_start': start, 'char_end': end, 'page': page, 'passage_index': index,
               'passage_sha256': _digest(text), 'rule': candidate['rule'], 'claim_date': candidate.get('claim_date') or '',
               'snapshot_as_of': cutoff.isoformat(), 'extraction_method': VERSION}


def _reviews(entries):
    indexed = {}
    for row in entries:
        entry = {key: _required(row, key) for key in ('claim_id', 'decision', 'reviewer', 'reviewed_on', 'reason')}
        if entry['claim_id'] in indexed or entry['decision'] not in {'approved', 'rejected'}:
            raise ValueError('Supply one approved or rejected review per recovery claim ID.')
        _date(entry['reviewed_on'])
        entry['independence_checked'] = row.get('independence_checked') is True
        indexed[entry['claim_id']] = entry
    return indexed


def _structured_counts(linked, valid_ids):
    if linked.empty:
        return {}
    _check_frame(linked, ('facility_id',), 'Linked source rows')
    raw_ids = {_text(value) for value in linked['facility_id'] if _text(value)}
    canonical = {_text(value): value for value in valid_ids if _text(value) in raw_ids}
    if len(canonical) != sum(_text(value) in raw_ids for value in valid_ids) or raw_ids - canonical.keys():
        raise ValueError('Linked count rows and prepared branches have mismatched or ambiguous IDs.')
    return {(canonical[row.facility_id], field): (row.status, row.value)
            for field in COUNT_UNITS if field in linked
            for row in _facility_target_audit(_audit_input(linked, field)).itertuples(index=False)}


def _output_field(row):
    field, scope = row['field'], row['scope']
    if field in COUNT_SCOPES and scope != COUNT_SCOPES[field][0]:
        return f'{field}:{scope}'
    if field == 'equipment':
        return f"equipment:{row['normalized_value']}"
    return field


def _release_gate(row, review, eligible, structured, cutoff):
    if row['linkage_status'] != 'linked' or not row['facility_id']:
        return row['linkage_status']
    if not eligible.get(row['facility_id'], False):
        return 'location_held'
    if not review or _date(review['reviewed_on']) > cutoff:
        return 'awaiting_review' if not review else 'review_date_in_future'
    if review['decision'] == 'rejected':
        return 'review_rejected'
    if row['retrieved_on'] and _date(review['reviewed_on']) < _date(row['retrieved_on']):
        return 'review_precedes_capture'
    claim_date, start, end = (_date(row[key]) for key in ('claim_date', 'valid_from', 'valid_through'))
    if (claim_date and claim_date > cutoff) or (start and start > cutoff):
        return 'claim_date_in_future'
    if end and end < cutoff:
        return 'expired_scope'
    if row['field'] in COUNT_SCOPES:
        if row['scope'] not in COUNT_SCOPES[row['field']]:
            return 'ambiguous_count_basis'
        if row['field'] == 'numberDoctors' and not (claim_date or row['document_date']):
            return 'doctor_count_date_missing'
        if _output_field(row) == row['field']:
            status, value = structured
            if status == 'reported':
                return 'already_reported' if row['normalized_value'] == value else 'structured_value_disagrees'
            if status in {'conflicting', 'unparseable'}:
                return 'structured_value_needs_review'
    if row['field'] == 'service' and row['source'] != 'saved_public_document':
        return 'service_needs_saved_public_citation'
    if row['field'] == 'service' and ((start and not end) or claim_date and claim_date < cutoff and not end):
        return 'dated_service_scope_needs_review'
    return 'ready'


def _similar(left, right):
    left, right = place_key(left), place_key(right)
    return left == right or min(len(left), len(right)) >= 40 and SequenceMatcher(None, left, right).ratio() >= 0.92


def _distinct_support(rows):
    chosen = []
    for row in sorted(rows, key=lambda item: (item['source'] == 'fdr', item['claim_id'])):
        if not any(_similar(row['excerpt'], prior['excerpt']) or (place_key(row['publisher_family']) == place_key(prior['publisher_family']))
                   or (row['host_family'] and row['host_family'] == prior['host_family']) for prior in chosen):
            chosen.append(row)
    return chosen


def _sidecar(rows, output_field, method):
    first = rows[0]
    details, references = [], []
    for row in rows:
        details.append(f"{row['excerpt']} [{row['scope']}{', ' + row['subtype'] if row['subtype'] else ''}"
                       f"{', p. ' + str(row['page']) if row['page'] else ''}"
                       f"{', dated ' + (row['claim_date'] or row['document_date']) if row['claim_date'] or row['document_date'] else ''}]")
        url = row['source_url'] if row['source'] == 'saved_public_document' else 'FDR source record ' + row['source_record_id']
        references.append(f"{url} ({row['claim_id']})")
    value = 'present' if first['field'] == 'equipment' else first['normalized_value']
    return dict(facility_id=first['facility_id'], field=output_field, method=method,
                value=None if method == 'conflicting' else value, unit=first['unit'], lower=None, upper=None,
                provenance=' | '.join(references), detail=' | '.join(details))


def _service_claim(row, review):
    return dict(facility_id=row['facility_id'], record_id=row['claim_id'], service=row['normalized_value'],
                stance=row['stance'], excerpt=row['excerpt'], source_url=row['source_url'],
                source_urls=[row['source_url']], source='source_recovery', scope=row['publication_scope'],
                validity='active' if row['valid_from'] and row['valid_through'] else 'unknown',
                validity_text=[], validity_start=row['valid_from'], validity_end=row['valid_through'],
                evidence_field='reviewed_recovery_text', citation_quality='direct', match_reason=row['linkage_basis'],
                publisher=row['publisher'], publisher_key=row['publisher_family'], reviewed_on=review['reviewed_on'],
                recovery_independence_checked=review['independence_checked'])


def prepare_field_recovery(records, links, entities, linked_fdr, aliases, *, as_of, documents=(), reviews=()):
    """Build immutable evidence and reviewed outputs from one prepared snapshot."""
    cutoff = date.fromisoformat(as_of)
    _check_frame(records, ('record_id', 'source', 'service_texts'), 'Facility records', 'record_id')
    _check_frame(links, ('record_id', 'facility_id', 'link_status'), 'Branch links', 'record_id')
    _check_frame(entities, ('facility_id', 'name', 'location_routing_eligible'), 'Prepared branches', 'facility_id')
    by_name, eligible = defaultdict(list), {}
    for entity in entities.to_dict(orient='records'):
        by_name[place_key(entity['name'])].append(entity)
        eligible[entity['facility_id']] = str(entity['location_routing_eligible']).casefold() == 'true'
    reviewed = _reviews(reviews)
    retained = _structured_counts(linked_fdr, eligible)
    passages = _source_passages(records, links)
    evidence = {row['claim_id']: row for meta, index, page, text in passages
                for row in _claim_rows(meta, index, page, text, aliases, cutoff)}
    document_ids = set()
    for entry in documents:
        meta, contents = _document_meta(entry, by_name, cutoff)
        if meta['document_id'] in document_ids:
            raise ValueError('Use one unique document_id for each saved source document.')
        document_ids.add(meta['document_id'])
        for index, (page, text) in enumerate(contents):
            passage = meta
            if meta['linkage_status'] == 'linked':
                other_pins = set(PIN_TOKEN.findall(text)) - {meta['matched_pin']}
                if meta['branch_anchor'] not in text or other_pins:
                    passage = {**meta, 'facility_id': '', 'linkage_status': 'passage_branch_unresolved'}
            evidence.update((row['claim_id'], row) for row in _claim_rows(passage, index, page, text, aliases, cutoff))

    decisions, candidates = {}, defaultdict(list)
    for claim_id, row in evidence.items():
        review = reviewed.get(claim_id, {})
        review_status = 'extracted_unreviewed'
        if review and _date(review['reviewed_on']) <= cutoff:
            review_status = 'reviewed_source_claim' if review['decision'] == 'approved' else 'rejected'
        structured = retained.get((row['facility_id'], row['field']), ('no_structured_source', None))
        release = _release_gate(row, review, eligible, structured, cutoff)
        decisions[claim_id] = {**{key: row[key] for key in ('claim_id', 'facility_id', 'field', 'normalized_value', 'scope', 'stance')},
                               'review_status': review_status, 'release_status': release, 'structured_status': structured[0],
                               'reviewer': review.get('reviewer', ''), 'reviewed_on': review.get('reviewed_on', ''),
                               'review_reason': review.get('reason', '')}
        if release == 'ready':
            field = _output_field(row)
            candidates[row['facility_id'], field, row['normalized_value'] if row['field'] == 'service' else ''].append(row)
    for claim_id in reviewed.keys() - evidence.keys():
        review = reviewed[claim_id]
        decisions[claim_id] = dict(claim_id=claim_id, review_status='unmatched_review', release_status='unmatched_review',
                                   reviewer=review['reviewer'], reviewed_on=review['reviewed_on'], review_reason=review['reason'])

    updates, services = [], []
    for (_, field, _), rows in sorted(candidates.items()):
        positive = _distinct_support([row for row in rows if row['stance'] == 'supports'])
        negative = _distinct_support([row for row in rows if row['stance'] == 'contradicts'])
        conflict = bool(positive and negative or len({row['normalized_value'] for row in rows}) > 1)
        if conflict and field != 'service':
            # Keep conflicting values even when their wording or publisher matches.
            updates.append(_sidecar(rows, field, 'conflicting'))
            for row in rows:
                decisions[row['claim_id']].update(release_status='conflicting', review_status='conflicting')
            continue
        publishable = positive + negative if field == 'service' else positive
        independent = [row for row in positive if row['source'] == 'saved_public_document'
                       and reviewed[row['claim_id']]['independence_checked']]
        corroborated = len(independent) > 1 and not conflict
        if field != 'service' and positive:
            updates.append(_sidecar(positive, field, 'source_recovered'))
        for row in rows:
            decision = decisions[row['claim_id']]
            if row not in publishable:
                decision['release_status'] = 'contradiction_audited' if row['stance'] == 'contradicts' else 'duplicate_evidence'
                continue
            if field == 'service':
                services.append(_service_claim(row, reviewed[row['claim_id']]))
            decision['release_status'] = 'released'
            if conflict:
                decision['review_status'] = 'conflicting'
            elif corroborated and row in independent:
                decision['review_status'] = 'independently_corroborated'

    audit = pd.DataFrame([decisions[key] for key in sorted(decisions)], columns=DECISION_COLUMNS)
    summary = audit.groupby(['field', 'review_status', 'release_status'], dropna=False).size().reset_index(name='claims')
    return dict(recovery_claims=pd.DataFrame([evidence[key] for key in sorted(evidence)], columns=EVIDENCE_COLUMNS),
                recovery_decisions=audit, recovered_field_updates=pd.DataFrame(updates, columns=UPDATE_COLUMNS),
                recovered_service_claims=pd.DataFrame(services, columns=SERVICE_COLUMNS), recovery_summary=summary)


def merge_recovered_service_claims(base, recovered, as_of):
    """Extend the service gate without granting extra credit to repeated source text."""
    if recovered.empty:
        return base
    cutoff = date.fromisoformat(as_of)
    groups = defaultdict(list)
    for row in base.to_dict(orient='records'):
        if _claim_evidence(row, cutoff)['role'] in {'supporting', 'contradicting'}:
            groups[row['facility_id'], row['service'], row['stance']].append(row)
    additions = []
    for item in recovered.to_dict(orient='records'):
        key = item['facility_id'], item['service'], item['stance']
        candidate = _claim_evidence(item, cutoff)
        if candidate['role'] not in {'supporting', 'contradicting'}:
            continue
        if key[2] == 'supports' and groups[key] and (
            not item['recovery_independence_checked']
            or any(row['source'] == 'source_recovery' and row.get('recovery_independence_checked') is not True
                   for row in groups[key])
        ):
            continue
        if any(_similar(item['excerpt'], prior['excerpt']) or _document(item['source_url']) == _document(prior['source_url'])
               or (place_key(prior.get('publisher_key')) and place_key(item['publisher_key']) == place_key(prior['publisher_key']))
               or candidate['publisher_family'] == _claim_evidence(prior, cutoff)['publisher_family'] for prior in groups[key]):
            continue
        groups[key].append(item)
        additions.append(item)
    return pd.concat([base, pd.DataFrame(additions, columns=recovered.columns)], ignore_index=True) if additions else base
