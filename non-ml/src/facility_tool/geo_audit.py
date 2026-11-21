"""Review facility coordinates and postal context with the original source anchors."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
import re
from statistics import median
from urllib.parse import urlsplit

import pandas as pd

from .geography import india_point, index_postal_areas, match_postal_area, pin_key, place_key, state_key
from .recommend import haversine_km


COORDINATE_OK = 'within_india_bounds'
COORDINATE_FLAGS = (
    'missing_coordinates', 'invalid_coordinates', 'zero_coordinate_placeholder',
    'likely_lat_lon_swap', 'outside_india_bounds',
)
POSTAL_FLAGS = (
    'declared_pin_invalid', 'declared_pin_conflicts_with_address', 'multiple_explicit_address_pins',
    'pin_not_in_directory', 'pin_state_conflict', 'pin_district_conflict', 'ambiguous_pin_area',
    'far_from_postal_centroid',
)
FLAGS = (*COORDINATE_FLAGS, *POSTAL_FLAGS, 'published_location_conflict', 'shared_exact_coordinates',
         'non_site_coordinate_quality', 'state_point_conflict', 'district_point_conflict', 'linked_point_state_conflict',
         'shared_point_cross_state_conflict', 'reliable_pin_point_conflict', 'reviewed_coordinate_conflict')
HOLD_REASONS = {
    'published_location_conflict': 'Published facility location details conflict.',
    'pin_state_conflict': 'The published facility state disagrees with the PIN directory.',
    'state_point_conflict': 'The site point is far outside the published state’s postal reference area.',
    'district_point_conflict': 'The site point is far outside the published district’s postal reference area.',
    'linked_point_state_conflict': 'The branch and its selected coordinate source publish different states.',
    'shared_point_cross_state_conflict': 'Facilities in three or more published states share this exact point.',
    'reliable_pin_point_conflict': 'The site point is far from a uniquely matched, explicitly published PIN.',
    'reviewed_coordinate_conflict': 'A reviewed source contradicts these exact published coordinates.',
    'missing_coordinates': 'A branch-level site coordinate is missing.',
    'invalid_coordinates': 'The published site coordinates are invalid.',
    'zero_coordinate_placeholder': 'The source used a zero coordinate placeholder.',
    'likely_lat_lon_swap': 'The latitude and longitude appear to be swapped.',
    'outside_india_bounds': 'The published coordinates are outside the broad India bounds.',
    'non_site_coordinate_quality': 'The source coordinate quality does not identify a usable site point.',
}
# Recognize explicit labels even when the PIN directory is absent.
STATES = frozenset(
    'andaman and nicobar islands|andhra pradesh|arunachal pradesh|assam|bihar|chandigarh|chhattisgarh|'
    'dadra and nagar haveli and daman and diu|delhi|goa|gujarat|haryana|himachal pradesh|jammu and kashmir|'
    'jharkhand|karnataka|kerala|ladakh|lakshadweep|madhya pradesh|maharashtra|manipur|meghalaya|mizoram|'
    'nagaland|odisha|puducherry|punjab|rajasthan|sikkim|tamil nadu|telangana|tripura|uttar pradesh|'
    'uttarakhand|west bengal'.split('|')
)
ADDITIONAL_STATE_ALIASES = {
    'andaman nicobar islands': 'andaman and nicobar islands',
    'jammu kashmir': 'jammu and kashmir',
    'the dadra and nagar haveli and daman and diu': 'dadra and nagar haveli and daman and diu',
}
MIN_STATE_REFERENCE_POINTS = 30
MIN_DISTRICT_REFERENCE_POINTS = 6
MAX_DISTRICT_REFERENCE_SPAN_KM = 250
NON_SITE_QUALITY = {'missing', 'invalid', 'ambiguous', 'placeholder', 'centroid', 'approximate', 'unresolved'}
MISSING_MARKERS = ('', 'n/a', 'na', 'nan', 'null', 'none', '<na>')
EXPLICIT_ADDRESS_PIN = re.compile(r'\bPIN(?:\s*CODE)?\s*[-:]?\s*([1-9]\d{2})\s*(\d{3})(?!\d)', re.IGNORECASE)
EXPLICIT_ADDRESS_DISTRICT = re.compile(r'(?:^|[,;])\s*Dist(?:rict)?\.?\s*:?\s+([^,;]+)\s*$', re.IGNORECASE)
POSTAL_COLUMNS = (
    'postal_resolution', 'postal_candidate_labels', 'postal_state', 'postal_district',
    'postal_centroid_lat', 'postal_centroid_lon', 'postal_centroid_distance_km',
    'postal_usable_office_points', 'postal_office_point_spread_km',
)
POINT_REVIEW_COLUMNS = (
    'state_point_status', 'state_point_reference_state', 'state_point_reference_count',
    'state_point_outside_km', 'state_point_buffer_km', 'district_point_status',
    'district_point_reference_district', 'district_point_reference_count', 'district_point_outside_km',
    'district_point_buffer_km', 'shared_point_state_labels',
    'reviewed_coordinate_source_url', 'reviewed_coordinate_reason',
)
LOCATION_COLUMNS = ('name', 'address', 'city', 'district', 'state', 'pin', 'lat', 'lon', 'location_quality')
RECORD_COLUMNS = (
    'record_id', 'source', 'source_record_id', 'input_position', 'source_urls',
    'facility_id', 'link_status', 'link_reason', 'candidate_facility_ids', 'base_record_id',
    'location_record_id', *LOCATION_COLUMNS, 'coordinate_note', 'raw_latitude', 'raw_longitude',
    'raw_pin', 'raw_state', 'explicit_address_pins', 'coordinate_status', *POSTAL_COLUMNS, *POINT_REVIEW_COLUMNS,
    'shared_coordinate_facility_count', 'review_needed', 'review_flags',
)
ENTITY_COLUMNS = (
    'facility_id', 'base_record_id', 'location_record_id', 'base_source', 'base_source_urls',
    'location_source_urls', *LOCATION_COLUMNS, 'coordinate_status', *POSTAL_COLUMNS, *POINT_REVIEW_COLUMNS,
    'shared_coordinate_facility_count', 'location_routing_eligible', 'location_hold_reason',
    'review_needed', 'review_flags', 'linked_review_record_ids',
)


@dataclass(frozen=True)
class _LocationReferences:
    pins: dict
    state_envelopes: dict
    district_envelopes: dict
    shared_points: dict
    shared_states: dict
    reviewed_holds: dict
    postal_offset_km: float
    postal_hold_km: float
    state_buffer_km: float
    district_buffer_km: float


def _present(value):
    if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
        return False
    return not isinstance(value, str) or value.strip().casefold() not in MISSING_MARKERS


def _point_status(lat, lon):
    if not (_present(lat) and _present(lon)):
        return 'missing_coordinates', None
    if pd.api.types.is_bool(lat) or pd.api.types.is_bool(lon):
        return 'invalid_coordinates', None
    try:
        point = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        return 'invalid_coordinates', None
    if not all(isfinite(value) for value in point):
        return 'invalid_coordinates', None
    if 0 in point:
        return 'zero_coordinate_placeholder', None
    if india_point(*point):
        return COORDINATE_OK, point
    if india_point(*point[::-1]):
        return 'likely_lat_lon_swap', None
    return 'outside_india_bounds', None


def _recognized_state(value):
    label = state_key(value)
    label = ADDITIONAL_STATE_ALIASES.get(label, label)
    return label if label in STATES else ''


def _state_envelopes(pin_groups):
    points = defaultdict(set)
    for rows in pin_groups.values():
        labels = {_recognized_state(row['state']) for row in rows}
        if len(labels) != 1 or '' in labels:
            continue
        sites = {(float(row['lat']), float(row['lon'])) for row in rows if india_point(row['lat'], row['lon'])}
        if sites:
            latitudes, longitudes = zip(*sites)
            points[next(iter(labels))].add((median(latitudes), median(longitudes)))
    envelopes = {}
    for label, sites in points.items():
        if len(sites) >= MIN_STATE_REFERENCE_POINTS:
            values = pd.DataFrame(list(sites), columns=['lat', 'lon'])
            # Trim isolated bad postal coordinates before adding the buffer.
            lower = tuple(values.quantile(0.02, interpolation='higher'))
            upper = tuple(values.quantile(0.98, interpolation='lower'))
            envelopes[label] = (lower, upper, len(sites))
    return envelopes


def _tight_postal_points(area):
    spread, count = area.get('office_point_spread_km'), area.get('usable_office_points')
    return (
        pd.api.types.is_number(spread) and not pd.api.types.is_bool(spread) and 0 <= spread <= 25
        and pd.api.types.is_number(count) and not pd.api.types.is_bool(count)
        and isfinite(count) and count >= 1
    )


def _district_envelopes(pin_groups):
    points = defaultdict(set)
    for rows in pin_groups.values():
        if len(rows) != 1:
            continue
        area = rows[0]
        label = (_recognized_state(area['state']), place_key(area['district']))
        if all(label) and _tight_postal_points(area) and india_point(area['lat'], area['lon']):
            points[label].add((float(area['lat']), float(area['lon'])))
    envelopes = {}
    for label, sites in points.items():
        if len(sites) >= MIN_DISTRICT_REFERENCE_POINTS:
            latitudes, longitudes = zip(*sites)
            lower, upper = (min(latitudes), min(longitudes)), (max(latitudes), max(longitudes))
            if haversine_km(*lower, *upper) <= MAX_DISTRICT_REFERENCE_SPAN_KM:
                envelopes[label] = (lower, upper, len(sites))
    return envelopes


def _outside_envelope_km(point, envelope):
    if point is None or envelope is None:
        return None
    lower, upper, _ = envelope
    nearest = tuple(max(low, min(value, high)) for value, low, high in zip(point, lower, upper))
    return haversine_km(*point, *nearest)


def _state_review(row, point_source, point, references):
    declared = _recognized_state(row.get('state'))
    coordinate_source = _recognized_state(point_source.get('state'))
    label = declared or coordinate_source
    envelope = references.state_envelopes.get(label)
    outside = _outside_envelope_km(point, envelope)
    flags = []
    if declared and coordinate_source and declared != coordinate_source:
        status = 'linked_state_conflict'
        flags.append('linked_point_state_conflict')
    elif point is None:
        status = 'coordinates_unavailable'
    elif not label:
        status = 'published_state_unrecognized'
    elif envelope is None:
        status = 'reference_unavailable'
    elif outside > references.state_buffer_km:
        status = 'outside_buffered_reference'
        flags.append('state_point_conflict')
    else:
        status = 'within_buffered_reference'
    return dict(
        state_point_status=status, state_point_reference_state=label,
        state_point_reference_count=envelope[2] if envelope else 0, state_point_outside_km=outside,
        state_point_buffer_km=references.state_buffer_km,
    ), flags


def _district_review(row, sources, point, state, postal, references):
    labels = set()
    for item in (row, *sources):
        if text := place_key(item.get('district')):
            labels.add(text)
        raw = item.get('raw')
        addresses = [item.get('address')]
        if isinstance(raw, dict):
            original_lines = [raw.get(f'address_line{index}') for index in range(1, 4)]
            addresses.append(', '.join(str(line).strip() for line in original_lines if _present(line)))
        for address in addresses:
            if match := EXPLICIT_ADDRESS_DISTRICT.search(str(address)):
                if text := place_key(match.group(1)):
                    labels.add(text)
    district = next(iter(labels)) if len(labels) == 1 else ''
    envelope = references.district_envelopes.get((state['state_point_reference_state'], district))
    outside = _outside_envelope_km(point, envelope)
    postal_district = place_key(postal['postal_district'])
    unresolved_pin = (
        pin_key(row.get('pin')) in references.pins
        and postal['postal_resolution'] not in ('unique_pin', 'unique_pin_with_state')
    )
    postal_disagrees = postal_district and district and postal_district != district
    flags = []
    if len(labels) > 1 or postal_disagrees or unresolved_pin:
        status = 'conflicting_district_labels'
    elif point is None:
        status = 'coordinates_unavailable'
    elif not district:
        status = 'published_district_unknown'
    elif envelope is None:
        status = 'reference_unavailable'
    elif outside > references.district_buffer_km:
        status = 'outside_buffered_reference'
        flags.append('district_point_conflict')
    else:
        status = 'within_buffered_reference'
    return dict(
        district_point_status=status, district_point_reference_district=district,
        district_point_reference_count=envelope[2] if envelope else 0,
        district_point_outside_km=outside, district_point_buffer_km=references.district_buffer_km,
    ), flags


def _explicit_pins(row):
    raw = row.get('raw')
    raw = raw if isinstance(raw, dict) else {}
    pins = {''.join(parts) for parts in EXPLICIT_ADDRESS_PIN.findall(str(row.get('address')))}
    if declared := pin_key(raw.get('address_zipOrPostcode')):
        pins.add(declared)
    return pins


def _index_reviewed_holds(reviewed_holds):
    index = defaultdict(dict)
    for entry in reviewed_holds or ():
        if not isinstance(entry, dict):
            raise ValueError('Each reviewed coordinate hold must be an object.')
        ident = str(entry.get('record_id') or '').strip()
        _, point = _point_status(entry.get('latitude'), entry.get('longitude'))
        url, reason = (str(entry.get(field) or '').strip() for field in ('source_url', 'reason'))
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        if not ident or not point or not valid_url or not reason or point in index[ident]:
            raise ValueError('Each reviewed coordinate hold needs a unique source ID and point, a public URL, and a reason.')
        index[ident][point] = (url, reason)
    return index


def _reviewed_point(point, location_record_id, references):
    for checked, citation in references.reviewed_holds.get(location_record_id, {}).items():
        if point is not None and all(abs(value - bound) <= 0.000001 for value, bound in zip(point, checked)):
            return citation
    return '', ''


def _postal_review(row, point, point_review, sources, references):
    pin = pin_key(row.get('pin'))
    matches = references.pins.get(pin, ())
    status, area = match_postal_area(matches, row.get('state'), row.get('district'))
    labels = sorted({f"{match['district']}, {match['state']}" for match in matches})
    latitude, longitude, distance = None, None, None
    spread = area.get('office_point_spread_km') if area else None
    usable_offices = area.get('usable_office_points') if area else None
    flags = []
    if pin and not matches and references.pins:
        flags.append('pin_not_in_directory')
    if status == 'state_conflict':
        flags.append('pin_state_conflict')
    if status == 'district_conflict':
        flags.append('pin_district_conflict')
    if status == 'ambiguous_pin':
        flags.append('ambiguous_pin_area')
    if area and india_point(area.get('lat'), area.get('lon')):
        latitude, longitude = float(area['lat']), float(area['lon'])
        if point is not None:
            distance = haversine_km(*point, latitude, longitude)
            if distance > references.postal_offset_km:
                flags.append('far_from_postal_centroid')
            if distance > references.postal_hold_km and len(matches) == 1 and _tight_postal_points(area):
                reference_state = point_review['state_point_reference_state']
                postal_state = _recognized_state(area['state'])
                coherent_pins = set().union(*(_explicit_pins(source) for source in sources))
                centroid_offset = _outside_envelope_km((latitude, longitude), references.state_envelopes.get(postal_state))
                if reference_state and reference_state == postal_state and coherent_pins == {pin} and centroid_offset == 0:
                    flags.append('reliable_pin_point_conflict')
    return dict(
        postal_resolution=status, postal_candidate_labels=labels,
        postal_state=area['state'] if area else None, postal_district=area['district'] if area else None,
        postal_centroid_lat=latitude, postal_centroid_lon=longitude, postal_centroid_distance_km=distance,
        postal_usable_office_points=usable_offices, postal_office_point_spread_km=spread,
    ), flags


def _location_review(row, raw_point, sources, location_record_id, references):
    stage = {column: row.get(column) for column in LOCATION_COLUMNS}
    coordinate_status, point = _point_status(*raw_point)
    if coordinate_status == 'missing_coordinates' and place_key(row.get('coordinate_note')) == 'zero placeholder':
        # Some saved source extracts already remove placeholders and retain the reason.
        coordinate_status = 'zero_coordinate_placeholder'
    flags = [] if coordinate_status == COORDINATE_OK else [coordinate_status]
    quality_terms = set(place_key(row.get('location_quality')).split())
    if 'conflict' in quality_terms or str(row.get('location_conflict')).casefold() == 'true':
        flags.append('published_location_conflict')
    if coordinate_status == COORDINATE_OK and (not quality_terms or NON_SITE_QUALITY & quality_terms):
        flags.append('non_site_coordinate_quality')
    state, state_flags = _state_review(row, sources[-1], point, references)
    postal, postal_flags = _postal_review(row, point, state, sources, references)
    district, district_flags = _district_review(row, sources, point, state, postal, references)
    peer_count = len(references.shared_points.get(point, ()))
    if peer_count > 1:
        flags.append('shared_exact_coordinates')
    shared_states = sorted(references.shared_states.get(point, ()))
    if len(shared_states) >= 3:
        flags.append('shared_point_cross_state_conflict')
    url, reason = _reviewed_point(point, location_record_id, references)
    if url:
        flags.append('reviewed_coordinate_conflict')
    stage.update(
        postal, **state, **district, coordinate_status=coordinate_status, shared_coordinate_facility_count=peer_count,
        shared_point_state_labels=shared_states, reviewed_coordinate_source_url=url, reviewed_coordinate_reason=reason,
    )
    return stage, [*flags, *state_flags, *district_flags, *postal_flags]


def _record_review(row, entity, link, references):
    raw = row.get('raw')
    raw = raw if isinstance(raw, dict) else {}
    raw_lat = raw.get('latitude', row.get('lat'))
    raw_lon = raw.get('longitude', row.get('lon'))
    raw_pin = raw.get('address_zipOrPostcode')
    stage, flags = _location_review(row, (raw_lat, raw_lon), (row,), row['record_id'], references)
    address = row.get('address')
    address_pins = sorted({''.join(parts) for parts in EXPLICIT_ADDRESS_PIN.findall(str(address))})
    declared_pin = pin_key(raw_pin)
    if _present(raw_pin) and not declared_pin:
        flags.append('declared_pin_invalid')
    if declared_pin and any(pin != declared_pin for pin in address_pins):
        flags.append('declared_pin_conflicts_with_address')
    if len(address_pins) > 1:
        flags.append('multiple_explicit_address_pins')
    return dict(
        stage, record_id=row['record_id'], source=row.get('source'), source_record_id=row.get('source_record_id'),
        input_position=row.get('input_position'), source_urls=row.get('source_urls'),
        facility_id=link.get('facility_id'), link_status=link.get('link_status'), link_reason=link.get('link_reason'),
        candidate_facility_ids=link.get('candidate_facility_ids', []), base_record_id=entity.get('base_record_id'),
        location_record_id=entity.get('location_record_id'), coordinate_note=row.get('coordinate_note'),
        raw_latitude=raw_lat, raw_longitude=raw_lon, raw_pin=raw_pin, raw_state=raw.get('address_stateOrRegion', row.get('state')),
        explicit_address_pins=address_pins, review_needed=bool(flags), review_flags=flags,
    )


def _summary(scope, frame, source_column):
    rows = []
    for source, group in frame.groupby(source_column, dropna=False, sort=True):
        counts = Counter(flag for flags in group['review_flags'] for flag in flags)
        checks = {'review_needed': int(group['review_needed'].sum()), **{flag: counts[flag] for flag in FLAGS}}
        rows.extend(dict(scope=scope, source=source, check=check, count=count, total=len(group))
                    for check, count in checks.items())
    return rows


def _hold_reason(flags, source_coordinate_status):
    reasons = set(flags)
    if 'missing_coordinates' in reasons and source_coordinate_status in COORDINATE_FLAGS:
        # Ingestion may clear an invalid point. Keep the source reason when available.
        reasons.remove('missing_coordinates')
        reasons.add(source_coordinate_status)
    return ' '.join(message for flag, message in HOLD_REASONS.items() if flag in reasons)


def _distance_limit(value, label, *, allow_zero=False):
    try:
        limit = float(value)
    except (TypeError, ValueError, OverflowError):
        limit = -1
    if pd.api.types.is_bool(value) or not isfinite(limit) or limit < 0 or not (limit or allow_zero):
        sign = 'nonnegative' if allow_zero else 'positive'
        raise ValueError(f'{label} must be a {sign} finite number.')
    return limit


def audit_locations(
    records, entities, postal_areas, *, links=None, postal_offset_km=50.0, postal_hold_km=150.0,
    state_point_buffer_km=75.0, district_point_buffer_km=75.0, reviewed_holds=None,
):
    """Return source-row checks, branch review rows, and source-level flag counts.

    State checks use trimmed and buffered envelopes of at least 30 distinct
    PIN-area points. Being inside an envelope leaves the site point unverified.
    An exact published district can use a compact envelope of at least 6
    tightly clustered PIN-area points. Conflicting district labels skip it.
    Far PIN offsets block routing only with an explicit, tightly clustered PIN
    reference. Exact points shared across three states also block routing.
    Optional reviews bind to the selected source and published coordinates,
    with one millionth of a degree tolerance for rounding.
    """
    offset_limit = _distance_limit(postal_offset_km, 'postal_offset_km')
    hold_limit = max(offset_limit, _distance_limit(postal_hold_km, 'postal_hold_km'))
    state_buffer = _distance_limit(state_point_buffer_km, 'state_point_buffer_km', allow_zero=True)
    district_buffer = _distance_limit(district_point_buffer_km, 'district_point_buffer_km', allow_zero=True)
    source_rows = {row['record_id']: row for row in records.to_dict(orient='records')}
    entity_rows = {row['facility_id']: row for row in entities.to_dict(orient='records')}
    if len(source_rows) != len(records) or len(entity_rows) != len(entities):
        raise ValueError('The location audit requires unique record IDs and facility IDs.')
    if links is not None:
        link_rows = {row['record_id']: row for row in links.to_dict(orient='records')}
        if len(link_rows) != len(links):
            raise ValueError('The location audit requires one link per source record.')
    else:
        link_rows = {row['base_record_id']: {'facility_id': key} for key, row in entity_rows.items()}

    pin_groups = index_postal_areas(postal_areas)
    shared_points = defaultdict(set)
    shared_states = defaultdict(set)
    location_ids = {}
    for key, row in entity_rows.items():
        candidate_id = row.get('location_record_id')
        location_id = candidate_id if _present(candidate_id) else row.get('base_record_id')
        location_ids[key] = location_id
        _, point = _point_status(row.get('lat'), row.get('lon'))
        if point is not None:
            shared_points[point].add(key)
            labels = (_recognized_state(item.get('state')) for item in (row, source_rows.get(location_id, {})))
            shared_states[point].update(label for label in labels if label)

    references = _LocationReferences(
        pin_groups, _state_envelopes(pin_groups), _district_envelopes(pin_groups),
        shared_points, shared_states, _index_reviewed_holds(reviewed_holds),
        offset_limit, hold_limit, state_buffer, district_buffer,
    )

    source_review = []
    source_coordinate_status = {}
    flagged_record_ids = defaultdict(list)
    for key, row in source_rows.items():
        link = link_rows.get(key, {})
        entity = entity_rows.get(link.get('facility_id'), {})
        review = _record_review(row, entity, link, references)
        source_review.append(review)
        source_coordinate_status[key] = review['coordinate_status']
        if _present(review['facility_id']) and review['review_needed']:
            flagged_record_ids[review['facility_id']].append(key)

    branch_review = []
    for key, row in entity_rows.items():
        base_id, location_id = row.get('base_record_id'), row.get('location_record_id')
        point_source_id = location_ids[key]
        sources = (source_rows.get(base_id, {}), source_rows.get(point_source_id, {}))
        stage, flags = _location_review(row, (row.get('lat'), row.get('lon')), sources, point_source_id, references)
        hold_reason = _hold_reason(flags, source_coordinate_status.get(point_source_id))
        location_urls = source_rows.get(point_source_id, {}).get('source_urls', [])
        if stage['reviewed_coordinate_source_url']:
            location_urls = list(dict.fromkeys([*location_urls, stage['reviewed_coordinate_source_url']]))
        branch_review.append(dict(
            stage, facility_id=key, base_record_id=base_id, location_record_id=location_id,
            base_source=row.get('base_source'), base_source_urls=source_rows.get(base_id, {}).get('source_urls', []),
            location_source_urls=location_urls,
            location_routing_eligible=not hold_reason, location_hold_reason=hold_reason,
            review_needed=bool(flags), review_flags=flags, linked_review_record_ids=flagged_record_ids.get(key, []),
        ))
    record_audit = pd.DataFrame(source_review, columns=RECORD_COLUMNS)
    entity_review = pd.DataFrame(branch_review, columns=ENTITY_COLUMNS)
    summary = pd.DataFrame(
        _summary('record', record_audit, 'source') + _summary('entity', entity_review, 'base_source'),
        columns=['scope', 'source', 'check', 'count', 'total'],
    )
    return record_audit, entity_review, summary


def attach_routing_review(entities, entity_review):
    """Persist the audited routing decision and its evidence on each branch."""
    fields = ['facility_id', 'location_routing_eligible', 'location_hold_reason',
              'location_source_urls', 'review_flags', *POINT_REVIEW_COLUMNS]
    routing = entity_review[fields].rename(columns={'review_flags': 'location_review_flags'})
    result = entities.merge(routing, on='facility_id', how='left', validate='1:1')
    if result['location_routing_eligible'].isna().any():
        raise ValueError('Each facility must have a saved location routing decision.')
    return result
