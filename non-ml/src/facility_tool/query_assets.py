"""Materialize the data used by lookup from the independent review notebooks."""

import pandas as pd

from .origin_index import build_origin_index
from .patient_experience import PATIENT_EXPERIENCE_COLUMNS
from .recommend import QUALITY_EVIDENCE_COLUMNS, prepare_service_candidates
from .response import prepare_contacts


LOOKUP_VERIFICATION_FIELDS = (
    'verification_status', 'verification_as_of', 'supporting_publisher_count',
    'supporting_publishers', 'supporting_urls', 'contradicting_urls', 'scope_caveat', 'verification_evidence',
)
LOOKUP_QUALITY_FIELDS = (
    'quality_as_of', 'quality_assessment_status', 'quality_assessment_programmes', 'quality_evidence',
)


def _lookup_ids(frame):
    # Candidate preparation trims legacy IDs. Preserve source IDs in the upstream tables.
    return frame.assign(facility_id=frame['facility_id'].str.strip())


def prepare_lookup_assets(store, config):
    """Build one prepared candidate checkpoint per service and a place index."""
    entities = store.read('entities')
    claims = store.read('verified_claims')
    verification = _lookup_ids(store.read('service_verification').rename(columns={'evidence': 'verification_evidence'}))
    quality = _lookup_ids(store.read('quality_profiles'))
    patient_experience = _lookup_ids(store.read('patient_experience'))
    updates = store.read('field_updates', optional=True)
    contacts = _lookup_ids(prepare_contacts(store.read('contact_evidence', optional=True)))
    measured = store.read('measured_load', optional=True)
    pressure = store.read('pressure_scenarios', optional=True)
    origins = build_origin_index(entities, store.read('postal_areas'))
    store.write('origin_index', origins)
    summary = []
    for service in config['service_aliases']:
        prepared = prepare_service_candidates(entities, claims, service, updates=updates, as_of=config['as_of'])
        prepared = prepared.merge(contacts, how='left', on='facility_id', validate='1:1')
        checks = verification.loc[verification['service'].eq(service), ['facility_id', *LOOKUP_VERIFICATION_FIELDS]]
        prepared = prepared.merge(checks, how='left', on='facility_id', validate='1:1')
        fields = ['facility_id', *QUALITY_EVIDENCE_COLUMNS, *LOOKUP_QUALITY_FIELDS]
        assessments = quality.loc[quality['service'].eq(service), fields]
        prepared = prepared.drop(columns=list(QUALITY_EVIDENCE_COLUMNS)).merge(
            assessments, how='left', on='facility_id', validate='1:1',
        )
        prepared = prepared.drop(
            columns=[field for field in PATIENT_EXPERIENCE_COLUMNS if field != 'facility_id'], errors='ignore',
        ).merge(patient_experience, how='left', on='facility_id', validate='1:1')
        store.write(f'lookup_candidates_{service}', prepared)
        for label, frame in [('pressure', pressure), ('measured', measured)]:
            scoped = frame.loc[frame['service'].eq(service)] if not frame.empty else frame
            store.write(f'lookup_{label}_{service}', scoped)
        summary.append({
            'service': service, 'candidates': len(prepared),
            'routing_eligible': int(prepared['location_routing_eligible'].eq(True).sum()),
            'direct_and_routable': int((prepared['location_routing_eligible'].eq(True) &
                                       prepared['evidence_hold_reason'].eq('')).sum()),
            'corroborated': int(prepared['verification_status'].eq('corroborated').sum()),
            'currently_assessed': int(prepared['quality_assessed'].eq(True).sum()),
        })
    profile = pd.DataFrame(summary)
    store.write('lookup_asset_profile', profile)
    return profile
