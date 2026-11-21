"""Review facility coordinates and postal context with the original source anchors."""

from collections import Counter, defaultdict
from math import isfinite
import re

import pandas as pd

from .geography import india_point, index_postal_areas, match_postal_area, pin_key, place_key
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
FLAGS = (*COORDINATE_FLAGS, *POSTAL_FLAGS, 'published_location_conflict', 'shared_exact_coordinates')
MISSING_MARKERS = ('', 'n/a', 'na', 'nan', 'null', 'none', '<na>')
EXPLICIT_ADDRESS_PIN = re.compile(r'\bPIN(?:\s*CODE)?\s*[-:]?\s*([1-9]\d{2})\s*(\d{3})(?!\d)', re.IGNORECASE)
POSTAL_COLUMNS = (
    'postal_resolution', 'postal_candidate_labels', 'postal_state', 'postal_district',
    'postal_centroid_lat', 'postal_centroid_lon', 'postal_centroid_distance_km',
)
LOCATION_COLUMNS = ('name', 'address', 'city', 'district', 'state', 'pin', 'lat', 'lon', 'location_quality')
RECORD_COLUMNS = (
    'record_id', 'source', 'source_record_id', 'input_position', 'source_urls',
    'facility_id', 'link_status', 'link_reason', 'candidate_facility_ids', 'base_record_id',
    'location_record_id', *LOCATION_COLUMNS, 'coordinate_note', 'raw_latitude', 'raw_longitude',
    'raw_pin', 'raw_state', 'explicit_address_pins', 'coordinate_status', *POSTAL_COLUMNS,
    'shared_coordinate_facility_count', 'review_needed', 'review_flags',
)
ENTITY_COLUMNS = (
    'facility_id', 'base_record_id', 'location_record_id', 'base_source', 'base_source_urls',
    'location_source_urls', *LOCATION_COLUMNS, 'coordinate_status', *POSTAL_COLUMNS,
    'shared_coordinate_facility_count', 'review_needed', 'review_flags', 'linked_review_record_ids',
)


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


def _postal_review(row, point, pin_groups, offset_limit):
    pin = pin_key(row.get('pin'))
    matches = pin_groups.get(pin, ())
    status, area = match_postal_area(matches, row.get('state'), row.get('district'))
    labels = sorted({f"{match['district']}, {match['state']}" for match in matches})
    latitude, longitude, distance = None, None, None
    flags = []
    if pin and not matches and pin_groups:
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
            if distance > offset_limit:
                flags.append('far_from_postal_centroid')
    return dict(
        postal_resolution=status, postal_candidate_labels=labels,
        postal_state=area['state'] if area else None, postal_district=area['district'] if area else None,
        postal_centroid_lat=latitude, postal_centroid_lon=longitude, postal_centroid_distance_km=distance,
    ), flags


def _location_review(row, raw_point, pin_groups, shared_points, offset_limit):
    stage = {column: row.get(column) for column in LOCATION_COLUMNS}
    coordinate_status, point = _point_status(*raw_point)
    if coordinate_status == 'missing_coordinates' and place_key(row.get('coordinate_note')) == 'zero placeholder':
        # Some saved source extracts already remove placeholders and retain the reason.
        coordinate_status = 'zero_coordinate_placeholder'
    flags = [] if coordinate_status == COORDINATE_OK else [coordinate_status]
    if place_key(row.get('location_quality')) == 'conflict' or str(row.get('location_conflict')).casefold() == 'true':
        flags.append('published_location_conflict')
    postal, postal_flags = _postal_review(row, point, pin_groups, offset_limit)
    peer_count = len(shared_points.get(point, ()))
    if peer_count > 1:
        flags.append('shared_exact_coordinates')
    stage.update(postal, coordinate_status=coordinate_status, shared_coordinate_facility_count=peer_count)
    return stage, [*flags, *postal_flags]


def _record_review(row, entity, link, pin_groups, shared_points, offset_limit):
    raw = row.get('raw')
    raw = raw if isinstance(raw, dict) else {}
    raw_lat = raw.get('latitude', row.get('lat'))
    raw_lon = raw.get('longitude', row.get('lon'))
    raw_pin = raw.get('address_zipOrPostcode')
    stage, flags = _location_review(row, (raw_lat, raw_lon), pin_groups, shared_points, offset_limit)
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


def audit_locations(records, entities, postal_areas, *, links=None, postal_offset_km=50.0):
    """Return source-row checks, branch review rows, and source-level flag counts.

    A plausible point stays a source claim. Shared points may reflect co-located
    sites, copied coordinates or approximation. Distance to an office centroid
    is a review signal over an editable threshold; the site and postal points
    stay separate. Suspected swaps, conflicting labels, and missing coordinates
    are surfaced with their source data and left for review.
    """
    try:
        offset_limit = float(postal_offset_km)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('postal_offset_km must be a positive finite number.') from None
    if pd.api.types.is_bool(postal_offset_km) or not isfinite(offset_limit) or offset_limit <= 0:
        raise ValueError('postal_offset_km must be a positive finite number.')
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
    for key, row in entity_rows.items():
        _, point = _point_status(row.get('lat'), row.get('lon'))
        if point is not None:
            shared_points[point].add(key)

    source_review = []
    flagged_record_ids = defaultdict(list)
    for key, row in source_rows.items():
        link = link_rows.get(key, {})
        entity = entity_rows.get(link.get('facility_id'), {})
        review = _record_review(row, entity, link, pin_groups, shared_points, offset_limit)
        source_review.append(review)
        if _present(review['facility_id']) and review['review_needed']:
            flagged_record_ids[review['facility_id']].append(key)

    branch_review = []
    for key, row in entity_rows.items():
        stage, flags = _location_review(row, (row.get('lat'), row.get('lon')), pin_groups, shared_points, offset_limit)
        base_id, location_id = row.get('base_record_id'), row.get('location_record_id')
        branch_review.append(dict(
            stage, facility_id=key, base_record_id=base_id, location_record_id=location_id,
            base_source=row.get('base_source'), base_source_urls=source_rows.get(base_id, {}).get('source_urls', []),
            location_source_urls=source_rows.get(location_id, {}).get('source_urls', []),
            review_needed=bool(flags), review_flags=flags, linked_review_record_ids=flagged_record_ids.get(key, []),
        ))
    record_audit = pd.DataFrame(source_review, columns=RECORD_COLUMNS)
    entity_review = pd.DataFrame(branch_review, columns=ENTITY_COLUMNS)
    summary = pd.DataFrame(
        _summary('record', record_audit, 'source') + _summary('entity', entity_review, 'base_source'),
        columns=['scope', 'source', 'check', 'count', 'total'],
    )
    return record_audit, entity_review, summary
