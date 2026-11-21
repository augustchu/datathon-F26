"""Resolve search origins and show PIN ambiguity without rewriting site coordinates."""

from dataclasses import dataclass
from math import isfinite
import re
import unicodedata

import pandas as pd


STATE_ALIASES = {
    'delhi nct': 'delhi',
    'nct delhi': 'delhi',
    'nct of delhi': 'delhi',
    'national capital territory of delhi': 'delhi',
    'new delhi': 'delhi',
    'orissa': 'odisha',
    'pondicherry': 'puducherry',
    'tamilnadu': 'tamil nadu',
    'uttaranchal': 'uttarakhand',
    'u p': 'uttar pradesh',
}


@dataclass(frozen=True)
class Origin:
    lat: float
    lon: float
    label: str
    basis: str


def place_key(value):
    if value is None or pd.isna(value):
        return ''
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', str(value)).casefold()))


def pin_key(value):
    text = str(value).strip()
    match = re.fullmatch(r'([1-9]\d{5})(?:\.0+)?', text)
    return match.group(1) if match else ''


def state_key(value):
    """Return a comparable state label while preserving published labels in tables."""
    key = place_key(value)
    return STATE_ALIASES.get(key, key)


def india_point(lat, lon):
    """A broad sanity check. It cannot establish a country's or district's boundary."""
    try:
        x, y = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        return False
    return isfinite(x) and isfinite(y) and 6 <= x <= 38 and 67 <= y <= 98


def _center(rows):
    points = [(float(a), float(b)) for a, b in zip(rows['lat'], rows['lon']) if india_point(a, b)]
    if not points:
        raise ValueError('The matched area has no usable coordinates. Enter a coordinate pair instead.')
    center = pd.DataFrame(points, columns=['lat', 'lon']).median()
    return float(center['lat']), float(center['lon'])


def build_postal_areas(pins):
    """One row per published PIN / state / district, retaining competing mappings."""
    fields = ['pin', 'state', 'district', 'offices', 'lat', 'lon', 'district_choices']
    if pins.empty:
        return pd.DataFrame(columns=fields)
    required = ['pincode', 'statename', 'district', 'latitude', 'longitude']
    missing = set(required).difference(pins.columns)
    if missing:
        raise ValueError(f'PIN directory is missing columns: {sorted(missing)}')
    directory = pins[required].rename(columns={'pincode': 'pin', 'statename': 'state', 'latitude': 'lat', 'longitude': 'lon'}).copy()
    directory['pin'] = directory['pin'].map(pin_key)
    directory['state_key'] = directory['state'].map(state_key)
    directory['district_key'] = directory['district'].map(place_key)
    directory = directory.loc[directory[['pin', 'state_key', 'district_key']].ne('').all(axis=1)]
    rows = []
    for (pin, _, _), group in directory.groupby(['pin', 'state_key', 'district_key'], sort=True):
        try:
            lat, lon = _center(group)
        except ValueError:
            lat, lon = None, None
        rows.append({'pin': pin, 'state': group['state'].iloc[0], 'district': group['district'].iloc[0],
                     'offices': len(group), 'lat': lat, 'lon': lon})
    areas = pd.DataFrame(rows, columns=fields[:-1])
    areas['district_choices'] = areas.groupby('pin')['pin'].transform('size')
    return areas


def index_postal_areas(areas):
    """Group published areas by PIN for repeated context checks."""
    groups = {}
    for row in areas.to_dict(orient='records'):
        pin = pin_key(row['pin'])
        if pin:
            groups.setdefault(pin, []).append(row)
    return groups


def match_postal_area(matches, state, district):
    """Return a status and a single matching area, if the labels resolve one."""
    if not matches:
        return 'unknown', None
    stated = state_key(state)
    if stated:
        matches = [row for row in matches if state_key(row['state']) == stated]
        if not matches:
            return 'state_conflict', None
    district_label = place_key(district)
    if district_label:
        matches = [row for row in matches if place_key(row['district']) == district_label]
        if not matches:
            return 'district_conflict', None
    if len(matches) != 1:
        return 'ambiguous_pin', None
    return ('unique_pin_with_state' if stated else 'unique_pin'), matches[0]


def add_postal_context(entities, areas):
    """Expose area matches separately; the result still has one row per entity."""
    pin_groups = index_postal_areas(areas)
    context = []
    for row in entities.itertuples(index=False):
        status, match = match_postal_area(pin_groups.get(pin_key(row.pin)), row.state, row.district)
        context.append({'postal_resolution': status, 'postal_state': match['state'] if match else None,
                        'postal_district': match['district'] if match else None})
    columns = ['postal_state', 'postal_district', 'postal_resolution']
    return entities.join(pd.DataFrame(context, index=entities.index, columns=columns))


def resolve_origin(query, entities, postal_areas):
    """Support coordinates, exact saved city/district names, and postal areas."""
    text = str(query).strip()
    coordinates = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)', text)
    if coordinates:
        latitude, longitude = map(float, coordinates.groups())
        if not india_point(latitude, longitude):
            raise ValueError('Enter latitude, longitude within the India coordinate range.')
        return Origin(latitude, longitude, text, 'User-supplied coordinates')
    if not text:
        raise ValueError('Enter a city, district, PIN or latitude, longitude.')
    label, separator, state = text.partition(',')
    state_name = state_key(state) if separator else ''
    pin = pin_key(label)
    if pin:
        rows = postal_areas[postal_areas['pin'].eq(pin)]
        if state_name:
            rows = rows[rows['state'].map(state_key).eq(state_name)]
        if len(rows) != 1:
            choices = sorted({f'{r.district}, {r.state}' for r in rows.itertuples()})
            problem = f'found multiple areas: {", ".join(choices)}' if choices else 'was not found in the saved PIN directory'
            raise ValueError(f'PIN {pin} {problem}. Give a state or a coordinate pair.')
        row = rows.iloc[0]
        lat, lon = _center(rows)
        return Origin(lat, lon, f'{pin}, {row.district}, {row.state}', 'Approximate origin from post-office coordinates')

    key = place_key(label)
    if not key:
        raise ValueError('Enter a city, district, PIN or latitude, longitude.')
    # Prefer an exact city label to a district of the same name.
    for field, frame, basis in [
        ('city', entities, 'Approximate city origin from listed facility coordinates'),
        ('district', postal_areas, 'Approximate district origin from post-office coordinates'),
        ('district', entities, 'Approximate district origin from listed facility coordinates'),
    ]:
        rows = frame[frame[field].map(place_key).eq(key)]
        if state_name:
            rows = rows[rows['state'].map(state_key).eq(state_name)]
        if rows.empty:
            continue
        states = {state_key(v): v for v in rows['state'] if state_key(v)}
        districts = {place_key(v) for v in rows['district'] if place_key(v)}
        if len(states) > 1 or field == 'city' and len(districts) > 1:
            raise ValueError(f'{label.strip()} is ambiguous. Enter a state, PIN or a coordinate pair.')
        if 'location_conflict' in rows:
            rows = rows.loc[~rows['location_conflict'].eq(True)]
        if rows.empty:
            raise ValueError('The matched place has conflicting coordinates. Enter a verified coordinate pair.')
        try:
            lat, lon = _center(rows)
        except ValueError:
            continue
        return Origin(lat, lon, text, basis)
    raise ValueError(f'Could not locate {text!r} with the current inputs. Try a city with its state, a PIN, or latitude, longitude.')
