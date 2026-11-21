"""Attach population density and NFHS indicators to the same facility geography."""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

# The notebooks run from a repository Git folder without installing this project.
_SHARED_SOURCE = Path(__file__).resolve().parents[2] / 'non-ml' / 'src'
if str(_SHARED_SOURCE) not in sys.path:
    sys.path.insert(0, str(_SHARED_SOURCE))

from facility_tool.geography import match_postal_area, pin_key, place_key, state_key


DENSITY_FEATURES = ('population_density_per_km2', 'population_density_missing')
NFHS_FEATURES = (
    'women_age_15_49_with_10_or_more_years_of_schooling_pct',
    'population_below_age_15_years_pct',
    'hh_use_improved_sanitation_pct',
    'women_age_15_49_years_whose_bmi_bmi_is_underweight_bmi_lt_1_pct',
    'hh_electricity_pct',
    'child_u5_who_are_stunted_height_for_age_18_pct',
    'households_using_clean_fuel_for_cooking_pct',
    'w20_24_married_before_age_18_years_pct',
    'female_population_age_6_years_and_above_ever_schooled_pct',
    'hh_member_covered_health_insurance_pct',
    'hh_improved_water_pct',
    'all_w15_49_who_are_anaemic_pct',
    'sex_ratio_total_f_per_1000_m',
    'households_using_iodized_salt_pct',
    'births_attended_by_skilled_hp_5y_10_pct',
)

_AREA_IN_KM2 = {'km2': 1.0, 'm2': 0.000001, 'ha': 0.01, 'mi2': 2.589988110336}
_AREA_KEYS = ('state_key', 'district_key')
_MISSING_PLACES = {'', 'null', 'none', 'nan', 'na', 'n a', 'unknown', 'not available'}


def _require_columns(frame, fields, label):
    if not isinstance(frame, pd.DataFrame) or frame.columns.has_duplicates:
        raise ValueError(f'{label} must be a DataFrame with unique column labels.')
    missing = [field for field in fields if field not in frame]
    if missing:
        raise ValueError(f'{label} is missing columns: {", ".join(map(str, missing))}.')


def _place(value, normalizer=place_key):
    if not pd.api.types.is_scalar(value):
        return ''
    key = normalizer(value)
    return '' if key in _MISSING_PLACES else key


def _area_columns(frame, state_column, district_column):
    return pd.DataFrame({
        'state_key': frame[state_column].map(lambda value: _place(value, state_key)),
        'district_key': frame[district_column].map(_place),
    }, index=frame.index)


def prepare_district_keys(
    facilities,
    postal,
    *,
    facility_pin='address_zipOrPostcode',
    facility_state='address_stateOrRegion',
    facility_district=None,
    postal_pin='pincode',
    postal_state='statename',
    postal_district='district',
):
    """Resolve at most one district and state for each input row.

    The PIN directory may list multiple offices for a key. Distinct areas stay
    separate and matching facility state or district may resolve ambiguity. An
    explicitly provided district and state may be used without a matching PIN.
    A conflict with an available PIN mapping is held for review. Use a real
    district field for `facility_district` and leave it unset for FDR's city field.
    """
    facility_fields = [field for field in (facility_pin, facility_state, facility_district) if field is not None]
    _require_columns(facilities, facility_fields, 'Facilities')
    _require_columns(postal, (postal_pin, postal_state, postal_district), 'Postal directory')
    if facility_pin is None and (facility_state is None or facility_district is None):
        raise ValueError('Supply facility PINs or both facility states and districts.')

    directory = _area_columns(postal, postal_state, postal_district)
    directory['pin_key'] = postal[postal_pin].map(pin_key)
    directory = directory.loc[directory.ne('').all(axis=1)].drop_duplicates()
    directory = directory.rename(columns={'state_key': 'state', 'district_key': 'district'})
    by_pin = {
        pin: group[['state', 'district']].to_dict(orient='records')
        for pin, group in directory.groupby('pin_key', sort=False)
    }

    def field_values(field, normalizer):
        if field is None:
            return [''] * len(facilities)
        return facilities[field].map(normalizer).to_list()

    pins = field_values(facility_pin, pin_key)
    states = field_values(facility_state, lambda value: _place(value, state_key))
    districts = field_values(facility_district, _place)
    rows = []
    for pin, state, district in zip(pins, states, districts):
        options = by_pin.get(pin, ())
        if options:
            status, area = match_postal_area(options, state, district)
            resolved = (area['state'], area['district']) if area else ('', '')
        elif state and district:
            status, resolved = 'direct_district', (state, district)
        else:
            status, resolved = ('pin_not_in_directory' if pin else 'pin_missing_or_invalid'), ('', '')
        rows.append((pin, *resolved, status))
    return pd.DataFrame(
        rows, index=facilities.index, columns=('pin_key', *_AREA_KEYS, 'postal_status'),
    )


def _numeric(values):
    text = values.astype('string').str.strip()
    # Accept conventional and Indian digit grouping without reinterpreting decimals.
    grouped = text.str.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?:\.\d+)?')
    text = text.mask(grouped.fillna(False), text.str.replace(',', '', regex=False))
    numeric = pd.to_numeric(text, errors='coerce').astype(float)
    return numeric.where(np.isfinite(numeric))


def _checked_lookup(frame, key_columns, value_columns, label):
    keep = frame.loc[frame[list(key_columns)].ne('').all(axis=1)]
    grouped = keep.groupby(list(key_columns), sort=False, dropna=False)[list(value_columns)]
    counts = grouped.nunique(dropna=True)
    conflicts = counts.loc[counts.gt(1).any(axis=1)]
    if not conflicts.empty:
        examples = ', '.join(map(str, conflicts.index[:5]))
        raise ValueError(f'{label} contains conflicting duplicate keys ({examples}). Resolve the values before joining.')
    # A missing duplicate value does not override the unique available value.
    return grouped.first()


