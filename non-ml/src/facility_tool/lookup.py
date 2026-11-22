"""Adapt user inputs, evidence and optional travel estimates to the shared selector."""

from datetime import datetime, timezone
import re

import pandas as pd

from .geography import place_key
from .network_load import read_network_pressure
from .origin_index import resolve_prepared_origin
from .patient_experience import PATIENT_EXPERIENCE_COLUMNS
from .query_assets import LOOKUP_QUALITY_FIELDS, LOOKUP_VERIFICATION_FIELDS
from .recommend import compare_prepared_modes, routine_load_allowed
from .response import CONTACT_FIELDS, format_recommendations
from .travel import MODES as TRANSPORT_MODES, estimate_travel


TOP_RECOMMENDATIONS = 5


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


def _routing_summary(summary, live, mode, window):
    row = summary.iloc[0]
    comparison = row['routing_comparison_status'] if live else 'offline'
    rankable = int(row['n_eligible'])
    eligible = int(row['n_documented_candidates'])
    unreachable = int(row['n_route_unreachable'])
    unavailable = int(row['n_route_unavailable'])
    if not live:
        message = 'Offline ranking uses straight-line distances.'
    elif not eligible:
        message = 'No branch has both documented service evidence and a usable location in this snapshot.'
    else:
        label = mode.replace('_', ' ')
        message = f'TravelTime supplied comparable {label} routes for {rankable} of {eligible} documented branches.'
        if unreachable:
            message += f' No route was found for {unreachable} within the {window}-minute provider window.'
        if unavailable:
            message += f' Route estimates were unavailable for {unavailable}. The comparison is incomplete.'
    return {
        'ranking_basis': row['ranking_travel_metric'], 'comparison_status': comparison,
        'eligible_count': eligible, 'rankable_count': rankable, 'unreachable_count': unreachable,
        'unavailable_count': unavailable, 'max_travel_minutes': window if live else None,
        'driving_fallback_count': 0, 'driving_comparison_status': None, 'message': message,
    }


def _add_driving_options(result, driving, mode):
    """Keep the best driver option when the requested mode cannot rank it."""
    preferred = result['rows']
    preferred_count = int((preferred['mode'].eq('confirmed') & preferred['kind'].eq('recommendation')).sum())
    fallback = driving['rows'].loc[driving['rows']['kind'].eq('recommendation')].copy()
    if preferred_count:
        rankable = result['trace'].loc[result['trace']['mode'].eq('confirmed') & result['trace']['decision'].eq('eligible')]
        fallback = fallback.loc[fallback['rank'].eq(1) & ~fallback['facility_id'].isin(rankable['facility_id'])].copy()
    if fallback.empty:
        return 0

    count = int(fallback['mode'].eq('confirmed').sum())
    trace = result['trace']
    statuses = trace.loc[trace['mode'].eq('confirmed')].set_index('facility_id')['requested_mode_route_status']
    fallback['requested_mode_route_status'] = fallback['facility_id'].map(statuses)
    label = mode.replace('_', ' ')
    fallback['route_note'] = fallback['requested_mode_route_status'].map(
        lambda status: f'Car only for this search. TravelTime found no {label} route in its search window.'
        if status == 'not_routed_within_limit' else f'Driving option. A {label} route estimate was unavailable.',
    )
    fallback['driving_rank'] = fallback['rank'].astype('Int64')
    keep = min(preferred_count, TOP_RECOMMENDATIONS - count)
    fallback['rank'] = fallback['driving_rank'] + keep

    # Car options occupy slots in the same displayed list. Keep the rest of the ranking in the trace.
    displaced = trace['kind'].eq('recommendation') & trace['rank'].gt(keep)
    trace.loc[displaced, 'rank'] += count
    trace.loc[displaced, 'selected'] = False
    trace = trace.loc[~trace['facility_id'].isin(fallback['facility_id'])]
    result['trace'] = pd.concat([trace, fallback], ignore_index=True)
    rows = result['trace'].loc[result['trace']['selected']].copy()
    rows['_lead'] = rows['kind'].ne('recommendation')
    result['rows'] = (rows.sort_values(['mode', '_lead', 'rank'], kind='stable')
                      .drop(columns='_lead').reset_index(drop=True))
    result['summary']['n_recommendations'] = keep + count
    return count


