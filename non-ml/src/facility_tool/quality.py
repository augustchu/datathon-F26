"""Build branch-level assessment evidence without estimating clinical quality."""

from collections import defaultdict
import re

import pandas as pd

from .verification import _date, _document, _text


ASSESSMENT_COLUMNS = (
    'record_id', 'facility_id', 'service', 'programme', 'assessment_status', 'assessment_basis',
    'eligible_for_quality', 'link_status', 'link_reason', 'source', 'source_url', 'excerpt', 'scope',
    'credential_number', 'validity_start', 'validity_end', 'validity_text', 'reviewed_on', 'retrieved_at',
    'assessment_as_of',
)
QUALITY_COLUMNS = (
    'facility_id', 'service', 'quality_as_of', 'quality_corroborated', 'quality_assessed',
    'quality_assessment_status', 'quality_assessment_programmes', 'quality_evidence',
)
ATTACHED = {'anchor', 'linked', 'source_only'}
NEGATIVE_ASSESSMENT = re.compile(r'\b(?:withdraw(?:al|n)|revo(?:ked|cation)|suspend(?:ed)?|suspension|cancel(?:led|ed|lation)|terminated|inactive|de[- ]accredited|not certified|not accredited)\b')
CREDENTIAL_NUMBER = re.compile(r'\b[A-Z]{1,10}-[0-9]{4}-[0-9]+(?:-[0-9]+)*\b')


def _iso(value):
    return value.isoformat() if value else ''


def _basis_date(value):
    return _date(_text(value)[:10]) or _date(value)


def _source_excerpt(value):
    return value if isinstance(value, str) else ''


def _assessment(record_id, facility_id, service, programme, source, url, excerpt, scope, cutoff,
                *, link_status, link_reason, status, basis, credential='', start=None, end=None,
                validity_text=(), reviewed_on='', retrieved_at='', eligible=False):
    return dict(
        record_id=record_id, facility_id=facility_id, service=service, programme=programme,
        assessment_status=status, assessment_basis=basis, eligible_for_quality=eligible,
        link_status=link_status, link_reason=link_reason, source=source, source_url=url, excerpt=excerpt,
        scope=scope, credential_number=credential, validity_start=_iso(start), validity_end=_iso(end),
        validity_text=list(validity_text), reviewed_on=reviewed_on, retrieved_at=retrieved_at,
        assessment_as_of=cutoff.isoformat(),
    )


def _record_assessment(record, link, cutoff):
    source = record['source']
    raw = record.get('raw')
    raw = raw if isinstance(raw, dict) else record
    link_status = _text(link.get('link_status'))
    facility = _text(link.get('facility_id')) if link_status in ATTACHED else ''
    urls = record.get('source_urls')
    url = _text(raw.get('source_url')) or (_text(urls[0]) if isinstance(urls, (list, tuple)) and len(urls) == 1 else '')
    excerpt = _source_excerpt(raw.get('certification_status', raw.get('status_text')))
    name = excerpt.strip().casefold()
    retrieved = _text(raw.get('retrieved_at_utc')) or _text(raw.get('scraped_at_utc'))
    issue = _date(raw.get('validity_start'))
    end = _date(raw.get('validity_date_iso')) or _date(raw.get('validity_end'))
    validity_text = _text(raw.get('validity_date_text')) or _text(raw.get('validity_date_raw'))
    official = _document(url)
    host = official[0] if official else ''
    trusted_host = 'nhsrcindia.org' if source == 'nqas' else 'nabh.co'
    trusted = host == trusted_host or host.endswith('.' + trusted_host)
    as_retrieved = _basis_date(retrieved)
    if (as_retrieved and as_retrieved > cutoff) or (issue and issue > cutoff):
        status, basis = 'future', 'The record or its stated validity starts after the assessment date.'
    elif not trusted or not name:
        status, basis = 'incomplete', 'A published assessment status and its organization URL are required.'
    elif issue and end and end < issue:
        status, basis = 'incomplete', 'The credential lists a validity end before its start.'
    elif (end and end < cutoff) or name == 'expired':
        status, basis = 'expired', 'The published assessment validity has ended.'
    elif NEGATIVE_ASSESSMENT.search(name):
        status, basis = 'negative', 'The published assessment status is explicitly negative.'
    elif source == 'nabh':
        status, basis = 'directory_status_only', 'Directory status without a reviewed, dated service scope.'
    elif name in {'conditional', 'conditionality', 'certified with conditionality'}:
        status, basis = 'conditional', 'The national directory lists certification with conditions.'
    elif name == 'certified' and end:
        status, basis = 'nqas_certified', 'The NHSRC directory lists full certification with an unexpired date.'
    else:
        status, basis = 'incomplete', 'Full certification with a usable validity date is required.'
    exact = facility and link_status in {'anchor', 'linked'}
    if status == 'nqas_certified' and not exact:
        basis += ' The saved link does not establish the branch association required for ranking.'
    return _assessment(
        record['record_id'], facility, '', source, source, url, excerpt,
        'Facility-level public health standards' if source == 'nqas' else 'NABH directory status', cutoff,
        link_status=link_status, link_reason=_text(link.get('link_reason')), status=status, basis=basis,
        credential=_text(raw.get('accreditation_number')), start=issue, end=end,
        validity_text=[validity_text] if validity_text else [], retrieved_at=retrieved,
        eligible=bool(exact and status == 'nqas_certified'),
    )


