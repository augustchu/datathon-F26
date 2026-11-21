"""Adapt user inputs, evidence and optional travel estimates to the shared selector."""

from datetime import datetime, timezone
import re

import pandas as pd

from .geography import place_key
from .origin_index import resolve_prepared_origin
from .patient_experience import PATIENT_EXPERIENCE_COLUMNS
from .query_assets import LOOKUP_QUALITY_FIELDS, LOOKUP_VERIFICATION_FIELDS
from .recommend import compare_prepared_modes
from .response import CONTACT_FIELDS, format_recommendations
from .travel import MODES as TRANSPORT_MODES, estimate_travel


def resolve_service(concern, aliases):
    """Recognize documented service terms. Leave symptom interpretation to the user."""
    text = place_key(concern)
    if not text:
        raise ValueError('Enter the service you need, such as dialysis or MRI.')
    matches = []
    for service, names in aliases.items():
        for name in {place_key(service), *(place_key(alias) for alias in names)} - {''}:
            for match in re.finditer(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text):
                matches.append((service, *match.span()))
    # “NICU Level 3” contains “NICU”. Keep the more specific phrase; concurrent services stay distinct.
    choices = sorted({service for service, start, end in matches if not any(
        left <= start and end <= right and (left, right) != (start, end)
        for _, left, right in matches
    )})
    if len(choices) == 1:
        return choices[0]
    if choices:
        raise ValueError(f'Choose one service: {", ".join(choices)}.')
    raise ValueError('Choose a listed service. The lookup uses service wording, so a symptom alone cannot resolve it.')


def lookup(store, config, *, concern, location, spread_demand=False, traveltime_enabled=None,
           travel_mode=None, route_sort=None, facility_sort=None, travel_weight=None, max_extra_km=None,
           max_extra_minutes=None, pressure_inputs=None, feedback=()):
    """Return all three modes from one snapshot and one optional routing request."""
    service = resolve_service(concern, config['service_aliases'])
    candidates = store.read(f'lookup_candidates_{service}')
    origin = resolve_prepared_origin(location, store.read('origin_index'))
    excluded = {entry.facility_id for entry in feedback if entry.service == service and not entry.confirmed}
    if excluded:
        candidates = candidates.loc[~candidates['facility_id'].isin(excluded)]
    limits = config['lookup']
    facility_sort = limits.get('facility_sort', 'balanced') if facility_sort is None else facility_sort
    if facility_sort not in {'balanced', 'closest'}:
        raise ValueError('Choose balanced or closest for facility sorting.')
    quality_policy = config.get('quality_ranking') if facility_sort == 'balanced' else None
    routing = config['traveltime']
    travel_mode = routing['mode'] if travel_mode is None else travel_mode
    if travel_mode not in TRANSPORT_MODES:
        raise ValueError(f"Choose a transport mode from {', '.join(TRANSPORT_MODES)}.")
    if route_sort is None:
        route_sort = 'time' if travel_mode == 'public_transport' else 'distance'
    if route_sort not in {'distance', 'time'}:
        raise ValueError('Choose distance or time for route sorting.')
    if travel_mode == 'public_transport' and route_sort == 'distance':
        raise ValueError('Choose time for public transport. TravelTime does not return transit route distance in this matrix.')
    use_travel = routing['enabled'] if traveltime_enabled is None else traveltime_enabled
    demo = store.path.parent.name == 'demo'
    measured = store.read(f'lookup_measured_{service}', optional=True) if spread_demand else None
    if spread_demand and pressure_inputs is None:
        pressure_inputs = store.read(f'lookup_pressure_{service}', optional=True)
    parameters = dict(top_k=limits['top_k'], max_radius_km=limits['max_radius_km'],
                      max_load_age_hours=limits['max_load_age_hours'], as_of=config['as_of'] if demo else datetime.now(timezone.utc),
                      travel_weight=limits['travel_weight'] if travel_weight is None else travel_weight,
                      max_extra_km=limits['max_extra_km'] if max_extra_km is None else max_extra_km,
                      max_extra_minutes=limits['max_extra_minutes'] if max_extra_minutes is None else max_extra_minutes)
    # Synthetic coordinates and IDs never go to the routing provider.
    routing_results = pd.DataFrame()
    if use_travel and not demo:
        supported = candidates['evidence_hold_reason'].eq('') | candidates['model_leads'].map(bool)
        routeable = candidates.loc[supported & candidates['location_routing_eligible'].eq(True)]
        sites = routeable[['facility_id', 'site_lat', 'site_lon', 'location_routing_eligible']].rename(
            columns={'site_lat': 'lat', 'site_lon': 'lon'},
        )
        routing_results = estimate_travel(
            (origin.lat, origin.lon), sites, enabled=True, mode=travel_mode,
            departure_time=datetime.now(timezone.utc), max_travel_minutes=routing['max_travel_minutes'],
            max_destinations=routing['max_destinations'],
        )
    result = compare_prepared_modes(candidates, origin.lat, origin.lon, service, **parameters,
                                    route_sort=route_sort, spread_demand=spread_demand, pressure_inputs=pressure_inputs,
                                    measured_load=measured, travel_times=routing_results, quality_policy=quality_policy)
    result['route_status'] = routing_results
    result['transport_mode'] = travel_mode
    result['route_sort'] = route_sort
    result['facility_sort'] = facility_sort
    result['origin'] = origin
    result['service'] = service
    result['excluded_by_caller'] = sorted(excluded)
    fields = (*LOOKUP_VERIFICATION_FIELDS, *LOOKUP_QUALITY_FIELDS,
              *(key for key in PATIENT_EXPERIENCE_COLUMNS if key != 'facility_id'))
    for table in ('rows', 'trace'):
        display_columns = [key for key in fields if key not in result[table].columns]
        checks = candidates.reindex(columns=['facility_id', *display_columns])
        result[table] = result[table].merge(checks, how='left', on='facility_id', validate='m:1')
        result[table]['verification_status'] = result[table]['verification_status'].fillna('insufficient')
        result[table]['quality_assessment_status'] = result[table]['quality_assessment_status'].fillna('unknown')
    contact_choices = candidates[['facility_id', *CONTACT_FIELDS]]
    result['rows'] = format_recommendations(result['rows'], contact_choices, origin, limits['max_radius_km'], feedback)
    return result


def side_by_side(rows):
    """Compare ranks and field values for the same facility across the three views."""
    columns = ['facility_id', 'name', 'mode', 'rank', 'capacity', 'capacity_method',
               'numberDoctors', 'numberDoctors_method']
    eligible = rows.loc[rows['kind'].eq('recommendation'), columns]
    if eligible.empty:
        return pd.DataFrame(columns=columns)
    comparison = eligible.set_index(['facility_id', 'name', 'mode']).unstack('mode')
    comparison.columns = [f'{metric} · {mode}' for metric, mode in comparison.columns]
    return comparison.reset_index()