def _join_features(facilities, lookup, keys, key_columns, value_columns, prefix, *, missing_flag=False):
    status_column = f'{prefix}_join_status'
    fields = [*value_columns, status_column]
    if missing_flag:
        fields.append(f'{prefix}_missing')
    collisions = set(fields).intersection(facilities.columns)
    if collisions:
        raise ValueError(f'Facility output columns already exist: {", ".join(sorted(collisions))}.')
    index = (
        pd.Index(keys[key_columns[0]], name=key_columns[0]) if len(key_columns) == 1
        else pd.MultiIndex.from_frame(keys[list(key_columns)])
    )
    values = lookup.reindex(index)
    found_key = index.isin(lookup.index)
    has_value = values.notna().any(axis=1).to_numpy()
    status = np.where(has_value, 'matched', np.where(found_key, 'value_missing', 'key_not_found')).astype(object)
    no_key = keys[list(key_columns)].eq('').any(axis=1).to_numpy()
    failures = keys['postal_status'].to_numpy() if len(key_columns) > 1 else 'pin_missing_or_invalid'
    status[no_key] = failures[no_key] if len(key_columns) > 1 else failures
    result = facilities.copy()
    for field in value_columns:
        result[field] = values[field].to_numpy(dtype=float)
    if missing_flag:
        result[f'{prefix}_missing'] = (~has_value).astype(np.int8)
    result[status_column] = status
    counts = result[status_column].value_counts().rename_axis('status').reset_index(name='facilities')
    counts['share'] = counts['facilities'] / len(facilities) if len(facilities) else pd.Series(dtype=float)
    return result, counts


def _aligned_keys(facilities, keys, columns):
    _require_columns(keys, columns, 'Prepared facility keys')
    if not keys.index.equals(facilities.index):
        raise ValueError('Prepared keys must retain the same index and row order as the facilities.')
    return keys


def join_population_density(
    facilities,
    density,
    *,
    keys=None,
    key='district',
    pin_column='pincode',
    state_column='state',
    district_column='district',
    density_column='population_density',
    population_column=None,
    area_column=None,
    area_unit=None,
    facility_pin='address_zipOrPostcode',
):
    """Left join people per km² and return the facilities and a status summary.

    `key` is `district` (state plus district) or `pin`. Supply either a
    `density_column` already in people/km² or `density_column=None` with a
    population column, land-area column and explicit unit (km2, m2, ha or mi2).
    Missing, negative and nonfinite values, and nonpositive areas, stay missing.
    """
    _require_columns(facilities, (), 'Facilities')
    if key == 'district':
        key_columns = _AREA_KEYS
        _require_columns(density, (state_column, district_column), 'Density source')
        reference = _area_columns(density, state_column, district_column)
        keys = _aligned_keys(facilities, keys, (*key_columns, 'postal_status'))
    elif key == 'pin':
        key_columns = ('pin_key',)
        _require_columns(density, (pin_column,), 'Density source')
        reference = pd.DataFrame({'pin_key': density[pin_column].map(pin_key)}, index=density.index)
        if keys is None:
            _require_columns(facilities, (facility_pin,), 'Facilities')
            keys = pd.DataFrame({'pin_key': facilities[facility_pin].map(pin_key)}, index=facilities.index)
        keys = _aligned_keys(facilities, keys, key_columns)
    else:
        raise ValueError("Density key must be 'district' or 'pin'.")

    if density_column is not None:
        if any(field is not None for field in (population_column, area_column, area_unit)):
            raise ValueError('Choose direct population density or population divided by land area.')
        _require_columns(density, (density_column,), 'Density source')
        values = _numeric(density[density_column])
    else:
        unit = str(area_unit or '').lower().strip().replace('²', '2').replace('^', '')
        if population_column is None or area_column is None or unit not in _AREA_IN_KM2:
            raise ValueError('Set population_column, area_column and area_unit (km2, m2, ha or mi2).')
        _require_columns(density, (population_column, area_column), 'Density source')
        population = _numeric(density[population_column])
        area = _numeric(density[area_column]) * _AREA_IN_KM2[unit]
        values = population.div(area.where(area.gt(0)))
    value_column = DENSITY_FEATURES[0]
    reference[value_column] = values.where(np.isfinite(values) & values.ge(0))
    lookup = _checked_lookup(reference, key_columns, (value_column,), 'Density source')
    return _join_features(
        facilities, lookup, keys, key_columns, (value_column,), 'population_density', missing_flag=True,
    )


def join_nfhs(facilities, nfhs, *, keys, state_column='state_ut', district_column='district_name'):
    """Left join the earlier MLP's 15 NFHS indicators on the shared district keys."""
    _require_columns(facilities, (), 'Facilities')
    _require_columns(nfhs, (state_column, district_column, *NFHS_FEATURES), 'NFHS source')
    keys = _aligned_keys(facilities, keys, (*_AREA_KEYS, 'postal_status'))
    reference = _area_columns(nfhs, state_column, district_column)
    for feature in NFHS_FEATURES:
        numeric = _numeric(nfhs[feature])
        reference[feature] = numeric.where(numeric.between(0, 100) if feature.endswith('_pct') else numeric.gt(0))
    lookup = _checked_lookup(reference, _AREA_KEYS, NFHS_FEATURES, 'NFHS source')
    return _join_features(facilities, lookup, keys, _AREA_KEYS, NFHS_FEATURES, 'nfhs')