def _reviewed_assessment(claim, branch_ids, cutoff):
    if _text(claim.get('source')) != 'reviewed' or _text(claim.get('publisher_key')).casefold() != 'nabh':
        return None
    facility, service = _text(claim.get('facility_id')), _text(claim.get('service'))
    scope, url = _text(claim.get('scope')), _text(claim.get('source_url'))
    publication = _document(url)
    host, path, _ = publication if publication else ('', '', '')
    source_is_certificate = (host == 'nabh.co' or host.endswith('.nabh.co')) and path.casefold().endswith('.pdf')
    excerpt = _source_excerpt(claim.get('excerpt'))
    start, end = _date(claim.get('validity_start')), _date(claim.get('validity_end'))
    reviewed = _date(claim.get('reviewed_on'))
    link_status = 'reviewed_branch_match' if facility in branch_ids else 'unlinked'
    dated_scope = 'scope' in scope.casefold() and 'accredit' in scope.casefold()
    safe_document = (source_is_certificate and dated_scope and excerpt.strip()
                     and _text(claim.get('citation_quality')) == 'direct'
                     and _text(claim.get('evidence_field')) == 'reviewed_service_text')
    if not safe_document or not (start and end and start <= end and reviewed):
        status, basis = 'incomplete', 'A direct reviewed scope document and a complete validity period are required.'
    elif reviewed > cutoff or start > cutoff:
        status, basis = 'future', 'The document review or its validity period starts after the assessment date.'
    elif end < cutoff:
        status, basis = 'expired', 'The reviewed accreditation validity has ended.'
    elif _text(claim.get('stance')) == 'contradicts' or _text(claim.get('validity')) == 'inactive':
        status, basis = 'negative', 'The reviewed certificate records a negative scope or validity claim.'
    elif _text(claim.get('stance')) == 'supports':
        status, basis = 'nabh_service_scope', 'A current reviewed NABH certificate lists this service in scope at this branch.'
    else:
        status, basis = 'incomplete', 'The reviewed text does not establish this service in the accredited scope.'
    credential = CREDENTIAL_NUMBER.search(scope)
    notes = claim.get('validity_text')
    notes = [note for note in notes if isinstance(note, str)] if isinstance(notes, (list, tuple)) else []
    return _assessment(
        _text(claim.get('record_id')), facility if link_status == 'reviewed_branch_match' else '', service,
        'nabh', 'reviewed', url, excerpt, scope, cutoff, link_status=link_status,
        link_reason=_text(claim.get('match_reason')), status=status, basis=basis,
        credential=credential.group() if credential else '', start=start, end=end, validity_text=notes,
        reviewed_on=_iso(reviewed), eligible=bool(service and link_status == 'reviewed_branch_match' and status == 'nabh_service_scope'),
    )