def lookup(store, config, *, concern, location, spread_demand=False, traveltime_enabled=None,
           travel_mode=None, route_sort=None, facility_sort=None, travel_weight=None, max_extra_km=None,
           max_extra_minutes=None, pressure_inputs=None, measured_load=None, load_context='operational',
           feedback=(), consider_equity=False, equity_evidence=None, network_forecasts=None):
    """Return route-backed branches with optional equity scoring in balanced mode."""
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
    equity_status, equity_reason = '', ''
    if consider_equity:
        concern_text = place_key(concern)
        if facility_sort == 'closest':
            equity_status, equity_reason = 'off', 'Choose balanced sorting to consider equity scores.'
        elif re.search(r'\b(?:emergency|urgent)\b', concern_text):
            equity_status, equity_reason = 'urgent', 'Time-critical searches use the ordinary order.'
        elif service == 'dialysis' and re.search(r'\bperitoneal\b', concern_text):
            equity_status = 'incomparable'
            equity_reason = 'A peritoneal dialysis comparison needs paired evidence for that modality.'
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
    if spread_demand and load_context == 'operational' and measured_load is None:
        measured_load = store.read(f'lookup_measured_{service}', optional=True)
    if network_forecasts is not None and (load_context != 'planning' or pressure_inputs is not None):
        raise ValueError('Choose planning mode and one pressure source for network forecasts.')
    if spread_demand and load_context == 'planning' and pressure_inputs is None:
        if network_forecasts is None:
            pressure_inputs = store.read(f'lookup_pressure_{service}', optional=True)
        else:
            pressure_inputs = read_network_pressure(network_forecasts, service, candidates['facility_id'])
            pressure_inputs = pressure_inputs.loc[pressure_inputs['service_evidence'].eq('reported_activity_in_scope')]
    load_hold = ''
    if spread_demand and (load_context == 'operational' or network_forecasts is not None):
        concern_text = place_key(concern)
        urgency = re.sub(r'\b(?:non|not) (?:urgent|emergency)\b', '', concern_text)
        if not routine_load_allowed(service) or re.search(r'\b(?:emergency|urgent)\b', urgency):
            load_hold = 'non_emergency_only'
        elif service == 'dialysis' and re.search(r'\bperitoneal\b', concern_text):
            load_hold = 'no_comparable_load_modality'
    now = datetime.now(timezone.utc)
    parameters = dict(top_k=TOP_RECOMMENDATIONS, max_radius_km=None,
                      max_load_age_hours=limits['max_load_age_hours'], as_of=config['as_of'] if demo else now,
                      travel_weight=limits['travel_weight'] if travel_weight is None else travel_weight,
                      max_extra_km=limits['max_extra_km'] if max_extra_km is None else max_extra_km,
                      max_extra_minutes=limits['max_extra_minutes'] if max_extra_minutes is None else max_extra_minutes,
                      spread_demand=spread_demand and not load_hold,
                      pressure_inputs=pressure_inputs, measured_load=measured_load, load_context=load_context,
                      quality_policy=quality_policy,
                      consider_equity=consider_equity and not equity_reason,
                      equity_evidence=equity_evidence)
    # Synthetic coordinates and IDs never go to the routing provider.
    routing_results = pd.DataFrame()
    driving_results = pd.DataFrame()
    live = use_travel and not demo
    window = routing['max_travel_minutes']
    if live:
        supported = candidates['evidence_hold_reason'].eq('') | candidates['model_leads'].map(bool)
        routeable = candidates.loc[supported & candidates['location_routing_eligible'].eq(True)]
        sites = routeable[['facility_id', 'site_lat', 'site_lon', 'location_routing_eligible']].rename(
            columns={'site_lat': 'lat', 'site_lon': 'lon'},
        )
        request = dict(enabled=True, departure_time=now, max_travel_minutes=window,
                       max_destinations=routing['max_destinations'])
        routing_results = estimate_travel((origin.lat, origin.lon), sites, mode=travel_mode, **request)
    result = compare_prepared_modes(candidates, origin.lat, origin.lon, service, **parameters,
                                    route_sort=route_sort, travel_times=routing_results,
                                    required_travel_mode=travel_mode if live else None)
    for table in ('rows', 'trace'):
        result[table] = result[table].assign(route_note='', driving_rank=pd.NA,
                                            requested_mode_route_status=result[table]['travel_status'])
    coverage = _routing_summary(result['summary'], live, travel_mode, window)
    if live and travel_mode != 'driving' and coverage['rankable_count'] < coverage['eligible_count']:
        driving_results = estimate_travel((origin.lat, origin.lon), sites, mode='driving', **request)
        driving = compare_prepared_modes(candidates, origin.lat, origin.lon, service, **parameters,
                                         route_sort='distance', travel_times=driving_results,
                                         required_travel_mode='driving')
        coverage['driving_comparison_status'] = driving['summary'].iloc[0]['routing_comparison_status']
        count = _add_driving_options(result, driving, travel_mode)
        if count:
            coverage['driving_fallback_count'] = count
            label = 'option' if count == 1 else 'options'
            coverage['message'] += f' Showing {count} separately labeled driving {label}.'
        if coverage['driving_comparison_status'] == 'partial':
            coverage['message'] += ' The driving comparison is incomplete.'
    result['summary']['n_driving_fallback'] = coverage['driving_fallback_count']
    if load_hold:
        for table in ('rows', 'trace', 'summary'):
            result[table]['load_status'] = load_hold
    if equity_reason:
        result['summary']['equity_status'] = equity_status
        result['summary']['equity_ranking_reason'] = equity_reason
    result['route_status'] = routing_results
    result['driving_route_status'] = driving_results
    result['routing'] = coverage
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
    result['rows'] = format_recommendations(result['rows'], contact_choices, origin, feedback=feedback)
    return result


def side_by_side(rows):
    """Compare ranks and field values for the same facility across the three views."""
    columns = ['facility_id', 'name', 'mode', 'rank', 'capacity', 'capacity_method',
               'numberDoctors', 'numberDoctors_method']
    travel_fields = ['travel_mode', 'route_note', 'driving_rank']
    eligible = rows.loc[rows['kind'].eq('recommendation'), columns]
    if eligible.empty:
        return pd.DataFrame(columns=[*columns, *travel_fields])
    comparison = eligible.set_index(['facility_id', 'name', 'mode']).unstack('mode')
    comparison.columns = [f'{metric} · {mode}' for metric, mode in comparison.columns]
    notes = rows.loc[rows['kind'].eq('recommendation') & rows['mode'].eq('confirmed')].reindex(
        columns=['facility_id', *travel_fields],
    )
    comparison = comparison.reset_index().merge(notes, on='facility_id', how='left', validate='1:1')
    if 'rank · confirmed' in comparison:
        comparison = comparison.sort_values('rank · confirmed', kind='stable', ignore_index=True)
    return comparison
