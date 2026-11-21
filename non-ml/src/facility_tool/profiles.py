"""Source coverage and contextual EDA. These counts describe records, not facilities."""

import ast
import re

import pandas as pd

from .geography import india_point, place_key, state_key


_EMPTY = {'', 'none', 'null', 'n/a', 'na', 'nan', 'unknown', 'not available', '-', '[]', '{}'}
_PHONE = re.compile(r'(?<!\w)\+?\d(?:[\d ().-]*\d)(?!\w)')


def items(value):
    if isinstance(value, (tuple, list)):
        return [item for element in value for item in items(element)]
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if text.casefold() in _EMPTY:
        return []
    if text.startswith('[') and len(text) < 10000:
        try:
            decoded = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            pass
        else:
            if isinstance(decoded, list):
                return items(decoded)
    return [text]


def plausible_phone(text):
    for candidate in _PHONE.findall(text):
        digits = ''.join(char for char in candidate if char.isdigit())
        if 7 <= len(digits) <= 15 and len(set(digits)) > 1:
            return True
    return False


def phone_coverage(records):
    """Non-empty claims and plausible formatting are separate; no number is dialed."""
    counts = []
    for source, rows in records.groupby('source', sort=True):
        listed = plausible = 0
        for value in rows['phones']:
            published = items(value)
            listed += bool(published)
            plausible += any(plausible_phone(text) for text in published)
        n = len(rows)
        counts.append({'source': source, 'source_records': n, 'with_listed_phone': listed,
                       'listed_phone_pct': 100 * listed / n, 'with_plausible_phone_format': plausible,
                       'plausible_phone_format_pct': 100 * plausible / n})
    return pd.DataFrame(counts)


def source_profile(records):
    rows = []
    for source, group in records.groupby('source', sort=True):
        location = group.apply(lambda row: india_point(row['lat'], row['lon']), axis=1)
        name = group['name'].map(place_key)
        address = group['address'].map(place_key)
        pairs = pd.DataFrame({'name': name, 'address': address}).loc[name.ne('') & address.ne('')]
        rows.append({'source': source, 'source_records': len(group), 'with_name': int(name.ne('').sum()),
                     'with_address': int(address.ne('').sum()), 'with_pin': int(group['pin'].map(bool).sum()),
                     'with_plausible_india_coordinate_pair': int(location.sum()),
                     'extra_rows_sharing_name_and_address': int(pairs.duplicated().sum()),
                     'with_source_url': int(group['source_urls'].map(bool).sum())})
    return pd.DataFrame(rows)


def field_profile(frame):
    return pd.DataFrame([{'column': col, 'non_null': int(frame[col].notna().sum()),
                          'usable_text_or_scalar': int(frame[col].map(lambda value: bool(items(value))).sum()),
                          'rows': len(frame)} for col in frame.columns])


def district_context(entities, nfhs, indicators):
    """Optional 2019–21 area context. Keep suppression and small-sample flags."""
    if nfhs.empty:
        return pd.DataFrame(), pd.DataFrame()
    for field in ('state_ut', 'district_name', *indicators):
        if field not in nfhs:
            raise ValueError(f'NFHS field {field!r} is absent. Choose available columns from the table.')
    survey = nfhs[['state_ut', 'district_name', *indicators]].copy()
    survey['state_key'] = survey['state_ut'].map(state_key)
    survey['district_key'] = survey['district_name'].map(place_key)
    survey = survey.loc[survey[['state_key', 'district_key']].ne('').all(axis=1)]
    ambiguous = survey.loc[survey.duplicated(['state_key', 'district_key'], keep=False)]
    survey = survey.loc[~survey.duplicated(['state_key', 'district_key'], keep=False)].copy()
    for field in indicators:
        text = survey[field].astype('string').str.strip()
        survey[field + '_small_sample'] = text.str.fullmatch(r'\([^()]+\)', na=False)
        survey[field + '_suppressed'] = text.eq('*').fillna(False)
        survey[field] = pd.to_numeric(text.str.strip('()').str.strip(), errors='coerce')
    selection = entities[['facility_id', 'state', 'district', 'postal_state', 'postal_district', 'postal_resolution']].copy()
    conflicted = selection['postal_resolution'].isin(['state_conflict', 'district_conflict'])
    selection['state_key'] = selection['state'].map(state_key).replace('', None).fillna(selection['postal_state'].map(state_key))
    selection['district_key'] = selection['district'].map(place_key).replace('', None).fillna(selection['postal_district'].map(place_key))
    selection.loc[conflicted, ['state_key', 'district_key']] = ''
    context = selection.merge(survey, on=['state_key', 'district_key'], how='left', validate='m:1', indicator='nfhs_join')
    return context, ambiguous