def _quality_profile(row, evidence, cutoff):
    valid = defaultdict(set)
    for item in evidence:
        if item['eligible_for_quality']:
            valid[item['programme']].add((
                item['credential_number'] or (item['source_url'] if item['programme'] == 'nabh' else ''),
                item['validity_start'], item['validity_end'],
            ))
    programme_status = {
        ('nqas',): 'nqas_certified', ('nabh',): 'nabh_service_scope',
        ('nabh', 'nqas'): 'nqas_certified_and_nabh_service_scope',
    }
    conflicting = (
        any(item['assessment_status'] == 'negative' for item in evidence)
        or any(len(versions) > 1 for versions in valid.values())
    )
    programmes = sorted(valid) if not conflicting else []
    status = 'conflicting_assessment_evidence' if conflicting else programme_status.get(
        tuple(programmes), 'assessment_status_only' if evidence else 'unknown',
    )
    return dict(
        facility_id=row['facility_id'], service=row['service'], quality_as_of=cutoff.isoformat(),
        quality_corroborated=_text(row['verification_status']) == 'corroborated', quality_assessed=bool(programmes),
        quality_assessment_status=status, quality_assessment_programmes=programmes, quality_evidence=evidence,
    )


def build_quality_profiles(records, links, verified_claims, service_verification, as_of):
    """Return credential evidence and one profile per verified facility-service pair.

    NQAS is a facility-level assurance signal on an exact existing branch link.
    A reviewed NABH scope document can document assessment for its listed service.
    An assessment says what was certified under that program and date. Published
    clinical outcomes, service availability and current intake are separate inputs.
    """
    cutoff = _date(as_of)
    if cutoff is None:
        raise ValueError('Quality evidence requires an ISO as-of date.')
    if not links.empty and links['record_id'].duplicated().any():
        raise ValueError('Quality evidence requires one canonical link per source record.')
    required = {'facility_id', 'service', 'verification_status'}
    if not service_verification.empty and not required.issubset(service_verification.columns):
        raise ValueError('Quality profiles require facility, service and verification status.')
    if not service_verification.empty and service_verification.duplicated(['facility_id', 'service']).any():
        raise ValueError('Quality profiles require one verification row per facility and service.')
    if 'verification_as_of' in service_verification and not service_verification['verification_as_of'].eq(cutoff.isoformat()).all():
        raise ValueError('The service verification snapshot must use the same assessment date.')

    by_record = {link['record_id']: link for link in links.to_dict(orient='records')}
    branch_ids = {_text(link.get('facility_id')) for link in by_record.values() if link.get('link_status') in ATTACHED} - {''}
    assessment_rows = [
        _record_assessment(record, by_record.get(record['record_id'], {}), cutoff)
        for record in records.to_dict(orient='records') if record['source'] in {'nqas', 'nabh'}
    ]
    assessment_rows.extend(
        assessment for row in verified_claims.to_dict(orient='records')
        if (assessment := _reviewed_assessment(row, branch_ids, cutoff)) is not None
    )

    by_scope = defaultdict(list)
    for item in assessment_rows:
        if item['facility_id']:
            by_scope[item['facility_id'], item['service']].append(item)
    profiles = []
    for row in service_verification.to_dict(orient='records'):
        evidence = by_scope[row['facility_id'], ''] + by_scope[row['facility_id'], row['service']]
        profiles.append(_quality_profile(row, evidence, cutoff))
    return pd.DataFrame(assessment_rows, columns=ASSESSMENT_COLUMNS), pd.DataFrame(profiles, columns=QUALITY_COLUMNS)
