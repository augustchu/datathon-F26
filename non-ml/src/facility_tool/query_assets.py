"""Materialize the data used by lookup from the independent review notebooks."""

import pandas as pd

from .origin_index import build_origin_index
from .recommend import prepare_service_candidates
from .response import prepare_contacts


LOOKUP_VERIFICATION_FIELDS = (
    'verification_status', 'verification_as_of', 'supporting_publisher_count',
    'supporting_publishers', 'supporting_urls', 'contradicting_urls', 'scope_caveat', 'verification_evidence',
)


def prepare_lookup_assets(store, config):
    """Build one prepared candidate checkpoint per service and a place index."""
    entities = store.read('entities')
    claims = store.read('verified_claims')
    verification = store.read('service_verification').rename(columns={'evidence': 'verification_evidence'})
    updates = store.read('field_updates', optional=True)
    contacts = prepare_contacts(store.read('contact_evidence', optional=True))
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
        })
    profile = pd.DataFrame(summary)
    store.write('lookup_asset_profile', profile)
    return profile
