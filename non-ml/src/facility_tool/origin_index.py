"""Prepare place resolution once for an offline recommendation snapshot."""

from collections import defaultdict
import re
from statistics import median

import pandas as pd

from .geography import Origin, india_point, pin_key, place_key, state_key


COLUMNS = (
    'lookup_key', 'origin_kind', 'name_key', 'state_key', 'resolution',
    'lat', 'lon', 'label', 'basis', 'choices',
)
_COORDINATES = re.compile(r'([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)')
_BASES = (
    'Approximate city origin from listed facility coordinates',
    'Approximate district origin from post-office coordinates',
    'Approximate district origin from listed facility coordinates',
    'Approximate origin from post-office coordinates',
)


def _lookup_key(category, name, state):
    return f'{category}|{name}|{state}'


def _groups(frame, field, *, facility=False):
    if frame.empty:
        return {}
    required = {field, 'state', 'district', 'lat', 'lon'}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f'Origin source using {field!r} is missing columns: {sorted(missing)}.')
    use_audit = facility and 'location_routing_eligible' in frame
    normalize = pin_key if field == 'pin' else place_key
    groups = defaultdict(list)
    for row in frame.to_dict(orient='records'):
        name = normalize(row[field])
        if not name:
            continue
        state = state_key(row['state'])
        district = place_key(row['district'])
        if facility and not district:
            district = place_key(row.get('postal_district'))
        flag = row.get('location_routing_eligible')
        permitted = not use_audit or (pd.api.types.is_bool(flag) and bool(flag))
        conflict = row.get('location_conflict')
        conflicting = facility and pd.api.types.is_bool(conflict) and bool(conflict)
        point = None
        if permitted and not conflicting and india_point(row['lat'], row['lon']):
            point = (float(row['lat']), float(row['lon']))
        area = ', '.join(str(row[key]).strip() for key in ('district', 'state') if place_key(row[key]))
        value = (state, district, area, point)
        groups[(name, '')].append(value)
        if state:
            groups[(name, state)].append(value)
    return groups


def _summarize(category, field, basis, groups):
    for (name, state), members in groups.items():
        states = {member[0] for member in members if member[0]}
        districts = {member[1] for member in members if member[1]}
        choices = sorted({member[2] for member in members if member[2]})
        points = [member[3] for member in members if member[3] is not None]
        ambiguous = len(members) > 1 if field == 'pin' else len(states) > 1 or (field == 'city' and len(districts) > 1)
        if ambiguous:
            resolution, lat, lon = 'ambiguous', None, None
        elif not points:
            resolution, lat, lon = 'unusable', None, None
        else:
            lats, lons = zip(*points)
            resolution, lat, lon = 'resolved', median(lats), median(lons)
        label = f'{name}, {choices[0]}' if field == 'pin' and len(choices) == 1 else name
        key = _lookup_key(category, name, state)
        yield key, (key, field, name, state, resolution, lat, lon, label, basis, choices)


def build_origin_index(entities, postal_areas):
    """Save one sorted decision per normalized place or PIN and optional state.

    Facility centroids use routing-eligible points when the location audit is
    supplied. Withheld branches still contribute labels to ambiguity checks.
    Prefer cities, then postal districts, then districts based on facility
    coordinates. A preferred place with usable coordinates or ambiguous area
    labels resolves the interpretation before considering later sources. A
    state-qualified city with conflicting districts may use the corresponding
    official postal district center when that district is uniquely resolved.
    """
    prepared = {}
    sources = (
        ('place', 'city', entities, True),
        ('place', 'district', postal_areas, False),
        ('place', 'district', entities, True),
        ('pin', 'pin', postal_areas, False),
    )
    for (category, field, frame, facility), basis in zip(sources, _BASES):
        for key, row in _summarize(category, field, basis, _groups(frame, field, facility=facility)):
            previous = prepared.get(key)
            official_area_fallback = (
                previous is not None and previous[1] == 'city' and previous[4] == 'ambiguous'
                and field == 'district' and not facility and bool(row[3]) and row[4] == 'resolved'
            )
            if previous is None or previous[4] == 'unusable' or official_area_fallback:
                prepared[key] = row
    return pd.DataFrame([prepared[key] for key in sorted(prepared)], columns=COLUMNS)


def resolve_prepared_origin(query, index):
    """Normalize an input and look up its saved decision without recomputing geography."""
    text = str(query).strip() if query is not None else ''
    coordinates = _COORDINATES.fullmatch(text)
    if coordinates:
        lat, lon = map(float, coordinates.groups())
        if not india_point(lat, lon):
            raise ValueError('Enter latitude, longitude within the India coordinate range.')
        return Origin(lat, lon, text, 'User-supplied coordinates')
    label, _, state = text.partition(',')
    pin = pin_key(label)
    name = pin or place_key(label)
    if not name:
        raise ValueError('Enter a city, district, PIN or latitude, longitude.')
    key = _lookup_key('pin' if pin else 'place', name, state_key(state))
    keys = index['lookup_key']
    position = keys.searchsorted(key)
    if position == len(keys) or keys.iloc[position] != key:
        if pin:
            raise ValueError(f'PIN {pin} was not found in the saved PIN directory. Give a state or a coordinate pair.')
        raise ValueError(f'Could not locate {text!r} with the current inputs. Try a city with its state, a PIN, or latitude, longitude.')
    row = index.iloc[position]
    if row['resolution'] == 'ambiguous':
        if pin:
            choices = ', '.join(row['choices'])
            raise ValueError(f'PIN {pin} found multiple areas: {choices}. Give a state or a coordinate pair.')
        raise ValueError(f'{label.strip()} is ambiguous. Enter a state, PIN or a coordinate pair.')
    if row['resolution'] != 'resolved':
        raise ValueError('The matched area has no usable coordinates. Enter a coordinate pair instead.')
    return Origin(float(row['lat']), float(row['lon']), row['label'] if pin else text, row['basis'])
