"""Adapt user inputs, evidence and optional travel estimates to the shared selector."""

from datetime import datetime, timezone
import re

import pandas as pd

from .geography import place_key, resolve_origin
from .recommend import compare_modes
from .response import format_recommendations
from .travel import estimate_travel


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


def lookup(store, config, *, concern, location, spread_demand=False, traveltime_enabled=False,
           travel_weight=None, max_extra_km=None, max_extra_minutes=None, pressure_inputs=None, feedback=()):
    """Return all three modes from one snapshot and one optional routing request."""
    entities = store.read('entities')
    claims = store.read('claims')
    updates = store.read('field_updates', optional=True)
    measured = store.read('measured_load', optional=True)
    contacts = store.read('contact_evidence', optional=True)
    geography = store.read('location_entity_review', optional=True)
    if pressure_inputs is None:
        pressure_inputs = store.read('pressure_scenarios', optional=True)
    service = resolve_service(concern, config['service_aliases'])
    origin = resolve_origin(location, entities, store.read('postal_areas'))
    excluded = {entry.facility_id for entry in feedback if entry.service == service and not entry.confirmed}
    if excluded:
        entities = entities.loc[~entities['facility_id'].isin(excluded)]
        claims = claims.loc[~claims['facility_id'].isin(excluded)]
        if not updates.empty:
            updates = updates.loc[~updates['facility_id'].isin(excluded)]
    limits = config['lookup']
    parameters = dict(updates=updates, top_k=limits['top_k'], max_radius_km=limits['max_radius_km'],
                      max_load_age_hours=limits['max_load_age_hours'], as_of=config['as_of'],
                      travel_weight=limits['travel_weight'] if travel_weight is None else travel_weight,
                      max_extra_km=limits['max_extra_km'] if max_extra_km is None else max_extra_km,
                      max_extra_minutes=limits['max_extra_minutes'] if max_extra_minutes is None else max_extra_minutes)
    baseline = compare_modes(entities, claims, origin.lat, origin.lon, service, **parameters)
    candidates = baseline['trace']
    route_ids = set(candidates.loc[candidates['kind'].isin(['recommendation', 'lead']), 'facility_id'])
    sites = entities.loc[entities['facility_id'].isin(route_ids)] if route_ids else entities.iloc[:0]
    routing = config['traveltime']
    # Synthetic coordinates and IDs never go to the routing provider.
    use_travel = bool(traveltime_enabled) and store.path.parent.name != 'demo'
    routing_results = estimate_travel((origin.lat, origin.lon), sites, enabled=use_travel, mode=routing['mode'],
                                      departure_time=datetime.now(timezone.utc),
                                      max_travel_minutes=routing['max_travel_minutes'], max_destinations=routing['max_destinations'])
    result = compare_modes(entities, claims, origin.lat, origin.lon, service, **parameters,
                           spread_demand=spread_demand, pressure_inputs=pressure_inputs,
                           measured_load=measured, travel_times=routing_results)
    result['route_status'] = routing_results
    result['origin'] = origin
    result['service'] = service
    result['excluded_by_caller'] = sorted(excluded)
    result['rows'] = format_recommendations(result['rows'], contacts, origin, limits['max_radius_km'], feedback)
    location_fields = ['facility_id', 'location_source_urls', 'review_flags']
    location_details = geography.reindex(columns=location_fields).rename(columns={'review_flags': 'location_review_flags'})
    for key in ('rows', 'trace'):
        result[key] = result[key].merge(location_details, on='facility_id', how='left', validate='m:1')
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
