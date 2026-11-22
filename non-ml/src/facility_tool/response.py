"""Contact presentation, fit explanations, and caller feedback for one search."""

from dataclasses import dataclass

import pandas as pd

from .profiles import plausible_phone
from .recommend import DISTANCE_METRIC, ROUTE_DISTANCE_METRIC, ROUTE_METRIC


LOAD_OPT_IN_LABEL = 'Not an emergency? Opt in to find less crowded facilities, which may be further away.'
CALL_PROMPT = 'Call this facility before traveling to verify the service, branch, and current availability.'
CONTACT_FIELDS = ('phone_number', 'phone_source_url', 'phone_source_note', 'phone_source_urls', 'other_listed_phones')
FIT_TRAVEL_SCALES = {
    ROUTE_METRIC: ('travel_minutes', 60, 'route time'),
    ROUTE_DISTANCE_METRIC: ('route_distance_km', 50, 'route distance'),
    DISTANCE_METRIC: ('distance_km', 50, 'straight-line distance'),
}


@dataclass(frozen=True)
class PhoneFeedback:
    service: str
    facility_id: str
    confirmed: bool


def record_call(feedback, service, facility_id, *, confirmed):
    """Return replacement state for this search."""
    if not isinstance(confirmed, bool) or not service or not facility_id:
        raise ValueError('Provide a service, a facility ID, and a yes/no confirmation.')
    return tuple(entry for entry in feedback if (entry.service, entry.facility_id) != (service, facility_id)) + (
        PhoneFeedback(service, facility_id, confirmed),
    )


def report_unconfirmed_call(feedback, service, facility_id, *, moderation_stack,
                            reason='service_not_confirmed', submission_id=None):
    """Send a negative response to moderation after computing the alternatives."""
    if not any(entry.service == service and entry.facility_id == facility_id and not entry.confirmed
               for entry in feedback):
        raise ValueError('Record a negative response for this facility and service before submitting a report.')
    return moderation_stack.submit_report(facility_id, service, reason=reason, submission_id=submission_id)


def prepare_contacts(contacts):
    """Choose citable published contacts once for each facility snapshot."""
    phones = {}
    if not contacts.empty:
        for phone in contacts.loc[contacts['field'].eq('phone_numbers')].itertuples(index=False):
            value = str(phone.value)
            if not phone.source_url or not plausible_phone(value):
                continue
            phones.setdefault(phone.facility_id, []).append(phone)
    details = []
    for facility_id, candidates in phones.items():
        # Prefer a single source URL and retain competing listed numbers.
        candidates = sorted(candidates, key=lambda p: (len(p.source_urls) != 1, p.source, str(p.value), p.record_id))
        first = candidates[0]
        details.append({
            'facility_id': facility_id, 'phone_number': str(first.value), 'phone_source_url': first.source_url,
            'phone_source_note': first.url_note, 'phone_source_urls': list(first.source_urls),
            'other_listed_phones': list(dict.fromkeys(str(p.value) for p in candidates))[1:],
        })
    return pd.DataFrame(details, columns=['facility_id', *CONTACT_FIELDS])


def format_recommendations(rows, contacts, origin, feedback=()):
    """Combine saved contact choices with request-specific fit and call feedback."""
    result = rows.merge(contacts, how='left', on='facility_id', validate='m:1')
    calls = {(entry.service, entry.facility_id): entry.confirmed for entry in feedback}
    details = []
    for row in result.itertuples(index=False):
        fit, basis = _fit_score(row, origin)
        called = calls.get((row.service, row.facility_id))
        details.append({
            'call_to_verify': CALL_PROMPT if pd.notna(row.phone_number) else 'Find an official contact and call to verify the service and current availability before traveling.',
            'fit_score': fit, 'fit_score_basis': basis,
            'call_response': 'confirmed_by_caller' if called else 'not_confirmed_by_caller' if called is False else 'not_checked',
        })
    fields = ('call_to_verify', 'fit_score', 'fit_score_basis', 'call_response')
    return result.join(pd.DataFrame(details, index=result.index, columns=fields))


def _fit_score(row, origin):
    if row.kind != 'recommendation':
        return None, 'No fit score for an unverified lead.'
    service_points = 45  # The ranker already required direct and citable evidence.
    corroboration = 10 if row.verification_status == 'corroborated' else 0
    validity_points = 15 if row.validity == 'active' else 5
    origin_points = 20 if origin.basis == 'User-supplied coordinates' else 10
    field, half_credit, label = FIT_TRAVEL_SCALES[row.ranking_travel_metric]
    proximity_points = 10 / (1 + getattr(row, field) / half_credit)
    score = round(service_points + corroboration + validity_points + origin_points + proximity_points)
    return score, (f'{service_points} direct service evidence + {corroboration} independent corroboration '
                   f'+ {validity_points} validity + {origin_points} origin precision '
                   f'+ {proximity_points:.1f} proximity based on {label}. Heuristic fit score out of 100.')


def new_alternatives(before, after):
    """Return newly shown recommendations per mode after the caller rejects a facility."""
    previously_shown = set(zip(before.loc[before['kind'].eq('recommendation'), 'mode'],
                               before.loc[before['kind'].eq('recommendation'), 'facility_id']))
    possible = after.loc[after['kind'].eq('recommendation')]
    mask = [(row.mode, row.facility_id) not in previously_shown for row in possible.itertuples(index=False)]
    return possible.loc[mask].reset_index(drop=True)
